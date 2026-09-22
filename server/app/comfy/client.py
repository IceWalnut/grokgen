"""ComfyUI 客户端的接口定义。

⚠️ 这是整个网关唯一的 GPU 边界（架构文档 §3）。
`core/`、`media/`、`api/` 里不允许出现任何 HTTP 调用或 ComfyUI 的地址 ——
这条约束由 `tests/test_layering.py` 强制（VS-13）。

M1R1 只需要 `system_stats`；`submit` / `history` / `upload_image` 在 M1R3 加。
"""

from dataclasses import dataclass
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


class ComfyUnreachable(Exception):
    """连不上 ComfyUI，或它的响应不是预期形状。

    ⚠️ 这个异常存在的意义是让「上游不可达」成为一个可报告的事实，
    而不是让请求挂住、或退化成一个语焉不详的 500（`AGENTS.md` §3 第 5 条）。
    调用方应当把它翻译成给用户看的状态，而不是吞掉。
    """


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

    async def aclose(self) -> None:
        """释放底层连接。应用关闭时调用。"""
        ...
