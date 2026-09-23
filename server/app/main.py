"""grokgen 网关的 FastAPI 应用。

M1R1 只有 `/v1/health`。任务提交、媒体库、GPU 管理分别在 M1R3 之后加。
"""

import logging
import subprocess
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI

from app.api import jobs, uploads
from app.comfy.client import ComfyClient, ComfyUnreachable
from app.comfy.http_client import HttpComfyClient
from app.core.config import settings
from app.core.jobs import JobManager
from app.media.probe import FfprobeImageProbe, ImageProbe

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def gateway_version() -> str:
    """当前跑的是哪个 commit。

    服务器上那份代码是 `origin/main` 的只读检出，所以这个值直接回答
    「我现在访问到的到底是不是我刚推上去的那一版」——
    部署链路出问题时，这往往是第一个要看的东西。

    取不到时返回 `"unknown"` 而不抛错：版本号拿不到不该让 health 失败。

    Returns:
        commit 短哈希，或 `"unknown"`。
    """
    repo_root = Path(__file__).resolve().parents[2]
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"

    if result.returncode != 0:
        return "unknown"
    return result.stdout.strip() or "unknown"


def create_app(
    comfy_client: ComfyClient | None = None,
    image_probe: ImageProbe | None = None,
    job_manager: JobManager | None = None,
) -> FastAPI:
    """组装 FastAPI 应用。

    Args:
        comfy_client: 注入的 ComfyUI 客户端。测试传 `FakeComfyClient`；
            传 `None` 时用真实的 `HttpComfyClient`。
        image_probe: 注入的图片探测器。测试传 `FakeImageProbe`；
            传 `None` 时用 `FfprobeImageProbe`。
        job_manager: 注入的任务管理器。测试传一个轮询间隔为 0、
            关掉预检的实例；传 `None` 时按配置建一个。

    前两个参数是开发机上能跑测试的原因 —— 它们各自封着一个开发机没有的
    外部依赖（GPU 上的 ComfyUI、以及 ffprobe），
    是架构文档 §3「外部调用必须可替换」那条约束的落点。

    Returns:
        配好路由与 lifespan 的 FastAPI 实例。
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # 客户端持有连接池，跟着应用生命周期走，不要每个请求建一个。
        app.state.comfy = comfy_client or HttpComfyClient()
        app.state.image_probe = image_probe or FfprobeImageProbe()
        # ⚠️ ffprobe 缺失要在启动时就喊出来，而不是等第一次上传才失败。
        if image_probe is None and not FfprobeImageProbe.available():
            logger.warning(
                "找不到 ffprobe，上传图片时读不出尺寸，画布将无法按图片比例推算"
            )
        app.state.jobs = job_manager or JobManager(
            app.state.comfy,
            poll_interval_seconds=settings.job_poll_interval_seconds,
            timeout_seconds=settings.job_timeout_seconds,
        )
        await app.state.jobs.start()
        yield
        # ⚠️ 顺序不能反：先停任务管理器，再关客户端的连接池。
        # 反过来的话 worker 可能正拿着一个已经关掉的连接池去轮询。
        await app.state.jobs.aclose()
        await app.state.comfy.aclose()

    app = FastAPI(title="grokgen gateway", lifespan=lifespan)
    app.include_router(uploads.router)
    app.include_router(jobs.router)

    @app.get("/v1/health")
    async def health() -> dict:
        """报告网关自身状态，以及 ComfyUI 是否可达。

        ⚠️ ComfyUI 不可达时**仍然返回 200**。网关自己是健康的，
        它在如实报告上游的状态。做成 502 会让「网关没起来」和
        「ComfyUI 没起来」在客户端看起来一样，而 App 需要分别提示
        这两种情况（需求 F1）。

        Returns:
            `{"gateway": {"status", "version"}, "comfy": {...}}`。
            `comfy` 可达时含 GPU 名与显存读数；不可达时是
            `{"reachable": False, "error": "<原因>"}`。
            `gateway.ffprobe` 说明这台机器上有没有 ffprobe ——
            没有的话上传的图读不出尺寸。
        """
        comfy: dict
        try:
            stats = await app.state.comfy.system_stats()
        except ComfyUnreachable as exc:
            comfy = {"reachable": False, "error": str(exc)}
        else:
            comfy = {
                "reachable": True,
                "gpu": stats.name,
                "vram_total_bytes": stats.vram_total_bytes,
                "vram_free_bytes": stats.vram_free_bytes,
            }

        return {
            "gateway": {
                "status": "ok",
                "version": gateway_version(),
                # ⚠️ 报告出来，而不是只在启动时 warn 一句。
                # 缺了它上传的图读不出尺寸，画布就没法按比例推算 ——
                # 那会静默地退回「用默认尺寸」，表现为画面被拉伸变形。
                "ffprobe": FfprobeImageProbe.available(),
            },
            "comfy": comfy,
        }

    return app


app = create_app()
