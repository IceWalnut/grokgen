"""`FfprobeImageProbe` 对 ffprobe 输出的解析。

⚠️ `probe.py` 的模块 docstring 说「开发机上没有 ffmpeg，所以 `FfprobeImageProbe`
只能在服务器上真跑」——**豁免的是「不能真跑 ffprobe」，不是「不用测」**。
这里把 `create_subprocess_exec` 换掉，喂进事先准备好的 ffprobe 输出，
测的是**它怎么解释那些输出**，那部分逻辑跟本机有没有 ffmpeg 无关。
这与 `test_comfy_client.py` 用 `httpx.MockTransport` 是同一套做法。

⚠️ 这个文件是 M2R2 为了一个实测到的 bug 才建的，见
`test_zero_size_is_rejected_even_though_ffprobe_exits_zero`。
在那之前 `FfprobeImageProbe` **一条测试都没有**。
"""

import asyncio
import json
from pathlib import Path

import pytest

from app.media.probe import FfprobeImageProbe, ImageSize, MediaProbeError


class _FakeProcess:
    """假装成 `asyncio.create_subprocess_exec` 返回的那个进程对象。"""

    def __init__(self, stdout: bytes, stderr: bytes = b"", returncode: int = 0) -> None:
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, self._stderr


def probe_seeing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    stdout: str | bytes,
    stderr: bytes = b"",
    returncode: int = 0,
) -> tuple[FfprobeImageProbe, Path]:
    """造一个「ffprobe 会输出 `stdout`」的 probe，外加一个真实存在的文件。

    Args:
        monkeypatch: pytest 内置。
        tmp_path: pytest 内置，用来放一个真文件（`image_size` 会先查文件在不在）。
        stdout: 假装 ffprobe 写到标准输出的内容。
        stderr: 假装写到标准错误的内容。**正常文件也会有警告**，见 §零尺寸那条测试。
        returncode: 假装的退出码。

    Returns:
        `(probe, path)`。
    """
    target = tmp_path / "whatever.png"
    target.write_bytes(b"not actually checked")

    payload = stdout.encode() if isinstance(stdout, str) else stdout

    async def fake_exec(*args, **kwargs):  # noqa: ANN002, ANN003
        return _FakeProcess(payload, stderr, returncode)

    # available() 查的是本机有没有 ffprobe —— 开发机上没有，但这里测的不是它。
    monkeypatch.setattr(FfprobeImageProbe, "available", staticmethod(lambda: True))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    return FfprobeImageProbe(), target


async def test_normal_output_is_parsed(monkeypatch, tmp_path):
    """一张正常图片：取第一条视频流的宽高。"""
    probe, path = probe_seeing(
        monkeypatch,
        tmp_path,
        stdout=json.dumps({"streams": [{"width": 1472, "height": 832}]}),
    )

    assert await probe.image_size(path) == ImageSize(width=1472, height=832)


async def test_zero_size_is_rejected_even_though_ffprobe_exits_zero(monkeypatch, tmp_path):
    """⭐ 本文件存在的理由。

    **ffprobe 对一个坏文件退出码是 0**：错误只写进 stderr，
    而 streams 里照样给出 `{"width": 0, "height": 0}`。
    下面这段 stdout/stderr 是 2026-09-24 在服务器上对一个「内容是纯文本的 .png」
    真实抓到的。

    原来的守卫只挡「宽高**缺失**」（KeyError），挡不住「宽高是 **0**」，
    于是上传接口返回 **200 加一个 0x0 的 asset** —— 看起来完全正常。

    ⚠️ **真正的失败被推迟了，而且归错了因**：按图片比例推画布的那一行是
    `math.sqrt(budget / (width * height))` —— **除以零**。
    用户会在**提交生成任务**时看到一个未处理的异常，
    而不是在上传时看到「这不是一张图片」。
    """
    probe, path = probe_seeing(
        monkeypatch,
        tmp_path,
        stdout=json.dumps({"programs": [], "streams": [{"width": 0, "height": 0}]}),
        stderr=b"[png @ 0x6449388e7ec0] Invalid PNG signature 0x7468697320697320.\n",
        returncode=0,
    )

    with pytest.raises(MediaProbeError, match="0x0"):
        await probe.image_size(path)


@pytest.mark.parametrize(
    "width,height",
    [(0, 832), (1472, 0), (-1, 832), (1472, -1)],
)
async def test_any_non_positive_dimension_is_rejected(monkeypatch, tmp_path, width, height):
    """零和负数都不行，**两条边各测一次**。

    只测 `0x0` 的话，「只有一边是 0」会漏过去 —— 而那同样会让
    `width * height == 0`，同样除以零。
    """
    probe, path = probe_seeing(
        monkeypatch,
        tmp_path,
        stdout=json.dumps({"streams": [{"width": width, "height": height}]}),
    )

    with pytest.raises(MediaProbeError):
        await probe.image_size(path)


async def test_stderr_noise_alone_does_not_fail_a_good_image(monkeypatch, tmp_path):
    """⚠️ 这条是上一条的**对照**，防止修法修过头。

    ffprobe 对完全正常的文件也会往 stderr 写警告。
    如果把判据写成「stderr 非空就算失败」，**好图片会被误判成坏的** ——
    所以判据必须落在**尺寸这个结果**上，不落在噪声上。
    """
    probe, path = probe_seeing(
        monkeypatch,
        tmp_path,
        stdout=json.dumps({"streams": [{"width": 736, "height": 416}]}),
        stderr=b"[swscaler @ 0x55] deprecated pixel format used, make sure you did set range correctly\n",
        returncode=0,
    )

    assert await probe.image_size(path) == ImageSize(width=736, height=416)


async def test_missing_dimensions_still_rejected(monkeypatch, tmp_path):
    """宽高**缺失**那条老路径没被改坏。"""
    probe, path = probe_seeing(
        monkeypatch, tmp_path, stdout=json.dumps({"streams": [{}]})
    )

    with pytest.raises(MediaProbeError, match="没有给出"):
        await probe.image_size(path)


async def test_no_streams_at_all_is_rejected(monkeypatch, tmp_path):
    """连一条流都没有 —— 比如上传了一个 zip。"""
    probe, path = probe_seeing(monkeypatch, tmp_path, stdout=json.dumps({"streams": []}))

    with pytest.raises(MediaProbeError, match="没有给出"):
        await probe.image_size(path)


async def test_non_zero_returncode_is_rejected_with_stderr(monkeypatch, tmp_path):
    """ffprobe 真的失败时，把 stderr 带进错误信息 —— 那是唯一的线索。"""
    probe, path = probe_seeing(
        monkeypatch,
        tmp_path,
        stdout=b"",
        stderr=b"No such file or directory",
        returncode=1,
    )

    with pytest.raises(MediaProbeError, match="No such file"):
        await probe.image_size(path)


async def test_missing_file_is_rejected_before_running_ffprobe(monkeypatch, tmp_path):
    """文件不在就别启进程了，而且错误要说清是哪个路径。"""
    monkeypatch.setattr(FfprobeImageProbe, "available", staticmethod(lambda: True))
    probe = FfprobeImageProbe()

    with pytest.raises(MediaProbeError, match="文件不存在"):
        await probe.image_size(tmp_path / "nope.png")
