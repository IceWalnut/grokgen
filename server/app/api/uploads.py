"""上传素材。

契约见 `Docs/contract/gateway_api_v0.1.md` §3。

**上传与提交是分开的两步**：先传拿到 `asset_id`，生成任务里只带这个字符串。
任务提交要快，上传可以慢（需求 F6）。
"""

import re
import secrets
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, UploadFile
from pydantic import BaseModel

from app.comfy.client import ComfyUnreachable
from app.core.config import settings
from app.media.probe import MediaProbeError

router = APIRouter(prefix="/v1/uploads", tags=["uploads"])

# ComfyUI 会把文件名直接拼进路径，所以只放行安全字符。
# 这不是替代 ComfyUI 的路径穿越防护（它自己也有），而是不让奇怪的名字走到那一步。
_SAFE_STEM = re.compile(r"[^A-Za-z0-9_-]+")

ALLOWED_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
MAX_UPLOAD_BYTES = 32 * 1024 * 1024


class UploadedImageResponse(BaseModel):
    """`POST /v1/uploads/image` 的响应。

    Attributes:
        asset_id: **就是 workflow 里 `LoadImage.image` 要填的值**
            （相对 ComfyUI `input/` 的路径），不是一个不透明的 id。
        width / height: 图片像素尺寸。⚠️ App 别丢掉它们 ——
            提交任务时不指定尺寸的话，网关就是按这两个数推画布的。
        size_bytes: 文件大小。
    """

    asset_id: str
    width: int
    height: int
    size_bytes: int


def _safe_filename(original: str) -> str:
    """生成一个安全、唯一、看得出来历的文件名。

    形如 `img_20260922_184455_a1b2c3.png`。加随机后缀是为了避开 ComfyUI 的
    重名改名逻辑（它会把冲突的文件改成 `name (1).ext`），
    虽然那条路径也处理得了，但少一次改名就少一处要对齐的地方。

    Args:
        original: 客户端给的原始文件名，只用来取扩展名。

    Returns:
        安全的文件名。

    Raises:
        HTTPException: 扩展名不在允许列表里。
    """
    suffix = Path(original or "").suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(
            status_code=415,
            detail=f"不支持的图片格式 {suffix or '(无扩展名)'}，"
            f"允许：{sorted(ALLOWED_SUFFIXES)}",
        )
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"img_{stamp}_{secrets.token_hex(3)}{suffix}"


@router.post("/image", response_model=UploadedImageResponse)
async def upload_image(request: Request, file: UploadFile) -> UploadedImageResponse:
    """接收一张图片，转存到 ComfyUI 的 `input/` 子目录下，并读出尺寸。

    流程说明：
        1. 校验扩展名与大小；
        2. 通过 `ComfyClient` 转存 —— **不直接写文件系统**，
           所有 ComfyUI 交互收在一处（架构文档 §3）；
        3. 用 `ImageProbe` 读尺寸。ComfyUI 的上传响应里**没有** width/height，
           而不指定画布时要靠它们推算比例。

    Args:
        request: 用来取 `app.state` 上的客户端与探测器。
        file: multipart 里的 `file` 字段。

    Returns:
        `UploadedImageResponse`。

    Raises:
        HTTPException: 格式不支持（415）、文件过大（413）、
            ComfyUI 不可达（502）、尺寸读不出来（500）。
    """
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"图片 {len(data)} 字节，超过上限 {MAX_UPLOAD_BYTES} 字节",
        )
    if not data:
        raise HTTPException(status_code=400, detail="上传内容为空")

    filename = _safe_filename(file.filename)

    try:
        uploaded = await request.app.state.comfy.upload_image(
            data=data, filename=filename, subfolder=settings.upload_subfolder
        )
    except ComfyUnreachable as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    # ⚠️ 用 uploaded.name 而不是上面那个 filename：
    # ComfyUI 遇到重名且内容不同会改名，用错名字会引用到另一张图且不报错。
    stored_path = settings.resolved_input_dir() / uploaded.subfolder / uploaded.name
    try:
        size = await request.app.state.image_probe.image_size(stored_path)
    except MediaProbeError as exc:
        # 读不出尺寸就明确失败，不要回退到一个猜测值 ——
        # 猜错的后果是画面变形，而那不会以任何形式报错。
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return UploadedImageResponse(
        asset_id=uploaded.reference,
        width=size.width,
        height=size.height,
        size_bytes=uploaded.size_bytes or len(data),
    )
