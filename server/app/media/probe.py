"""读媒体文件的尺寸等元信息。

**为什么要读图片尺寸**：首帧图被拉伸变形是这条链路最容易出的问题 ——
原生 `MiniMaxH3ImageToVideo` 对 `first_frame` 用的是 `crop="disabled"`（直接拉伸）。
正解是让画布去适配图片，而那要先知道图片有多大，
而 ComfyUI 的 `POST /upload/image` 响应里**没有** width/height。

**为什么用 ffprobe 而不是 Pillow**：服务器上本来就有 ffprobe，
M3 做缩略图和读视频时长也要用它，不必为此多引一个 Python 依赖。

⚠️ 代价要认：**开发机上没有 `ffmpeg`**（runbook §1），
所以 `FfprobeImageProbe` 只能在服务器上真跑。其余逻辑靠 `FakeImageProbe` 在开发机上测 ——
这与 `ComfyClient` 是同一套做法（架构文档 §3）。
"""

import asyncio
import json
import shutil
from pathlib import Path
from typing import NamedTuple, Protocol

FFPROBE = "ffprobe"
PROBE_TIMEOUT_SECONDS = 10.0


class ImageSize(NamedTuple):
    """图片的像素尺寸。

    Attributes:
        width: 宽，像素。
        height: 高，像素。
    """

    width: int
    height: int


class MediaProbeError(Exception):
    """读不出媒体信息。

    ⚠️ 读不出来要明确报错，不要回退到一个猜测的尺寸 ——
    猜错的后果是画面变形，而那不会以任何形式报错（`AGENTS.md` §3 第 5 条）。
    """


class ImageProbe(Protocol):
    """读图片尺寸的接口。"""

    async def image_size(self, path: Path) -> ImageSize:
        """读一张图片的像素尺寸。

        Args:
            path: 图片的绝对路径。

        Returns:
            `ImageSize`。

        Raises:
            MediaProbeError: 文件不存在、不是图片，或探测工具不可用。
        """
        ...


class FfprobeImageProbe:
    """调 `ffprobe` 读尺寸的真实实现。"""

    @staticmethod
    def available() -> bool:
        """当前环境有没有 ffprobe。

        启动自检用：缺了要在日志里明说，而不是等第一次上传才失败。

        Returns:
            找得到可执行文件则 `True`。
        """
        return shutil.which(FFPROBE) is not None

    async def image_size(self, path: Path) -> ImageSize:
        """用 ffprobe 读第一条视频流的宽高。

        图片在 ffprobe 眼里也是「一帧的视频流」，所以取 `v:0` 即可。

        Args:
            path: 图片的绝对路径。

        Returns:
            `ImageSize`。

        Raises:
            MediaProbeError: 文件不存在、ffprobe 不可用、超时、返回非零，
                或输出里没有宽高。
        """
        if not path.is_file():
            raise MediaProbeError(f"文件不存在: {path}")
        if not self.available():
            raise MediaProbeError(f"找不到 {FFPROBE}，无法读取图片尺寸")

        args = [
            FFPROBE, "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "json",
            str(path),
        ]
        try:
            process = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=PROBE_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError as exc:
            raise MediaProbeError(f"{FFPROBE} 读取 {path.name} 超时") from exc
        except OSError as exc:
            raise MediaProbeError(f"{FFPROBE} 启动失败: {exc!r}") from exc

        if process.returncode != 0:
            raise MediaProbeError(
                f"{FFPROBE} 读取 {path.name} 失败: {stderr.decode(errors='replace')[:200]}"
            )

        try:
            streams = json.loads(stdout).get("streams", [])
            stream = streams[0]
            width, height = int(stream["width"]), int(stream["height"])
        except (ValueError, IndexError, KeyError, TypeError) as exc:
            raise MediaProbeError(
                f"{FFPROBE} 没有给出 {path.name} 的宽高，它可能不是图片"
            ) from exc

        # ⚠️ 上面那个 except 挡的是「宽高**缺失**」，挡不住「宽高是 **0**」。
        #
        # 因为 ffprobe 对一个坏文件**退出码是 0**：它把错误写进 stderr，
        # 而 streams 里照样给出 `{"width": 0, "height": 0}`。实测（2026-09-24）：
        #
        #     $ ffprobe -v error -select_streams v:0 \
        #           -show_entries stream=width,height -of json <内容是文本的 .png>
        #     {"streams":[{"width":0,"height":0}]}
        #     [png @ ...] Invalid PNG signature 0x7468697320697320.
        #     exit=0
        #
        # 于是上传接口会返回 **200 加一个 0x0 的 asset** —— 看起来完全正常。
        # 真正的失败被推迟到提交生成任务那一刻，而且归错了因：
        # 按图片比例推画布的那一行是 `math.sqrt(budget / (width * height))`，
        # **除以零**，报出来是一个未处理的异常，不是「这不是一张图片」。
        #
        # ⚠️ **不要改成「stderr 非空就算失败」** —— ffprobe 对正常文件也会往
        # stderr 写警告，那样会把好图片误判成坏的。
        # **判据落在尺寸这个结果上，不落在噪声上。**
        if width <= 0 or height <= 0:
            raise MediaProbeError(
                f"{FFPROBE} 给 {path.name} 报的尺寸是 {width}x{height}，"
                "它不是一张有效图片"
            )

        return ImageSize(width=width, height=height)


class FakeImageProbe:
    """测试替身：返回预置尺寸，或按要求报错。"""

    def __init__(self, size: ImageSize | None = None, error: str | None = None) -> None:
        """构造替身。

        Args:
            size: 要返回的尺寸，默认 768x768（服务器上 `example.png` 的真实尺寸）。
            error: 不为 `None` 时抛 `MediaProbeError` 并带上这段文字。
        """
        self._size = size or ImageSize(768, 768)
        self._error = error
        self.probed: list[Path] = []

    async def image_size(self, path: Path) -> ImageSize:
        """返回预置尺寸。

        Args:
            path: 会被记进 `self.probed` 供断言。

        Returns:
            构造时给定的 `ImageSize`。

        Raises:
            MediaProbeError: 构造时给了 `error`。
        """
        if self._error is not None:
            raise MediaProbeError(self._error)
        self.probed.append(path)
        return self._size
