"""`ComfyClient` 的真实实现，通过 HTTP 与 ComfyUI 通信。

⚠️ 这是网关里唯一允许说 HTTP 的模块。别处出现 `httpx` 就是分层破了（VS-13）。
"""

import httpx

from app.comfy.client import (
    ComfyUnreachable,
    ComfyValidationError,
    GpuStats,
    JobRecord,
    OutputFile,
    UploadedImage,
)
from app.core.config import settings

# /object_info 有两三兆，/prompt 的执行可能排队，都不能用 health 那个 2 秒超时。
LONG_TIMEOUT_SECONDS = 60.0


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
                拉节点定义、提交任务这些慢接口各自覆盖成 `LONG_TIMEOUT_SECONDS`。
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

    async def _get_json(self, path: str, timeout: float | None = None) -> dict:
        """GET 一个 JSON 接口，把任何网络层失败翻译成 `ComfyUnreachable`。

        Args:
            path: 相对路径，例如 `/system_stats`。
            timeout: 覆盖默认超时，秒。

        Returns:
            解析后的 JSON。

        Raises:
            ComfyUnreachable: 请求失败、超时或返回非 2xx。
        """
        try:
            response = await self._client.get(path, timeout=timeout)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ComfyUnreachable(f"{settings.comfy_base_url}{path} 不可达: {exc!r}") from exc
        return response.json()

    async def system_stats(self) -> GpuStats:
        """读 ComfyUI 的 `/system_stats`，取第一块 GPU 的显存读数。

        用这个接口而不是 `nvidia-smi`：网关只需说 HTTP 就能拿到显存，
        不必 ssh 进 shell，边界更干净（架构文档 §5）。

        Returns:
            第一块 GPU 的 `GpuStats`。

        Raises:
            ComfyUnreachable: 请求失败，或响应里没有设备。
        """
        devices = (await self._get_json("/system_stats")).get("devices", [])
        if not devices:
            raise ComfyUnreachable("ComfyUI 的 /system_stats 里没有 devices，可能没认到 GPU")

        device = devices[0]
        return GpuStats(
            name=device["name"],
            vram_total_bytes=device["vram_total"],
            vram_free_bytes=device["vram_free"],
        )

    async def object_info(self) -> dict:
        """拉取全部节点定义，给提交前的预检用。

        响应有两三兆（约 1300 个节点），所以用长超时。

        Returns:
            `{节点名: 节点定义}`。

        Raises:
            ComfyUnreachable: 请求失败或超时。
        """
        return await self._get_json("/object_info", timeout=LONG_TIMEOUT_SECONDS)

    async def submit(self, workflow: dict, client_id: str) -> str:
        """把一张 workflow 提交给 ComfyUI。

        流程说明：
            1. POST `/prompt`；
            2. ComfyUI 先校验再排队。校验不过时它返回 **HTTP 400 带
               `node_errors`** —— 这种情况要抛 `ComfyValidationError`
               并把 `node_errors` 原样带上，而不是笼统地报「不可达」；
            3. 校验通过则返回 `prompt_id`。

        ⚠️ 校验通过就意味着任务已经排进队列了，**没有「只校验不执行」的模式**。
        所以提交之前应当先用 `workflows.validation` 对图做一次本地预检。

        Args:
            workflow: API 格式的节点图。
            client_id: 关联 WebSocket 进度事件用。

        Returns:
            `prompt_id`。

        Raises:
            ComfyValidationError: 图没通过校验，异常里带 `node_errors`。
            ComfyUnreachable: 其他任何失败。
        """
        payload = {"prompt": workflow, "client_id": client_id}
        try:
            response = await self._client.post(
                "/prompt", json=payload, timeout=LONG_TIMEOUT_SECONDS
            )
        except httpx.HTTPError as exc:
            raise ComfyUnreachable(f"提交失败: {exc!r}") from exc

        if response.status_code >= 400:
            # 校验失败走这里。ComfyUI 返回的是结构化的 node_errors，不要丢。
            try:
                body = response.json()
            except ValueError:
                raise ComfyUnreachable(
                    f"ComfyUI 返回 {response.status_code}，响应不是 JSON: {response.text[:200]}"
                ) from None
            raise ComfyValidationError(
                message=body.get("error", {}).get("message", "workflow 校验失败"),
                node_errors=body.get("node_errors", {}),
            )

        prompt_id = response.json().get("prompt_id")
        if not prompt_id:
            raise ComfyUnreachable(f"ComfyUI 没有返回 prompt_id: {response.text[:200]}")
        return prompt_id

    async def upload_image(
        self, data: bytes, filename: str, subfolder: str
    ) -> UploadedImage:
        """把图片 POST 给 ComfyUI 的 `/upload/image`。

        ⚠️ **必须用响应里返回的 `name`，不能用自己发过去的那个。**
        ComfyUI 在不覆盖模式下会先比文件 hash：内容相同就复用已有文件，
        内容不同则把新文件改名成 `name (1).ext`。
        用错名字的后果是 workflow 引用到另一张图，而且**不会报错**。

        Args:
            data: 图片字节。
            filename: 期望的文件名。
            subfolder: `input/` 下的子目录。

        Returns:
            `UploadedImage`。

        Raises:
            ComfyUnreachable: 请求失败、非 2xx，或响应里没有 `name`。
        """
        files = {"image": (filename, data, "application/octet-stream")}
        form = {"type": "input", "subfolder": subfolder}
        try:
            response = await self._client.post(
                "/upload/image", files=files, data=form, timeout=LONG_TIMEOUT_SECONDS
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ComfyUnreachable(f"上传 {filename} 失败: {exc!r}") from exc

        body = response.json()
        name = body.get("name")
        if not name:
            raise ComfyUnreachable(f"ComfyUI 上传响应里没有 name: {response.text[:200]}")

        asset = body.get("asset") or {}
        return UploadedImage(
            name=name,
            subfolder=body.get("subfolder", subfolder),
            size_bytes=asset.get("size"),
        )

    async def history(self, prompt_id: str) -> JobRecord | None:
        """查一次任务的结果。

        Args:
            prompt_id: `submit` 返回的 id。

        Returns:
            `JobRecord`；ComfyUI 里还没有这条记录时返回 `None`——
            那表示任务还在排队，**不是错误**。

        Raises:
            ComfyUnreachable: 请求失败。
        """
        body = await self._get_json(f"/history/{prompt_id}", timeout=LONG_TIMEOUT_SECONDS)
        record = body.get(prompt_id)
        if record is None:
            return None

        status = record.get("status", {})
        outputs: list[OutputFile] = []
        for node_id, node_output in record.get("outputs", {}).items():
            # 视频与图片都落在 "images" 这个键下，video 类型多一个 animated 标记。
            for item in node_output.get("images", []) + node_output.get("videos", []):
                outputs.append(
                    OutputFile(
                        filename=item["filename"],
                        subfolder=item.get("subfolder", ""),
                        node_id=node_id,
                    )
                )

        return JobRecord(
            prompt_id=prompt_id,
            status=status.get("status_str", "unknown"),
            completed=bool(status.get("completed", False)),
            outputs=outputs,
            messages=status.get("messages", []),
        )
