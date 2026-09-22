"""`ComfyClient` 的真实实现，通过 HTTP 与 ComfyUI 通信。

⚠️ 这是网关里唯一允许说 HTTP 的模块。别处出现 `httpx` 就是分层破了（VS-13）。
"""

import httpx

from app.comfy.client import ComfyUnreachable, GpuStats
from app.core.config import settings


class HttpComfyClient:
    """走 HTTP 的 ComfyUI 客户端。

    网关与 ComfyUI 跑在同一台机器上，所以默认打回环地址 `127.0.0.1:8188`。
    """

    def __init__(self, base_url: str | None = None, timeout: float | None = None) -> None:
        """建立到 ComfyUI 的长连接客户端。

        Args:
            base_url: ComfyUI 的地址。默认取配置里的 `comfy_base_url`。
            timeout: 单次请求超时，秒。默认取配置里的 `comfy_timeout_seconds`（2 秒）。
                这个值偏小是故意的：health 检查不该把请求挂住。
        """
        self._client = httpx.AsyncClient(
            base_url=base_url or settings.comfy_base_url,
            timeout=timeout or settings.comfy_timeout_seconds,
            # ⚠️ trust_env=False 不能去掉。
            #
            # httpx 默认读 HTTP_PROXY / HTTPS_PROXY，而服务器的 WSL 里那两个变量
            # 指向 127.0.0.1:7890 —— 那个端口上没有任何进程在监听（runbook §6.3）。
            # 服务器的 no_proxy 恰好包含 127.0.0.1，所以不加这行「碰巧」也能工作，
            # 但网关只跟本机和 tailnet 说话，永远不需要代理，
            # 没有理由把正确性寄托在一个碰巧正确的环境变量上。
            trust_env=False,
        )

    async def aclose(self) -> None:
        """关闭底层连接池。由 FastAPI 的 lifespan 在应用退出时调用。"""
        await self._client.aclose()

    async def system_stats(self) -> GpuStats:
        """读 ComfyUI 的 `/system_stats`，取第一块 GPU 的显存读数。

        流程说明：
            1. GET `/system_stats`，任何网络层错误或非 2xx 都转成 `ComfyUnreachable`；
            2. 从响应的 `devices` 数组取第一项；
            3. 数组为空说明 ComfyUI 没认到 GPU —— 这同样是「上游不可用」，
               而不是一个可以返回零值继续走下去的情况。

        用这个接口而不是 `nvidia-smi`：网关只需说 HTTP 就能拿到显存，
        不必 ssh 进 shell，边界更干净（架构文档 §5）。

        Returns:
            第一块 GPU 的 `GpuStats`。

        Raises:
            ComfyUnreachable: 请求失败、超时、返回非 2xx，或响应里没有设备。
        """
        try:
            response = await self._client.get("/system_stats")
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ComfyUnreachable(f"{settings.comfy_base_url} 不可达: {exc!r}") from exc

        devices = response.json().get("devices", [])
        if not devices:
            raise ComfyUnreachable("ComfyUI 的 /system_stats 里没有 devices，可能没认到 GPU")

        device = devices[0]
        return GpuStats(
            name=device["name"],
            vram_total_bytes=device["vram_total"],
            vram_free_bytes=device["vram_free"],
        )
