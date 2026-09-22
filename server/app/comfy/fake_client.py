"""测试用的 `ComfyClient` 替身。

开发机上没有 GPU、也连不到 ComfyUI，网关除 `http_client` 之外的全部逻辑都靠它来测。
这是架构文档 §3 那条分层约束能成立的前提。
"""

from app.comfy.client import ComfyUnreachable, GpuStats

# 取自 2026-09-22 服务器实测读数，便于与真实响应对照。
DEFAULT_STATS = GpuStats(
    name="cuda:0 NVIDIA GeForce RTX 4080 SUPER : cudaMallocAsync",
    vram_total_bytes=17170956288,
    vram_free_bytes=15647768576,
)


class FakeComfyClient:
    """可控的 ComfyUI 替身：要么返回固定读数，要么按要求报不可达。"""

    def __init__(
        self,
        stats: GpuStats | None = None,
        unreachable_reason: str | None = None,
    ) -> None:
        """构造替身。

        Args:
            stats: 要返回的读数。默认 `DEFAULT_STATS`。
            unreachable_reason: 不为 `None` 时，`system_stats` 抛
                `ComfyUnreachable` 并带上这段文字。用来测「上游挂了」这条路径。
        """
        self._stats = stats or DEFAULT_STATS
        self._unreachable_reason = unreachable_reason

    async def aclose(self) -> None:
        """替身没有连接要关，空实现。"""

    async def system_stats(self) -> GpuStats:
        """返回预置读数，或按构造参数抛不可达。

        Returns:
            构造时给定的 `GpuStats`。

        Raises:
            ComfyUnreachable: 构造时给了 `unreachable_reason`。
        """
        if self._unreachable_reason is not None:
            raise ComfyUnreachable(self._unreachable_reason)
        return self._stats
