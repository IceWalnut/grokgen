"""任务的提交、查询、列表与取消。

契约见 `Docs/contract/gateway_api_v0.1.md` §2。

⚠️ **归一化与建图在这一层做，不在 `core/jobs.py` 里做。**
理由是 `POST /v1/jobs` 的响应必须带 `normalized`（契约要求 App 显示它），
而 `normalize()` 在用户没填 seed 时会随机生成一个。
算两次就会得到两个不同的 seed —— 回报给用户的那个，和真正喂给模型的那个不是同一个，
**而且不会报错**。所以整条链路只归一化一次，结果带着走（VS-18 守这条）。
"""

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.comfy.client import ComfyUnreachable
from app.comfy.workflows.h3_video import build_workflow
from app.core.config import settings
from app.core.jobs import (
    Job,
    JobAlreadyFinished,
    JobNotFound,
    JobState,
    ManagerClosing,
)
from app.media.probe import MediaProbeError
from app.models.video_job import NormalizedParams, VideoJobRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/jobs", tags=["jobs"])

#: 列表接口一次最多返回多少条。契约 §2。
MAX_LIST_LIMIT = 200


class FailureReasonResponse(BaseModel):
    """失败原因。契约 §2 的 `failure_reason`。

    Attributes:
        kind: 失败大类，App 按它决定怎么提示。
        message: 一句话概述，可直接显示。
        detail: 结构化原始信息。
            ⚠️ `comfy_validation` 时这里是 ComfyUI 的 `node_errors` **原文**。
            App 可以折叠显示，但**不能丢** —— 排障时只有它有用。
    """

    kind: str
    message: str
    detail: object | None = None


class OutputResponse(BaseModel):
    """一个产出文件。

    ⚠️ **M1 只填得出 `kind` 与文件位置。** 契约里还列了 `item_id`、
    `duration_seconds`、`codec`、`size_bytes`，那些要读产物文件才知道，
    是 M3 媒体库索引的事。

    Attributes:
        kind: 产物类型。M1 只有 `video`。
        filename: 文件名。
        subfolder: 相对 ComfyUI `output/` 的子目录。
    """

    kind: str
    filename: str
    subfolder: str


class JobResponse(BaseModel):
    """任务的完整状态。契约 §2 的 `GET /v1/jobs/{job_id}`。

    ⚠️ 列表接口返回的每一项**用的是同一个形状**，不做精简版 ——
    队列页要显示的东西详情页也要显示，分成两种形状只会让 App 写两套解析。

    Attributes:
        job_id: 任务 id。
        state: 当前状态。
        stage: `running` 时的细分阶段。**M1 恒为 `null`**，见下。
        progress: 采样进度。**M1 恒为 `null`**。
        created_at: 创建时刻，ISO 8601 带时区。
        normalized: 归一化后的真实参数。
        notices: 给用户看的中文提示，说明「你要的」和「你会得到的」差在哪。
        outputs: 产出的文件，未完成时是空列表。
        failure_reason: 失败原因，其余情况为 `null`。

    ⚠️ **`stage` 与 `progress` 在 M1 阶段恒为 `null`。** 它们的数据来自
    ComfyUI 的 WebSocket 事件，而 M1 只做轮询。网关靠队列接口只能区分到
    「已提交」与「正在跑」，再细分不出来。**App 不要把它们为空当成异常。**
    """

    job_id: str
    state: str
    stage: str | None = None
    progress: float | None = None
    created_at: str
    normalized: NormalizedParams
    notices: list[str]
    outputs: list[OutputResponse] = Field(default_factory=list)
    failure_reason: FailureReasonResponse | None = None


class JobListResponse(BaseModel):
    """任务列表。

    Attributes:
        items: 任务，按创建时间倒序。
        total: 过滤后、**截断前**的总条数。
            没有它 App 不知道列表有没有被 `limit` 截掉。
    """

    items: list[JobResponse]
    total: int


class SubmitResponse(BaseModel):
    """提交任务的响应。契约 §2 的 `POST /v1/jobs`。

    Attributes:
        job_id: 任务 id，后续查询用。
        state: 提交后的状态，正常是 `queued`。
        normalized: 归一化后的真实参数。**App 必须显示它** ——
            尺寸、实际时长、seed 三个值都可能和用户填的不一样。
        notices: 中文提示，说明差别在哪。
    """

    job_id: str
    state: str
    normalized: NormalizedParams
    notices: list[str]


def _to_response(job: Job) -> JobResponse:
    """把内部的 `Job` 转成契约形状的响应。

    Args:
        job: 内部任务对象。

    Returns:
        `JobResponse`。
    """
    failure = None
    if job.failure_reason is not None:
        failure = FailureReasonResponse(
            kind=job.failure_reason.kind.value,
            message=job.failure_reason.message,
            detail=job.failure_reason.detail,
        )
    return JobResponse(
        job_id=job.job_id,
        state=job.state.value,
        stage=job.stage,
        progress=job.progress,
        created_at=job.created_at.isoformat(),
        normalized=job.normalized,
        notices=job.normalized.notices,
        outputs=[
            OutputResponse(
                kind="video", filename=o.filename, subfolder=o.subfolder
            )
            for o in job.outputs
        ],
        failure_reason=failure,
    )


async def _probe_first_frame(request: Request, asset_id: str):
    """读首帧图的像素尺寸，用来按比例推画布。

    路径拼法与 `api/uploads.py` 一致：`asset_id` 本身就是相对 ComfyUI `input/`
    的路径（上传接口返回的就是它）。

    ⚠️ **读不出来就明确失败，不要回退到一个猜测的尺寸。**
    猜错的后果是首帧被拉伸变形，而那不会以任何形式报错
    （原生节点对首帧用的是 `crop="disabled"`，是拉伸不是裁剪）。

    Args:
        request: 用来取 `app.state` 上的探测器。
        asset_id: 上传接口返回的 `asset_id`。

    Returns:
        `ImageSize`。

    Raises:
        HTTPException: 读不出尺寸（400）。
    """
    stored_path = Path(settings.resolved_input_dir()) / asset_id
    try:
        return await request.app.state.image_probe.image_size(stored_path)
    except MediaProbeError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"读不出首帧图 {asset_id} 的尺寸：{exc}",
        ) from exc


@router.post("", response_model=SubmitResponse)
async def submit_job(request: Request, payload: VideoJobRequest) -> SubmitResponse:
    """提交一个生成任务。

    流程说明：
        1. 有首帧图就先读它的尺寸 —— 不指定宽高时画布要按它的比例推算；
        2. **一次性**归一化并拼出 workflow，结果一起交给队列。
           这一步只做一次是有意的，见模块 docstring；
        3. 登记任务、排进队列，立刻返回 `queued`。
           **不等生成开始** —— 提交要快（需求 F6）。

    Args:
        request: 用来取 `app.state` 上的探测器与任务管理器。
        payload: 请求体。线上字段名以契约为准
            （`first_frame_asset_id` 等），模型里用 alias 对上。

    Returns:
        `SubmitResponse`，带 `job_id` 与归一化结果。

    Raises:
        HTTPException: 首帧图读不出尺寸（400）、网关正在关停（503）。
    """
    source_size = None
    if payload.first_frame:
        source_size = await _probe_first_frame(request, payload.first_frame)

    workflow, normalized = build_workflow(payload, source_size)

    try:
        job = request.app.state.jobs.submit(payload, workflow, normalized)
    except ManagerClosing as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    logger.info(
        "已登记任务 %s：%s %sx%s %d 帧",
        job.job_id,
        payload.mode.value,
        normalized.width,
        normalized.height,
        normalized.length_frames,
    )
    return SubmitResponse(
        job_id=job.job_id,
        state=job.state.value,
        normalized=normalized,
        notices=normalized.notices,
    )


@router.get("", response_model=JobListResponse)
async def list_jobs(
    request: Request,
    state: JobState | None = None,
    limit: int = Query(default=20, ge=1, le=MAX_LIST_LIMIT),
) -> JobListResponse:
    """列出任务，按创建时间倒序。

    Args:
        request: 用来取任务管理器。
        state: 只要这个状态的任务；不给则全部。
        limit: 最多返回多少条，1〜200，默认 20。

    Returns:
        `JobListResponse`。`total` 是截断前的条数，
        App 靠它知道有没有被截掉。
    """
    manager = request.app.state.jobs
    jobs = manager.list_jobs(state=state, limit=limit)
    return JobListResponse(
        items=[_to_response(j) for j in jobs], total=manager.count(state=state)
    )


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(request: Request, job_id: str) -> JobResponse:
    """查一个任务的状态。

    Args:
        request: 用来取任务管理器。
        job_id: 任务 id。

    Returns:
        `JobResponse`。

    Raises:
        HTTPException: 没有这个任务（404）。
    """
    try:
        job = request.app.state.jobs.get(job_id)
    except JobNotFound as exc:
        raise HTTPException(status_code=404, detail=f"没有任务 {job_id}") from exc
    return _to_response(job)


@router.post("/{job_id}/cancel", response_model=JobResponse)
async def cancel_job(request: Request, job_id: str) -> JobResponse:
    """取消一个任务。

    ⚠️ 返回时状态**不一定已经是 `cancelled`**：任务已经提交给 ComfyUI 时，
    中断是异步发生的，终态由后台的轮询循环落。App 应当继续查询。

    Args:
        request: 用来取任务管理器。
        job_id: 任务 id。

    Returns:
        处理后的 `JobResponse`。

    Raises:
        HTTPException: 没有这个任务（404）、任务已经结束或已在收尾（409）、
            ComfyUI 不可达（502）。
    """
    try:
        job = await request.app.state.jobs.cancel(job_id)
    except JobNotFound as exc:
        raise HTTPException(status_code=404, detail=f"没有任务 {job_id}") from exc
    except JobAlreadyFinished as exc:
        raise HTTPException(
            status_code=409,
            detail=f"任务 {job_id} 已经结束或正在收尾，不能取消",
        ) from exc
    except ComfyUnreachable as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _to_response(job)


def _resolve_output_path(job: Job) -> Path:
    """把任务产物的位置解析成一个可以安全读取的绝对路径。

    流程说明：
        1. 从 `job.outputs` 取第一个产物（M1 一个任务只有一个视频）；
        2. 拼到 ComfyUI 的输出根目录下；
        3. `resolve()` 之后断言它仍在输出根目录之内；
        4. 确认文件真的存在。

    ⚠️ **第 3 步不是形式主义。** `filename` 与 `subfolder` 来自 ComfyUI
    `/history` 响应，那是**外部输入**，不是网关自己生成的。
    没有这一步，一个 `subfolder` 为 `../../..` 的记录就能让这个接口
    读出输出目录之外的任意文件 —— 而网关没有认证，
    tailnet 里任何设备都能调它（契约 §1）。

    Args:
        job: 已经处于 `done` 的任务。

    Returns:
        产物文件的绝对路径。

    Raises:
        HTTPException: 任务还没完成（409）、没有产物或文件不存在（404）、
            路径逃出了输出目录（404，**不用 403** —— 403 等于告诉对方
            「这个路径存在但你不能看」，那本身就是一条信息）。
    """
    if job.state is not JobState.DONE:
        raise HTTPException(
            status_code=409,
            detail=f"任务 {job.job_id} 还没有产物，当前状态是 {job.state.value}",
        )

    if not job.outputs:
        # 任务是 done 却没有产物 —— 这是网关自己的状态不一致，不是客户端的错。
        logger.error("任务 %s 处于 done 但 outputs 为空", job.job_id)
        raise HTTPException(
            status_code=500, detail=f"任务 {job.job_id} 已完成但没有记录任何产物"
        )

    output = job.outputs[0]
    root = settings.resolved_output_dir().resolve()
    candidate = (root / output.subfolder / output.filename).resolve()

    if not candidate.is_relative_to(root):
        logger.error(
            "任务 %s 的产物路径逃出了输出目录：subfolder=%r filename=%r",
            job.job_id,
            output.subfolder,
            output.filename,
        )
        raise HTTPException(status_code=404, detail=f"没有任务 {job.job_id} 的产物")

    if not candidate.is_file():
        raise HTTPException(
            status_code=404,
            detail=f"任务 {job.job_id} 的产物文件不在磁盘上：{output.subfolder}/{output.filename}",
        )

    return candidate


@router.get("/{job_id}/video")
async def get_job_video(request: Request, job_id: str) -> FileResponse:
    """取回任务产出的视频，支持 Range 请求。

    ⭐ **Range 是手机上能拖进度条的唯一依赖。** 这里不自己实现它 ——
    Starlette 的 `FileResponse` 已经会解析 `Range` 头、返回 206、
    设置 `Content-Range`，并默认带上 `Accept-Ranges: bytes`。
    所以这个函数的责任只有一条：**把正确且安全的文件路径交给它**。

    ⚠️ 正因为 Range 是框架给的，VS-11 那条「Range 必须真测」反而更要做 ——
    要测的是我们的接线对不对，不是框架对不对。

    Args:
        request: 用来取任务管理器。
        job_id: 任务 id。

    Returns:
        `FileResponse`，`media_type` 是 `video/mp4`。

    Raises:
        HTTPException: 任务不存在（404）、还没完成（409）、
            产物不存在（404）、状态不一致（500）。详见 `_resolve_output_path`。
    """
    try:
        job = request.app.state.jobs.get(job_id)
    except JobNotFound as exc:
        raise HTTPException(status_code=404, detail=f"没有任务 {job_id}") from exc

    path = _resolve_output_path(job)
    return FileResponse(path, media_type="video/mp4", filename=path.name)
