"""取回产物与 Range（VS-11）。

⚠️ **Range 本身是 Starlette 的 `FileResponse` 实现的**，不是我们写的。
所以这里测的是**我们的接线**：路径解析对不对、安全不安全、状态码对不对。
「框架应该支持」不能代替真测 —— 接线错了同样拿不到 206。

全部用替身，不连服务器、不占 GPU。产物目录用 `tmp_path` 现造。
"""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.comfy.client import OutputFile
from app.comfy.fake_client import FakeComfyClient
from app.comfy.workflows.h3_video import build_workflow
from app.core.config import settings
from app.core.jobs import Job, JobManager, JobState
from app.main import create_app
from app.media.probe import FakeImageProbe, ImageSize
from app.models.video_job import PromptParts, VideoJobRequest, VideoMode

# 造一段有内容的假 mp4：只要字节数够、内容可辨认就行。
# 这里不需要它是真视频 —— 真产物的编码断言由冒烟脚本在服务器上用 ffprobe 做，
# 因为开发机上没有 ffprobe（runbook §1）。
FAKE_VIDEO = bytes(range(256)) * 20  # 5120 字节

VIDEO_OUTPUT = OutputFile(
    filename="grokgen_00001_.mp4", subfolder="video", node_id="save_video"
)


def make_client(tmp_path, monkeypatch) -> tuple[TestClient, JobManager]:
    """造一个全替身的客户端，并把产物根目录指向临时目录。

    Args:
        tmp_path: pytest 的临时目录，当作 ComfyUI 的 `output/`。
        monkeypatch: 用来改配置里的输出目录。

    Returns:
        `(TestClient, JobManager)`。
    """
    monkeypatch.setattr(settings, "comfy_output_dir", tmp_path)
    comfy = FakeComfyClient(gated=True)
    manager = JobManager(comfy, poll_interval_seconds=0, preflight=False)
    app = create_app(comfy, FakeImageProbe(ImageSize(1280, 720)), manager)
    return TestClient(app), manager


def register_job(manager: JobManager, state=JobState.DONE, outputs=None) -> Job:
    """直接造一个处于指定状态的任务并登记，**不走队列**。

    ⚠️ 不用 `manager.submit()` 是有意的：那会把任务排进队列，
    后台 worker 立刻开始推进它，测试里看到的状态就不确定了
    （而且从 `running` 往回退到 `preparing` 会被状态机判成非法转移）。
    这个文件要测的是取回产物，不是状态机 —— 状态机在 `test_jobs.py` 里测。

    Args:
        manager: 任务管理器。
        state: 任务要停在哪个状态。
        outputs: 挂在任务上的产物。

    Returns:
        已登记的 `Job`。
    """
    request = VideoJobRequest(
        mode=VideoMode.T2VA,
        prompt=PromptParts(description="a cat walking on a table"),
        duration_seconds=5.0,
    )
    workflow, normalized = build_workflow(request)
    now = datetime.now(timezone.utc)
    job = Job(
        job_id="job_20260923_0001",
        request=request,
        workflow=workflow,
        normalized=normalized,
        created_at=now,
        state=state,
        outputs=list(outputs) if outputs is not None else [VIDEO_OUTPUT],
    )
    manager._jobs[job.job_id] = job
    return job


def write_video(tmp_path, subfolder="video", filename="grokgen_00001_.mp4") -> None:
    """在临时产物目录里放一个假视频文件。"""
    target = tmp_path / subfolder if subfolder else tmp_path
    target.mkdir(parents=True, exist_ok=True)
    (target / filename).write_bytes(FAKE_VIDEO)


def test_downloading_a_finished_job_returns_the_whole_file(tmp_path, monkeypatch):
    """完整下载：200、正确的 content-type、字节与磁盘上的一致。"""
    write_video(tmp_path)
    client, manager = make_client(tmp_path, monkeypatch)
    with client:
        job = register_job(manager)
        response = client.get(f"/v1/jobs/{job.job_id}/video")

    assert response.status_code == 200
    assert response.headers["content-type"] == "video/mp4"
    assert response.content == FAKE_VIDEO


def test_the_response_advertises_range_support(tmp_path, monkeypatch):
    """响应里要有 `Accept-Ranges: bytes`。

    ⚠️ 这不是装饰：播放器靠这个头决定要不要让用户拖进度条。
    没有它，很多播放器会退化成「必须下完才能播」。
    """
    write_video(tmp_path)
    client, manager = make_client(tmp_path, monkeypatch)
    with client:
        job = register_job(manager)
        response = client.get(f"/v1/jobs/{job.job_id}/video")

    assert response.headers.get("accept-ranges") == "bytes"


def test_a_range_request_returns_206_with_the_right_bytes(tmp_path, monkeypatch):
    """VS-11：Range 返回 206、`Content-Range` 正确，**且字节确实是那一段**。

    ⚠️ 三条都要断言。只看状态码的话，一个「返回 206 但把整个文件塞回去」
    或者「返回错误偏移的字节」的实现照样通过 ——
    而手机上的表现会是拖动之后播放错位，那种错很难往回查到这里。
    """
    write_video(tmp_path)
    client, manager = make_client(tmp_path, monkeypatch)
    with client:
        job = register_job(manager)
        response = client.get(
            f"/v1/jobs/{job.job_id}/video", headers={"Range": "bytes=0-1023"}
        )

    assert response.status_code == 206
    assert response.headers["content-range"] == f"bytes 0-1023/{len(FAKE_VIDEO)}"
    assert len(response.content) == 1024
    assert response.content == FAKE_VIDEO[:1024]


def test_a_mid_file_range_returns_that_exact_slice(tmp_path, monkeypatch):
    """从文件中间取一段 —— 拖进度条实际发出的就是这种请求。"""
    write_video(tmp_path)
    client, manager = make_client(tmp_path, monkeypatch)
    with client:
        job = register_job(manager)
        response = client.get(
            f"/v1/jobs/{job.job_id}/video", headers={"Range": "bytes=2000-2099"}
        )

    assert response.status_code == 206
    assert response.headers["content-range"] == f"bytes 2000-2099/{len(FAKE_VIDEO)}"
    assert response.content == FAKE_VIDEO[2000:2100]


def test_an_unfinished_job_returns_409_not_404(tmp_path, monkeypatch):
    """还没跑完返回 409，并且带上当前状态。

    ⚠️ **不能用 404。** 对 App 来说 404 是「这东西不存在，别问了」，
    409 是「再等等，还在生成」—— 混成一个，App 就没法决定要不要继续轮询。
    """
    client, manager = make_client(tmp_path, monkeypatch)
    with client:
        job = register_job(manager, state=JobState.RUNNING, outputs=[])
        response = client.get(f"/v1/jobs/{job.job_id}/video")

    assert response.status_code == 409
    assert "running" in response.json()["detail"]


def test_an_unknown_job_returns_404(tmp_path, monkeypatch):
    """任务不存在返回 404。"""
    client, _ = make_client(tmp_path, monkeypatch)
    with client:
        response = client.get("/v1/jobs/job_20260101_9999/video")

    assert response.status_code == 404


def test_a_missing_file_on_disk_returns_404_naming_the_file(tmp_path, monkeypatch):
    """产物记录在、文件不在磁盘上 —— 404 并说清是哪个文件。

    这种情况真实会发生：ComfyUI 的 `output/` 被手工清理过。
    """
    client, manager = make_client(tmp_path, monkeypatch)
    with client:
        job = register_job(
            manager,
            outputs=[OutputFile(filename="nope.mp4", subfolder="video", node_id="save_video")],
        )
        response = client.get(f"/v1/jobs/{job.job_id}/video")

    assert response.status_code == 404
    assert "nope.mp4" in response.json()["detail"]


def test_a_done_job_without_outputs_is_a_server_error(tmp_path, monkeypatch):
    """`done` 却没有产物是网关自己的状态不一致，报 500 而不是 404。

    ⚠️ 报 404 会把「网关出 bug 了」伪装成「这个任务本来就没产物」，
    而后者在别的路径上是正常情况 —— 混在一起就再也查不出来了。
    """
    client, manager = make_client(tmp_path, monkeypatch)
    with client:
        job = register_job(manager, outputs=[])
        response = client.get(f"/v1/jobs/{job.job_id}/video")

    assert response.status_code == 500


# ⚠️ 三个写法都必须**逃到同一个真实存在的诱饵文件上**。
#
# 这一点是植入缺陷时才发现的：最初写的是逃到更外层的路径，
# 去掉边界检查后**只有一个用例变红**，另外两个仍然「通过」——
# 因为它们指向的位置本来就没有文件，挡住它们的是「文件不存在」那条检查，
# 不是边界检查。那是假通过，比没有这条测试更糟。
@pytest.mark.parametrize(
    "subfolder,filename",
    [
        ("..", "secret.txt"),
        ("video/..", "../secret.txt"),
        ("", "../secret.txt"),
    ],
)
def test_a_path_escaping_the_output_dir_is_refused(
    tmp_path, monkeypatch, subfolder, filename
):
    """路径穿越必须被挡住。

    ⚠️ **`filename` 与 `subfolder` 来自 ComfyUI 的 `/history` 响应 ——
    那是外部输入，不是网关生成的。** 没有这道检查，一条构造过的记录
    就能让这个接口读出输出目录之外的任意文件，
    而网关**没有认证**，tailnet 里任何设备都能调它（契约 §1）。

    断言 404 而不是 403：403 等于告诉对方「这个路径存在但你不能看」，
    那本身就是一条信息。

    ⚠️ 逃逸目标处**真的放了一个文件** —— 否则「没读到」可能只是
    「那里本来就没东西」，测试会假通过。
    """
    outside = tmp_path.parent / "secret.txt"
    outside.write_bytes(b"THIS MUST NOT BE SERVED")

    client, manager = make_client(tmp_path, monkeypatch)
    with client:
        job = register_job(
            manager,
            outputs=[OutputFile(filename=filename, subfolder=subfolder, node_id="save_video")],
        )
        response = client.get(f"/v1/jobs/{job.job_id}/video")

    assert response.status_code == 404
    assert b"THIS MUST NOT BE SERVED" not in response.content
