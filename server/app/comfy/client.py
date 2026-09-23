"""ComfyUI 客户端的接口定义。

⚠️ 这是整个网关唯一的 GPU 边界（架构文档 §3）。
`core/`、`media/`、`api/` 里不允许出现任何 HTTP 调用或 ComfyUI 的地址 ——
这条约束由 `tests/test_layering.py` 强制（VS-13）。

M1R3 加了 `submit` / `history` / `object_info`，M1R4 加了 `upload_image`，
M1R5 加了 `queue` / `interrupt` / `delete_queued`（任务状态机与取消要用）；
`free` / `events` 在 M4（显存切换）与 M2（WebSocket 进度）再加。
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
class UploadedImage:
    """`POST /upload/image` 的结果。

    Attributes:
        name: **ComfyUI 最终采用的文件名**。
            ⚠️ 它不一定等于上传时给的名字 —— 重名且内容不同时，
            ComfyUI 会改成 `name (1).ext`。必须用这个返回值。
        subfolder: 落在 `input/` 下的哪个子目录。
        size_bytes: 文件大小；ComfyUI 没给时为 `None`。
    """

    name: str
    subfolder: str
    size_bytes: int | None = None

    @property
    def reference(self) -> str:
        """workflow 里 `LoadImage.image` 该填的值（相对 `input/` 的路径）。"""
        return f"{self.subfolder}/{self.name}" if self.subfolder else self.name


@dataclass(frozen=True)
class QueueState:
    """ComfyUI 队列的一张快照。

    ⚠️ **这是 M1 里区分「已提交」与「正在跑」的唯一办法。**
    `/history/{prompt_id}` 只在任务**结束**后才有记录，在排队和执行期间
    一律返回 `None` —— 光靠它，`running` 这个状态任何输入都构造不出来。

    Attributes:
        running: 正在执行的 `prompt_id`。ComfyUI 一次只跑一个，
            但仍用元组，免得将来它改了行为时这里要改类型。
        pending: 已排队、还没开始的 `prompt_id`，按队列顺序。
    """

    running: tuple[str, ...]
    pending: tuple[str, ...]


#: ComfyUI 的 `status_str` 里表示「这次执行已经结束」的取值。
#:
#: M1R7 实测到的只有这两个：成功是 `success`，报错或被中断都是 `error`。
#: 用白名单而不是「不等于某个值」，是因为将来若出现一个表示「进行中」的新取值，
#: 白名单会保守地继续等待，而黑名单会把它误判成已结束。
TERMINAL_COMFY_STATUSES = frozenset({"success", "error"})


@dataclass(frozen=True)
class JobRecord:
    """`/history/{prompt_id}` 里一条任务记录。

    Attributes:
        prompt_id: ComfyUI 给的任务 id。
        status: ComfyUI 的 `status_str`。成功是 `"success"`，
            报错和被中断都是 `"error"`。
        completed: ComfyUI 的 `completed` 字段**原样**。

            ⚠️ **它的含义是「成功完成」，不是「结束了」。**
            被中断或报错的任务是 `status_str="error"` 且 `completed=False`。
            M1R7 实测过一条被中断的记录，三个字段是：
            `status_str="error"`、`completed=False`、
            messages 里有 `execution_interrupted`。

            ⇒ **判断「这次执行结束了没有」要用 `finished`，不要用这个字段。**
            用错的后果是失败和取消都永远等不到终态 —— 而任务超时默认是关的，
            所以是真的无限轮询下去。这个 bug 在 M1R7 冒烟时被抓到过一次。
        outputs: 产出的文件。
        messages: ComfyUI 的执行消息原文，失败时的线索都在这里。
    """

    prompt_id: str
    status: str
    completed: bool
    outputs: list[OutputFile] = field(default_factory=list)
    messages: list = field(default_factory=list)

    @property
    def finished(self) -> bool:
        """这次执行是否已经结束 —— 成功、报错、被中断都算。

        Returns:
            `status` 落在 `TERMINAL_COMFY_STATUSES` 里就是 `True`。
        """
        return self.status in TERMINAL_COMFY_STATUSES


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

    async def upload_image(
        self, data: bytes, filename: str, subfolder: str
    ) -> UploadedImage:
        """把一张图传到 ComfyUI 的 `input/` 下。

        Args:
            data: 图片字节。
            filename: 期望的文件名。**ComfyUI 可能改名**，以返回值为准。
            subfolder: `input/` 下的子目录，空字符串表示顶层。

        Returns:
            `UploadedImage`，用它的 `reference` 填进 workflow。

        Raises:
            ComfyUnreachable: 上传失败。
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

    async def queue(self) -> QueueState:
        """读 ComfyUI 的队列快照。

        ⚠️ **这个接口不占 GPU**，可以放心轮询。它回答两个问题：
        「我提交的那个任务开始跑了吗」（`submitted` → `running`），
        以及「现在正在跑的是不是我要取消的那个」（见 `interrupt`）。

        Returns:
            `QueueState`。

        Raises:
            ComfyUnreachable: ComfyUI 不可达。
        """
        ...

    async def interrupt(self) -> None:
        """中断 ComfyUI **当前正在执行**的那个任务。

        ⚠️ **这个接口没有参数，按 prompt_id 取消是做不到的。**
        它打掉的是此刻正在跑的那一个，不管那是谁提交的。
        而这台服务器上 ComfyUI 仍监听 `0.0.0.0`，用户自己也在用它的网页界面 ——
        所以调用方**必须先用 `queue()` 确认正在跑的确实是自己要取消的那个**，
        否则会打断用户手工提交的生成。

        Raises:
            ComfyUnreachable: ComfyUI 不可达。
        """
        ...

    async def delete_queued(self, prompt_id: str) -> None:
        """把一个**还没开始执行**的任务从 ComfyUI 的队列里删掉。

        ⚠️ 它只对排队中的任务有效，对正在跑的那个无效（那个要用 `interrupt`）。

        这条路径存在的理由：网关自己的队列容量是 1，本来不会让两个任务同时排在
        ComfyUI 里。但 ComfyUI 的网页界面也在用同一个队列，所以「我的任务排在
        别人后面还没开始」这个状态是真实可达的。没有这个接口，那种情况下取消就只剩
        「打断别人」和「什么都不做、让它跑成孤儿产物」两个都不对的选项。

        Args:
            prompt_id: 要删掉的任务 id。删一个不存在的 id 不算错误。

        Raises:
            ComfyUnreachable: ComfyUI 不可达。
        """
        ...

    async def aclose(self) -> None:
        """释放底层连接。应用关闭时调用。"""
        ...
