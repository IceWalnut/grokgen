"""上传端点的判据（VS-15）。

全部用替身：`FakeComfyClient` 顶掉 ComfyUI，`FakeImageProbe` 顶掉 ffprobe
（开发机上没有 ffmpeg）。
"""

import pytest
from fastapi.testclient import TestClient

from app.comfy.fake_client import FakeComfyClient
from app.main import create_app
from app.media.probe import FakeImageProbe, ImageSize

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"x" * 64


def make_client(comfy=None, probe=None) -> tuple[TestClient, FakeComfyClient, FakeImageProbe]:
    """造一个全替身的测试客户端。"""
    comfy = comfy or FakeComfyClient()
    probe = probe or FakeImageProbe(ImageSize(768, 768))
    return TestClient(create_app(comfy, probe)), comfy, probe


def test_upload_returns_path_style_asset_id_under_the_configured_subfolder():
    """asset_id 是「子目录/文件名」，而且子目录是配置里那个。

    ⚠️ 正向断言不能只看「有返回值」：`asset_id` 会被原样填进
    `LoadImage.image`，前缀错了就引用不到文件。
    """
    client, comfy, _ = make_client()
    with client:
        response = client.post(
            "/v1/uploads/image", files={"file": ("photo.png", PNG_BYTES, "image/png")}
        )

    assert response.status_code == 200
    body = response.json()
    assert body["asset_id"].startswith("grokgen/")
    assert body["asset_id"].endswith(".png")
    assert body["width"] == 768 and body["height"] == 768

    # 传给 ComfyUI 的子目录也要对
    assert comfy.uploads[0][1] == "grokgen"


def test_upload_uses_the_name_comfyui_returned_not_the_one_we_sent():
    """⚠️ 本轮最容易错的一处。

    ComfyUI 遇到重名且内容不同时会把文件改成 `name (1).ext`。
    用我们自己发过去的名字，workflow 就会引用到**另一张图**，
    而且不会有任何报错 —— 画面里出现的是别人的首帧。
    """
    comfy = FakeComfyClient(rename_upload_to="renamed (1).png")
    client, _, _ = make_client(comfy=comfy)
    with client:
        response = client.post(
            "/v1/uploads/image", files={"file": ("photo.png", PNG_BYTES, "image/png")}
        )

    assert response.json()["asset_id"] == "grokgen/renamed (1).png"


def test_probe_reads_the_stored_path_not_the_uploaded_name():
    """尺寸要从 ComfyUI 实际落盘的位置读。"""
    comfy = FakeComfyClient(rename_upload_to="renamed (1).png")
    probe = FakeImageProbe(ImageSize(1024, 576))
    client, _, _ = make_client(comfy=comfy, probe=probe)
    with client:
        client.post("/v1/uploads/image", files={"file": ("p.png", PNG_BYTES, "image/png")})

    probed = probe.probed[0]
    assert probed.name == "renamed (1).png"
    assert probed.parent.name == "grokgen"


@pytest.mark.parametrize("filename", ["evil.exe", "noext", "doc.pdf"])
def test_unsupported_extension_is_rejected(filename):
    client, _, _ = make_client()
    with client:
        response = client.post(
            "/v1/uploads/image", files={"file": (filename, PNG_BYTES, "application/octet-stream")}
        )
    assert response.status_code == 415


def test_empty_upload_is_rejected():
    client, _, _ = make_client()
    with client:
        response = client.post(
            "/v1/uploads/image", files={"file": ("a.png", b"", "image/png")}
        )
    assert response.status_code == 400


def test_comfy_unreachable_becomes_502():
    """上游挂了要说清是上游，不要变成 500。"""
    client, _, _ = make_client(comfy=FakeComfyClient(unreachable_reason="connection refused"))
    with client:
        response = client.post(
            "/v1/uploads/image", files={"file": ("a.png", PNG_BYTES, "image/png")}
        )
    assert response.status_code == 502
    assert "connection refused" in response.json()["detail"]


def test_probe_failure_is_surfaced_not_defaulted():
    """⚠️ 读不出尺寸要明确失败，不能回退到一个猜测的尺寸。

    猜错的后果是画面被拉伸变形，而那**不会以任何形式报错**。
    """
    probe = FakeImageProbe(error="ffprobe 没有给出宽高")
    client, _, _ = make_client(probe=probe)
    with client:
        response = client.post(
            "/v1/uploads/image", files={"file": ("a.png", PNG_BYTES, "image/png")}
        )
    assert response.status_code == 500
    assert "宽高" in response.json()["detail"]


def test_generated_filename_is_safe_and_unique():
    """文件名会被 ComfyUI 直接拼进路径，所以只放行安全字符。"""
    client, comfy, _ = make_client()
    with client:
        for _ in range(2):
            client.post(
                "/v1/uploads/image",
                files={"file": ("../../etc/passwd.png", PNG_BYTES, "image/png")},
            )

    names = [name for name, _ in comfy.uploads]
    assert all(".." not in n and "/" not in n for n in names)
    assert names[0] != names[1], "同名上传两次应当得到不同的文件名"
