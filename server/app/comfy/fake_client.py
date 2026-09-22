"""测试用的 `ComfyClient` 替身。

开发机上没有 GPU、也连不到 ComfyUI，网关除 `http_client` 之外的全部逻辑都靠它来测。
这是架构文档 §3 那条分层约束能成立的前提。
"""

from app.comfy.client import (
    ComfyUnreachable,
    ComfyValidationError,
    GpuStats,
    JobRecord,
    OutputFile,
    UploadedImage,
)

# 取自 2026-09-22 服务器实测读数，便于与真实响应对照。
DEFAULT_STATS = GpuStats(
    name="cuda:0 NVIDIA GeForce RTX 4080 SUPER : cudaMallocAsync",
    vram_total_bytes=17170956288,
    vram_free_bytes=15647768576,
)


class FakeComfyClient:
    """可控的 ComfyUI 替身。

    能模拟四种上游行为：正常、不可达、拒绝 workflow、任务还在排队。
    """

    def __init__(
        self,
        stats: GpuStats | None = None,
        unreachable_reason: str | None = None,
        object_info: dict | None = None,
        reject_with: dict | None = None,
        record: JobRecord | None = None,
        rename_upload_to: str | None = None,
    ) -> None:
        """构造替身。

        Args:
            stats: `system_stats` 要返回的读数。默认 `DEFAULT_STATS`。
            unreachable_reason: 不为 `None` 时所有调用都抛 `ComfyUnreachable`。
            object_info: `object_info` 要返回的节点定义。
            reject_with: 不为 `None` 时 `submit` 抛 `ComfyValidationError`，
                并把这个 dict 当作 `node_errors`。用来测「失败原因要带回来」。
            record: `history` 要返回的记录。`None` 表示任务还在排队。
            rename_upload_to: 不为 `None` 时，`upload_image` 返回这个名字而不是
                传进去的那个 —— 用来模拟 ComfyUI 的重名改名行为。
        """
        self._stats = stats or DEFAULT_STATS
        self._unreachable_reason = unreachable_reason
        self._object_info = object_info or {}
        self._reject_with = reject_with
        self._record = record
        self._rename_upload_to = rename_upload_to
        self.submitted: list[dict] = []
        self.uploads: list[tuple[str, str]] = []

    def _guard(self) -> None:
        """不可达模式下统一抛异常。"""
        if self._unreachable_reason is not None:
            raise ComfyUnreachable(self._unreachable_reason)

    async def aclose(self) -> None:
        """替身没有连接要关，空实现。"""

    async def system_stats(self) -> GpuStats:
        """返回预置读数，或按构造参数抛不可达。

        Returns:
            构造时给定的 `GpuStats`。

        Raises:
            ComfyUnreachable: 构造时给了 `unreachable_reason`。
        """
        self._guard()
        return self._stats

    async def object_info(self) -> dict:
        """返回预置的节点定义。

        Returns:
            构造时给定的 `object_info`。

        Raises:
            ComfyUnreachable: 构造时给了 `unreachable_reason`。
        """
        self._guard()
        return self._object_info

    async def submit(self, workflow: dict, client_id: str) -> str:
        """记下提交的图并返回一个固定 id，或按构造参数拒绝。

        Args:
            workflow: 节点图，会被存进 `self.submitted` 供断言。
            client_id: 忽略。

        Returns:
            固定的 `prompt_id`。

        Raises:
            ComfyValidationError: 构造时给了 `reject_with`。
            ComfyUnreachable: 构造时给了 `unreachable_reason`。
        """
        self._guard()
        if self._reject_with is not None:
            raise ComfyValidationError("Prompt outputs failed validation", self._reject_with)
        self.submitted.append(workflow)
        return "fake-prompt-id"

    async def upload_image(
        self, data: bytes, filename: str, subfolder: str
    ) -> UploadedImage:
        """记下上传参数并返回结果。

        Args:
            data: 忽略。
            filename: 期望文件名，会被记进 `self.uploads` 供断言。
            subfolder: 子目录，同上。

        Returns:
            `UploadedImage`；构造时给了 `rename_upload_to` 就用那个名字。

        Raises:
            ComfyUnreachable: 构造时给了 `unreachable_reason`。
        """
        self._guard()
        self.uploads.append((filename, subfolder))
        return UploadedImage(
            name=self._rename_upload_to or filename,
            subfolder=subfolder,
            size_bytes=len(data),
        )

    async def history(self, prompt_id: str) -> JobRecord | None:
        """返回预置的任务记录。

        Args:
            prompt_id: 忽略，替身只有一条记录。

        Returns:
            构造时给定的 `JobRecord`；给的是 `None` 则表示还在排队。

        Raises:
            ComfyUnreachable: 构造时给了 `unreachable_reason`。
        """
        self._guard()
        return self._record


def finished_record(filename: str = "grokgen_00001_.mp4") -> JobRecord:
    """造一条「已成功」的任务记录，省得每个测试自己拼。

    Args:
        filename: 产出的文件名。

    Returns:
        一条 `status="success"` 且带一个视频输出的 `JobRecord`。
    """
    return JobRecord(
        prompt_id="fake-prompt-id",
        status="success",
        completed=True,
        outputs=[OutputFile(filename=filename, subfolder="video", node_id="save_video")],
    )
