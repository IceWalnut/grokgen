"""ComfyUI 客户端的接口定义。

⚠️ 这是整个网关唯一的 GPU 边界（架构文档 §3）。
`core/`、`media/`、`api/` 里不允许出现任何 HTTP 调用或 ComfyUI 的地址 ——
这条约束由 `tests/test_layering.py` 强制（VS-13）。

M1R3 加了 `submit` / `history` / `object_info`；
`upload_image` 在 M1R4，`interrupt` / `free` / `events` 在 M1R5 之后。
"""

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class GpuStats:
    """ComfyUI 报告的 GPU 读数。

    Attributes:
        name: 设备名，形如 `cuda:0 NVIDIA GeForce RTX 4080 SUPER : cudaMallocAsync`。
        vram_total_bytes: 显存总量，字节。这台机器实测约 17.17e9（16 GiB）。
        vram_free_bytes: 当前空闲显存，字节。它是 GPU 状态机判断能否切换服务的依据。
    """

    name: str
    vram_total_bytes: int
    vram_free_bytes: int


@dataclass(frozen=True)
class OutputFile:
    """一次任务产出的一个文件。

    Attributes:
        filename: 文件名，例如 `grokgen_00001_.mp4`。
        subfolder: 相对 ComfyUI `output/` 的子目录，可能为空字符串。
        node_id: 产出它的节点 id，用来区分同一次任务的多个输出。
    """

    filename: str
    subfolder: str
    node_id: str


@dataclass(frozen=True)
class JobRecord:
    """`/history/{prompt_id}` 里一条任务记录。

    Attributes:
        prompt_id: ComfyUI 给的任务 id。
        status: ComfyUI 的 `status_str`，成功是 `"success"`。
        completed: 是否已经结束（成功或失败都算结束）。
        outputs: 产出的文件。
        messages: ComfyUI 的执行消息原文，失败时的线索都在这里。
    """

    prompt_id: str
    status: str
    completed: bool
    outputs: list[OutputFile] = field(default_factory=list)
    messages: list = field(default_factory=list)


class ComfyUnreachable(Exception):
    """连不上 ComfyUI，或它的响应不是预期形状。

    ⚠️ 这个异常存在的意义是让「上游不可达」成为一个可报告的事实，
    而不是让请求挂住、或退化成一个语焉不详的 500（`AGENTS.md` §3 第 5 条）。
    调用方应当把它翻译成给用户看的状态，而不是吞掉。
    """


class ComfyValidationError(Exception):
    """ComfyUI 拒绝了这张 workflow。

    ⚠️ **`node_errors` 必须原样带着**，不要压成一句「生成失败」。
    它指明了是哪个节点的哪个字段有问题，排障时那是唯一有用的信息
    （契约文档 §2 的 `failure_reason.detail`）。

    Attributes:
        message: ComfyUI 给的概述。
        node_errors: 逐节点的错误详情，原样保留。
    """

    def __init__(self, message: str, node_errors: dict):
        super().__init__(message)
        self.message = message
        self.node_errors = node_errors


class ComfyClient(Protocol):
    """网关与 ComfyUI 之间的全部交互。

    实现有两个：`http_client.HttpComfyClient`（真实）与
    `fake_client.FakeComfyClient`（测试替身）。
    开发机上没有 GPU，除 `http_client` 外的所有逻辑都靠替身来测。
    """

    async def system_stats(self) -> GpuStats:
        """读取 GPU 状态。

        Returns:
            第一块 GPU 的 `GpuStats`。

        Raises:
            ComfyUnreachable: ComfyUI 不可达，或响应里没有可用的设备。
        """
        ...

    async def object_info(self) -> dict:
        """拉取全部节点定义。

        ⚠️ 这个接口是**提交之前对图做预检**用的（见 `workflows.validation`）。
        它不占 GPU，但能抓住「节点名写错、漏了必填输入、COMBO 值不在候选里」
        这一整类错误 —— 而那类错误如果等到提交才发现，要先付一次模型加载的代价。

        Returns:
            `{节点名: 节点定义}`，形状与 ComfyUI 的 `/object_info` 一致。

        Raises:
            ComfyUnreachable: ComfyUI 不可达。
        """
        ...

    async def submit(self, workflow: dict, client_id: str) -> str:
        """提交一张 workflow。

        Args:
            workflow: API 格式的节点图。
            client_id: 用来关联 WebSocket 进度事件的标识。

        Returns:
            ComfyUI 给的 `prompt_id`。

        Raises:
            ComfyValidationError: 图没通过 ComfyUI 的校验，带 `node_errors`。
            ComfyUnreachable: ComfyUI 不可达。
        """
        ...

    async def history(self, prompt_id: str) -> JobRecord | None:
        """查一次任务的结果。

        Args:
            prompt_id: `submit` 返回的 id。

        Returns:
            `JobRecord`；ComfyUI 还没有这条记录时返回 `None`
            （任务还在排队，不是错误）。

        Raises:
            ComfyUnreachable: ComfyUI 不可达。
        """
        ...

    async def aclose(self) -> None:
        """释放底层连接。应用关闭时调用。"""
        ...
