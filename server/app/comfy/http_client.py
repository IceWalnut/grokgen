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
    QueueState,
    UploadedImage,
)
from app.core.config import settings

# /object_info 有两三兆，/prompt 的执行可能排队，都不能用 health 那个 2 秒超时。
LONG_TIMEOUT_SECONDS = 60.0


def _long_timeout() -> httpx.Timeout:
    """给慢接口用的超时：**读可以久，连接不许久**。

    ⚠️ **不要直接传 `timeout=LONG_TIMEOUT_SECONDS`** —— httpx 里一个标量会
    **同时**设成连接、读、写、连接池四个超时。而 ComfyUI 就在本机：
    连本机端口从来不该要 60 秒。

    M2R2 真机实测踩到的后果：ComfyUI 停掉时上传一张图，网关要**等满 60 秒**
    才放弃并返回 502。而 App 的读超时也是 60 秒，于是**客户端总是先一步超时** ——
    用户看到的是「服务器没有应答」，而实际上网关好好的、挂掉的是 ComfyUI。
    **一次误诊，把人指向错误的排查方向。**

    ⚠️ 这个坑在别的机器上未必出现：连关闭端口正常会立刻收到 ECONNREFUSED。
    但这台服务器的 WSL 用镜像网络，**关闭端口上的连接是被丢弃而不是被拒绝**，
    所以「连接超时」这一项才真的会跑满。

    Returns:
        连接用 `comfy_timeout_seconds`（2 秒），读/写/池用 60 秒。
    """
    return httpx.Timeout(
        LONG_TIMEOUT_SECONDS,
        connect=settings.comfy_timeout_seconds,
    )


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
                拉节点定义、提交任务这些慢接口各自覆盖成 `_long_timeout()` ——
                **那个只放宽读超时，连接超时仍然是这里的值**，见该函数的说明。
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
            timeout: 覆盖默认超时，秒。**不给就用客户端那 2 秒**，见下。

        Returns:
            解析后的 JSON。

        Raises:
            ComfyUnreachable: 请求失败、超时或返回非 2xx。
        """
        # ⚠️ **httpx 里请求级的 `timeout=None` 意思是「不设超时」，
        #    不是「用客户端默认值」** —— 它会把构造器里那 2 秒显式覆盖掉。
        #    表示「用默认」要用 `httpx.USE_CLIENT_DEFAULT` 这个哨兵值。
        #
        # 这个坑以前一直看不出来：ComfyUI 没跑时连本机的关闭端口，
        # 正常会立刻收到 ECONNREFUSED，所以有没有超时都一样快。
        # 但这台服务器的 WSL 用的是镜像网络，**关闭端口上的连接是被丢弃而不是被拒绝**，
        # 于是「没有超时」变成了真的永远等下去。
        #
        # 实测（2026-09-24，ComfyUI 已停时打 127.0.0.1:8188）：
        #   timeout=None        跑满 12 秒仍未返回
        #   不传 timeout        2.00 秒后抛 ConnectTimeout
        #   USE_CLIENT_DEFAULT  2.00 秒后抛 ConnectTimeout
        #
        # 后果有两处，都很严重：
        #   `/v1/health` 在 ComfyUI 挂掉时永远不返回 —— 而那正是它存在的意义；
        #   `queue()` 在任务轮询循环里，ComfyUI 中途挂掉会让网关永远轮询下去。
        effective_timeout = httpx.USE_CLIENT_DEFAULT if timeout is None else timeout
        try:
            response = await self._client.get(path, timeout=effective_timeout)
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
        return await self._get_json("/object_info", timeout=_long_timeout())

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
                "/prompt", json=payload, timeout=_long_timeout()
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
                "/upload/image", files=files, data=form, timeout=_long_timeout()
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

    async def queue(self) -> QueueState:
        """读 `/queue`，取出正在跑的与排队中的 `prompt_id`。

        流程说明：
            1. GET `/queue`，响应形如
               `{"queue_running": [...], "queue_pending": [...]}`；
            2. 两个列表里每个条目都是一个**数组**，形如
               `[number, prompt_id, prompt, extra_data, outputs]`，
               `prompt_id` 在下标 1；
            3. 取不出 `prompt_id` 的条目直接跳过，不让一条畸形数据
               把整次查询变成异常 —— 这个接口的调用方（轮询循环）
               宁可这一轮少看见一个 id，也不该因此把任务判成失败。

        ⚠️ **这个接口不占 GPU。** 轮询期间每轮调一次的代价可以忽略。

        Returns:
            `QueueState`。ComfyUI 空闲时两个元组都是空的。

        Raises:
            ComfyUnreachable: ComfyUI 不可达。
        """
        body = await self._get_json("/queue")

        def prompt_ids(key: str) -> tuple[str, ...]:
            """从 `/queue` 的一个列表里挑出所有 `prompt_id`。"""
            entries = body.get(key) or []
            return tuple(
                entry[1]
                for entry in entries
                if isinstance(entry, (list, tuple))
                and len(entry) > 1
                and isinstance(entry[1], str)
            )

        return QueueState(
            running=prompt_ids("queue_running"), pending=prompt_ids("queue_pending")
        )

    async def interrupt(self) -> None:
        """中断 ComfyUI 当前正在执行的任务。

        ⚠️ **无参数，打的是此刻正在跑的那一个，不管是谁提交的。**
        调用方必须先用 `queue()` 确认那确实是自己要取消的任务，
        否则会打断用户在 ComfyUI 网页界面上手工提交的生成。

        Returns:
            无。成功即表示中断请求已被 ComfyUI 接受 ——
            **不代表任务已经停止**，停止是异步发生的，
            调用方要继续轮询 `/history` 才能看到最终结果。

        Raises:
            ComfyUnreachable: 请求失败或返回非 2xx。
        """
        await self._post_json("/interrupt", {})

    async def delete_queued(self, prompt_id: str) -> None:
        """把一个还没开始执行的任务从 ComfyUI 队列里删掉。

        Args:
            prompt_id: 要删的任务 id。ComfyUI 对不存在的 id 也返回 2xx，
                所以「删一个已经开始跑的任务」不会报错，**只是没有效果** ——
                调用方要靠 `queue()` 先判断它到底在不在排队。

        Returns:
            无。

        Raises:
            ComfyUnreachable: 请求失败或返回非 2xx。
        """
        await self._post_json("/queue", {"delete": [prompt_id]})

    async def _post_json(self, path: str, payload: dict) -> None:
        """POST 一个 JSON 请求，不关心响应体，把任何失败翻译成 `ComfyUnreachable`。

        给 `/interrupt`、`/queue` 这类「只看成功与否」的控制接口用。
        `/prompt` 不走这里 —— 它要把校验失败区分出来，见 `submit`。

        Args:
            path: 相对路径。
            payload: JSON 请求体。

        Returns:
            无。

        Raises:
            ComfyUnreachable: 请求失败、超时或返回非 2xx。
        """
        try:
            response = await self._client.post(path, json=payload)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ComfyUnreachable(f"{settings.comfy_base_url}{path} 调用失败: {exc!r}") from exc

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
        body = await self._get_json(f"/history/{prompt_id}", timeout=_long_timeout())
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
