"""任务状态机与容量为 1 的串行队列。

**这个模块存在的理由是一条硬约束**：这台机器的显存是 16 GB，而 MiniMax H3 的
主模型 14 GB、text encoder 15 GB，单这两个就超过显存总量，靠 ComfyUI 分批加载
才跑得起来（需求文档 §5 F7）。所以同一时间只能有一个重 GPU 任务，
而这条约束**由网关强制，不靠调用方自觉**。

串行不是靠信号量计数实现的，是**结构上只有一个 worker 协程**在消费队列 ——
「同时跑两个」在这个形状里根本表达不出来。

⚠️ 分层：这里不许出现任何 HTTP 调用或 ComfyUI 的地址（架构文档 §3，VS-13 强制）。
所有与 ComfyUI 的交互都走 `ComfyClient` 这个接口。

M1 的简化：状态只存在内存里，**网关一重启就全丢**。持久化放到 M3 和媒体库索引
一起做。但状态机的形状现在就要定对，后面只是换存储。
"""

import asyncio
import contextlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

from app.comfy.client import (
    ComfyClient,
    ComfyUnreachable,
    ComfyValidationError,
    JobRecord,
    OutputFile,
)
from app.comfy.workflows.validation import check_workflow
from app.models.video_job import NormalizedParams, VideoJobRequest

logger = logging.getLogger(__name__)


class JobState(str, Enum):
    """任务状态。取值与架构文档 §4、契约 §2 完全一致。"""

    QUEUED = "queued"
    PREPARING = "preparing"
    SUBMITTED = "submitted"
    RUNNING = "running"
    POSTPROCESSING = "postprocessing"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class FailureKind(str, Enum):
    """失败原因的大类。取值与契约 §2 的 `failure_reason.kind` 一致。"""

    COMFY_VALIDATION = "comfy_validation"
    OUT_OF_MEMORY = "out_of_memory"
    COMFY_UNREACHABLE = "comfy_unreachable"
    TIMEOUT = "timeout"
    INTERNAL = "internal"


#: 三个终态。到了这里就不再变化。
TERMINAL_STATES: frozenset[JobState] = frozenset(
    {JobState.DONE, JobState.FAILED, JobState.CANCELLED}
)

#: 「这个任务已经占住 ComfyUI 了」的两个状态。
#:
#: ⭐ **VS-9 的判据就是这个集合**：任何时刻处于其中的任务不能超过一个。
#: 测试里不要另抄一份字符串 —— 抄一份就会在改动时各自漂移。
#:
#: 为什么把 submitted 和 running 当成一对：它们的区别只是「ComfyUI 收下了」
#: 与「ComfyUI 开跑了」，而显存是在这两种情况下都被占着的。
GPU_OCCUPYING_STATES: frozenset[JobState] = frozenset(
    {JobState.SUBMITTED, JobState.RUNNING}
)

#: 合法的状态转移。**架构文档 §4 那张图的可执行形式。**
#:
#: 让非法转移抛异常而不是静默接受，和 VS-13 把分层约束写成扫 AST 是同一个动作：
#: 把一条画在文档里的规矩，变成一条改坏了会立刻报错的规矩。
ALLOWED_TRANSITIONS: dict[JobState, frozenset[JobState]] = {
    JobState.QUEUED: frozenset(
        {JobState.PREPARING, JobState.FAILED, JobState.CANCELLED}
    ),
    JobState.PREPARING: frozenset(
        {JobState.SUBMITTED, JobState.FAILED, JobState.CANCELLED}
    ),
    # ⚠️ `SUBMITTED → POSTPROCESSING` 是合法的**直跳**，不能去掉。
    # M1 没有 WebSocket，靠轮询观察状态；一个短任务完全可能在两次轮询之间
    # 从「已提交」跑到「已完成」，我们根本没机会看到 running。
    # 不允许这条边，正常路径上就会抛非法转移。
    JobState.SUBMITTED: frozenset(
        {
            JobState.RUNNING,
            JobState.POSTPROCESSING,
            JobState.FAILED,
            JobState.CANCELLED,
        }
    ),
    JobState.RUNNING: frozenset(
        {JobState.POSTPROCESSING, JobState.FAILED, JobState.CANCELLED}
    ),
    # ⚠️ `POSTPROCESSING` 不能转 `CANCELLED`：产物已经出来了，
    # 这时候取消等于扔掉已经付过的几分钟 GPU。对它的取消请求返回 409。
    JobState.POSTPROCESSING: frozenset({JobState.DONE, JobState.FAILED}),
    JobState.DONE: frozenset(),
    JobState.FAILED: frozenset(),
    JobState.CANCELLED: frozenset(),
}

#: 任务记录的保留上限。M1 存内存，重启就丢，但无上限的 dict 是个跑一周才炸的东西。
MAX_RETAINED_JOBS = 500

#: 转移日志的保留上限。它是 VS-9 判据的数据源，也是排障时的时间线。
MAX_RETAINED_TRANSITIONS = 5000


class InvalidTransition(Exception):
    """试图做一次 `ALLOWED_TRANSITIONS` 里没有的状态转移。

    ⚠️ 这个异常**不应该**在正常运行中出现。它出现就说明状态机被改坏了，
    或者有人在 worker 之外的地方改了 state。
    """


class JobNotFound(Exception):
    """按 id 找不到任务。路由层把它翻译成 404。"""


class JobAlreadyFinished(Exception):
    """对一个已经结束（或已进入收尾）的任务请求取消。路由层翻译成 409。"""


class ManagerClosing(Exception):
    """网关正在关停，不再接受新任务。路由层翻译成 503。"""


@dataclass(frozen=True)
class FailureReason:
    """任务为什么失败。

    Attributes:
        kind: 失败大类，决定 App 怎么提示用户。
        message: 一句话概述，可以直接显示。
        detail: 结构化的原始信息。

            ⚠️ **`comfy_validation` 时这里必须是 ComfyUI 的 `node_errors` 原文**
            （契约 §2，VS-10 的判据）。它指明了是哪个节点的哪个字段有问题，
            排障时那是唯一有用的信息。**不要压成一句话。**
    """

    kind: FailureKind
    message: str
    detail: object | None = None


@dataclass(frozen=True)
class Transition:
    """一次状态变化。按发生顺序追加，构成任务的完整时间线。

    ⭐ **VS-9 的判据建立在这份日志上**：回放它就能算出「同时占住 GPU 的任务数」
    的峰值。这比在几个时间点采样强，因为状态是**分段常数**的 ——
    只在转移点改变，所以逐个转移检查就是完备的，不是抽样。

    Attributes:
        seq: 全局递增序号。同一微秒内的多次转移靠它定序。
        job_id: 哪个任务。
        state: 转移到了哪个状态。
        at: 发生时刻，带时区。
    """

    seq: int
    job_id: str
    state: JobState
    at: datetime


@dataclass
class Job:
    """一个任务的全部状态。

    ⚠️ **`workflow` 与 `normalized` 在提交时就算好了，之后不再重算。**
    原因见 `JobManager.submit` 的说明 —— 重算会得到不同的随机 seed。

    Attributes:
        job_id: 形如 `job_20260923_0001`，契约 §2 的形状。
        request: App 提交的原始请求，回显与排障用。
        workflow: 已经拼好的 API 格式节点图，直接就是 `POST /prompt` 的内容。
        normalized: 归一化后的真实参数，**要原样回给 App**。
        created_at: 创建时刻，带时区。列表按它倒序。
        state: 当前状态。**只能由 `JobManager._set_state` 修改。**
        stage: `running` 时的细分阶段。
            ⚠️ **M1 阶段恒为 `None`** —— 它的数据来自 ComfyUI 的 WebSocket 事件，
            而 M1 只做轮询。`GET /queue` 只能回答「是不是在跑」，
            回答不了「跑到哪一段」。M2 接 ws 后才会有值。
        progress: 采样进度百分比。同上，M1 恒为 `None`。
        prompt_id: ComfyUI 给的 id。取消与排障都要靠它。
        outputs: 产出的文件。
        failure_reason: 失败时的原因；其余情况为 `None`。
        updated_at: 最后一次状态变化的时刻。
        cancel_requested: 用户是否请求过取消。worker 在几个检查点看它。
    """

    job_id: str
    request: VideoJobRequest
    workflow: dict
    normalized: NormalizedParams
    created_at: datetime
    state: JobState = JobState.QUEUED
    stage: str | None = None
    progress: float | None = None
    prompt_id: str | None = None
    outputs: list[OutputFile] = field(default_factory=list)
    failure_reason: FailureReason | None = None
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    cancel_requested: bool = False
    # 让轮询循环的等待能被立刻打断，这样取消的响应时间不受轮询间隔影响。
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)

    @property
    def is_terminal(self) -> bool:
        """这个任务是否已经到了终态。"""
        return self.state in TERMINAL_STATES


def peak_gpu_occupancy(transitions: list[Transition]) -> int:
    """回放转移日志，算出「同时占住 GPU 的任务数」的峰值。

    ⭐ **这是 VS-9 的判据函数。**

    为什么回放日志是完备的、而不是抽样：任务状态是**分段常数**的，
    只在转移点改变。所以「在每个转移之后取一次快照的最大值」
    与「对连续时间取上确界」是同一个数。

    Args:
        transitions: 按发生顺序排列的转移日志。

    Returns:
        整段历史中，同时处于 `submitted`/`running` 的任务数的最大值。
        串行队列正确时它应当是 1（有任务跑过）或 0（一个都没跑）。
    """
    live: dict[str, JobState] = {}
    peak = 0
    for transition in transitions:
        live[transition.job_id] = transition.state
        occupied = sum(1 for state in live.values() if state in GPU_OCCUPYING_STATES)
        peak = max(peak, occupied)
    return peak


def _looks_like_out_of_memory(messages: list) -> bool:
    """从 ComfyUI 的执行消息里判断这次失败是不是显存不足。

    ⚠️ **这是字符串匹配，依据是 PyTorch 的 OOM 文案，没有真实样本佐证。**
    显存不足在这个项目里是最该被单独提示的失败（需求 §5 F7），所以值得试着分类；
    但认错了的后果只是降级成 `internal`（一个更笼统但不算错的分类），
    不会让任务被误判成成功。真实的 OOM 消息长什么样，要等实际撞上一次才知道。

    Args:
        messages: `JobRecord.messages`，ComfyUI 的执行消息原文。

    Returns:
        看起来像显存不足则为 `True`。
    """
    text = str(messages).lower()
    return "out of memory" in text or "outofmemory" in text or "cuda oom" in text


class JobManager:
    """任务的登记处、串行队列与状态推进者。

    **状态只由 worker 协程推进，路由层只读、只打标记。**
    整个网关跑在单个事件循环里，只有一个 worker，所以不需要锁。
    ⚠️ 不要在路由里直接改 `job.state` —— 那会让 `_set_state` 的
    「唯一入口」失效，非法转移也就查不出来了。
    """

    def __init__(
        self,
        comfy: ComfyClient,
        *,
        poll_interval_seconds: float = 1.0,
        timeout_seconds: float | None = None,
        preflight: bool = True,
    ) -> None:
        """构造任务管理器。

        Args:
            comfy: ComfyUI 客户端。生产用 `HttpComfyClient`，测试用 `FakeComfyClient`。
            poll_interval_seconds: 轮询任务状态的间隔，秒。测试传 0 可以让
                轮询退化成「让出一次事件循环」，从而不靠 sleep 就能推进。
            timeout_seconds: 单个任务的超时，秒。`None` 表示不超时（默认）。
                默认为什么留空见 `core/config.py` 上的说明。
            preflight: 提交前是否拉一次 `/object_info` 对图做形状预检。
                默认开 —— 一次提交错图要付几分钟模型加载的代价（M1R3 的结论）。
        """
        self._comfy = comfy
        self._poll_interval = poll_interval_seconds
        self._timeout = timeout_seconds
        self._preflight = preflight

        self._jobs: dict[str, Job] = {}
        self._transitions: list[Transition] = []
        self._seq = 0
        self._sequence_by_day: dict[str, int] = {}
        self._queue: asyncio.Queue[Job] | None = None
        self._worker_task: asyncio.Task | None = None
        self._closing = False
        # 状态一变就通知，等待方靠它醒来，而不是靠 sleep 轮询自己的内存。
        self._changed = asyncio.Condition()

    # ---- 生命周期 ----

    async def start(self) -> None:
        """建队列并起 worker 协程。由 FastAPI 的 lifespan 调用。"""
        self._queue = asyncio.Queue()
        self._worker_task = asyncio.create_task(self._worker(), name="grokgen-job-worker")

    async def aclose(self) -> None:
        """停掉 worker，不接新任务。

        ⚠️ **不会中断已经提交给 ComfyUI 的任务。** 一次 H3 生成是分钟级的 GPU 开销，
        为了让网关的状态干净而把它扔掉是亏的 —— 产物照样会落到 ComfyUI 的
        `output/` 下。代价是网关侧的状态丢失（M1 本来就不持久化），
        所以这里把 `prompt_id` 打进日志：那是重启后找回产物的唯一线索。

        Returns:
            无。
        """
        self._closing = True
        if self._worker_task is not None:
            self._worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._worker_task
            self._worker_task = None

        for job in self._jobs.values():
            if job.state in GPU_OCCUPYING_STATES:
                logger.warning(
                    "网关关停时任务 %s 仍在 ComfyUI 上执行（prompt_id=%s）。"
                    "它会继续跑完并把产物写到 output/，但网关不再跟踪它 —— "
                    "要找回产物就用这个 prompt_id 查 ComfyUI 的 /history",
                    job.job_id,
                    job.prompt_id,
                )

    # ---- 路由层用的接口 ----

    def submit(
        self, request: VideoJobRequest, workflow: dict, normalized: NormalizedParams
    ) -> Job:
        """登记一个任务并排进队列，立刻返回。

        ⚠️ **workflow 与 normalized 由调用方（路由层）先算好再传进来，
        这里不自己算。** 这不是可有可无的分工：

        `normalize()` 在用户没填 seed 时会 `random.randrange(2**63)` 生成一个，
        而 `build_workflow()` 内部会调 `normalize()`。
        如果「提交时算一次回给 App、执行时再算一次喂给 ComfyUI」，
        **两次的 seed 不一样，而且不会报错** ——
        用户拿着回报的 seed 永远复现不出那个视频。
        所以整条链路只归一化一次，结果带着走（VS-18 守这条）。

        Args:
            request: 原始请求，回显用。
            workflow: 已拼好的节点图。
            normalized: 与 `workflow` 同一次归一化产出的参数。

        Returns:
            状态为 `queued` 的 `Job`。

        Raises:
            ManagerClosing: 网关正在关停。
            RuntimeError: `start()` 还没调用过。
        """
        if self._closing:
            raise ManagerClosing("网关正在关停，不再接受新任务")
        if self._queue is None:
            raise RuntimeError("JobManager.start() 还没被调用，队列不存在")

        now = datetime.now(timezone.utc)
        job = Job(
            job_id=self._next_job_id(now),
            request=request,
            workflow=workflow,
            normalized=normalized,
            created_at=now,
            updated_at=now,
        )
        self._jobs[job.job_id] = job
        self._record_transition(job, JobState.QUEUED)
        self._evict_old_jobs()
        self._queue.put_nowait(job)
        return job

    def get(self, job_id: str) -> Job:
        """按 id 取一个任务。

        Args:
            job_id: 任务 id。

        Returns:
            对应的 `Job`。

        Raises:
            JobNotFound: 没有这个 id。
        """
        job = self._jobs.get(job_id)
        if job is None:
            raise JobNotFound(job_id)
        return job

    def list_jobs(self, state: JobState | None = None, limit: int = 20) -> list[Job]:
        """列出任务，按创建时间倒序（最新的在前）。

        ⚠️ 名字不叫 `list`：那会在类作用域里遮蔽内置的 `list`，
        使同一个类里后续的 `-> list[X]` 注解解析到这个方法上，
        报一个与真实原因毫无关系的 `'function' object is not subscriptable`。

        Args:
            state: 只要这个状态的任务；`None` 表示全部。
            limit: 最多返回多少条。

        Returns:
            排好序、截断后的任务列表。
        """
        jobs = [j for j in self._jobs.values() if state is None or j.state is state]
        jobs.sort(key=lambda j: j.created_at, reverse=True)
        return jobs[:limit]

    def count(self, state: JobState | None = None) -> int:
        """数一数有多少个任务（截断前）。

        Args:
            state: 只数这个状态的；`None` 表示全部。

        Returns:
            条数。App 靠它知道列表有没有被 `limit` 截掉。
        """
        return sum(1 for j in self._jobs.values() if state is None or j.state is state)

    async def cancel(self, job_id: str) -> Job:
        """取消一个任务。

        流程说明：
            1. 已经是终态、或已进入 `postprocessing` ⇒ 抛 `JobAlreadyFinished`
               （产物都出来了，这时候取消等于白付那几分钟 GPU）；
            2. 打上取消标记并叫醒轮询循环，让取消的响应时间不受轮询间隔影响；
            3. 还在 `queued` ⇒ 直接置 `cancelled`，**完全不碰 ComfyUI**；
            4. 在 `preparing` ⇒ 只打标记，由 worker 在提交前的检查点自己落终态。
               **不能在这里直接改状态** —— 那会和 worker 形成竞态，
               可能出现「已取消的任务又被推进到 submitted」；
            5. 已经提交出去 ⇒ 交给 `_interrupt_if_ours`，见那里的说明。

        Args:
            job_id: 任务 id。

        Returns:
            处理后的 `Job`。注意第 4、5 步返回时状态可能还不是 `cancelled` ——
            终态由 worker 落，调用方应当继续查询。

        Raises:
            JobNotFound: 没有这个 id。
            JobAlreadyFinished: 任务已到终态或已进入 `postprocessing`。
        """
        job = self.get(job_id)
        if job.is_terminal or job.state is JobState.POSTPROCESSING:
            raise JobAlreadyFinished(job_id)

        job.cancel_requested = True
        job.cancel_event.set()

        if job.state is JobState.QUEUED:
            # worker 还没碰它，这里落终态是安全的。
            # ⚠️ asyncio.Queue 没法删元素，所以队列里那个壳还在 ——
            # worker 取出来时会先看 cancel_requested 然后跳过。
            await self._set_state(job, JobState.CANCELLED)
            return job

        if job.state is JobState.PREPARING:
            return job

        await self._interrupt_if_ours(job)
        return job

    async def wait_for(
        self, job_id: str, states: set[JobState], timeout: float = 5.0
    ) -> Job:
        """等一个任务进入指定状态之一。

        ⚠️ **这是给测试和将来的事件推送用的，不是生产路径上的等待。**
        它靠状态变化的通知醒来，不轮询、不 sleep ——
        所以测试可以「等条件」而不是「等时间」，不会 flaky。

        Args:
            job_id: 任务 id。
            states: 等待的目标状态集合。
            timeout: 最多等多久，秒。超了说明真的卡住了，不是抖动。

        Returns:
            进入目标状态后的 `Job`。

        Raises:
            JobNotFound: 没有这个 id。
            asyncio.TimeoutError: 超时。
        """
        job = self.get(job_id)

        async def _wait() -> None:
            async with self._changed:
                while job.state not in states:
                    await self._changed.wait()

        await asyncio.wait_for(_wait(), timeout=timeout)
        return job

    @property
    def transitions(self) -> list[Transition]:
        """状态转移日志的快照。VS-9 的判据数据源。"""
        return list(self._transitions)

    # ---- 内部：状态推进 ----

    async def _set_state(self, job: Job, state: JobState, **fields) -> None:
        """改一个任务的状态。**这是唯一允许改 `job.state` 的地方。**

        流程说明：
            1. 先查 `ALLOWED_TRANSITIONS`，不合法就抛 —— 把架构 §4 那张图
               变成一条改坏了会报错的规矩；
            2. 落盘状态与随之一起变的字段；
            3. 追加转移日志（VS-9 的判据数据源）；
            4. 通知所有等待方。

        Args:
            job: 要改的任务。
            state: 目标状态。
            **fields: 与这次转移一起设置的字段，例如 `prompt_id`、`outputs`。

        Returns:
            无。

        Raises:
            InvalidTransition: 目标状态不在当前状态的允许集合里。
        """
        if state not in ALLOWED_TRANSITIONS[job.state]:
            raise InvalidTransition(
                f"{job.job_id}: {job.state.value} → {state.value} 不是合法转移"
            )
        job.state = state
        for key, value in fields.items():
            setattr(job, key, value)
        self._record_transition(job, state)
        async with self._changed:
            self._changed.notify_all()

    def _record_transition(self, job: Job, state: JobState) -> None:
        """记一条转移日志并更新 `updated_at`。

        Args:
            job: 任务。
            state: 转移到的状态。

        Returns:
            无。
        """
        now = datetime.now(timezone.utc)
        job.updated_at = now
        self._seq += 1
        self._transitions.append(Transition(self._seq, job.job_id, state, now))
        if len(self._transitions) > MAX_RETAINED_TRANSITIONS:
            del self._transitions[: len(self._transitions) - MAX_RETAINED_TRANSITIONS]

    async def _fail(
        self, job: Job, kind: FailureKind, message: str, detail: object | None = None
    ) -> None:
        """把任务落成失败，并带上原因。

        Args:
            job: 任务。
            kind: 失败大类。
            message: 一句话概述。
            detail: 结构化原始信息。`comfy_validation` 时这里是 `node_errors` 原文。

        Returns:
            无。
        """
        logger.warning("任务 %s 失败（%s）：%s", job.job_id, kind.value, message)
        await self._set_state(
            job,
            JobState.FAILED,
            failure_reason=FailureReason(kind=kind, message=message, detail=detail),
        )

    def _next_job_id(self, now: datetime) -> str:
        """生成形如 `job_20260923_0001` 的任务 id。

        ⚠️ 序号是**进程内**的，网关重启后从 0001 重来，
        所以同一天重启两次会产生重复 id。M1 状态不持久化、重启后旧 id 本来也查不到，
        所以现在没有实际影响 —— 但 M3 做持久化时必须先解决它，否则会撞主键。

        Args:
            now: 当前时刻。

        Returns:
            任务 id。
        """
        day = now.strftime("%Y%m%d")
        self._sequence_by_day[day] = self._sequence_by_day.get(day, 0) + 1
        return f"job_{day}_{self._sequence_by_day[day]:04d}"

    def _evict_old_jobs(self) -> None:
        """超过上限时丢掉最老的那些终态任务。

        只丢终态的 —— 还在跑的任务丢了就再也查不到它，而它仍然占着 GPU。

        Returns:
            无。
        """
        if len(self._jobs) <= MAX_RETAINED_JOBS:
            return
        finished = [j for j in self._jobs.values() if j.is_terminal]
        finished.sort(key=lambda j: j.created_at)
        for job in finished[: len(self._jobs) - MAX_RETAINED_JOBS]:
            del self._jobs[job.job_id]

    # ---- 内部：worker ----

    async def _worker(self) -> None:
        """串行消费队列。**整个网关只有这一个 worker，串行由此保证。**

        ⚠️ 单个任务出任何意外都不能让这个循环退出 —— worker 死了之后
        所有后续任务会永远停在 `queued`，而且没有任何报错。
        所以这里兜住一切异常，把它落成任务的 `failed`，循环继续。

        Returns:
            无（正常情况下不返回，被 cancel 时结束）。
        """
        assert self._queue is not None
        while True:
            job = await self._queue.get()
            try:
                await self._run_one(job)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 —— 见上面的说明，这里必须兜住
                logger.exception("任务 %s 的执行过程抛出了未预期的异常", job.job_id)
                if not job.is_terminal:
                    await self._fail(
                        job, FailureKind.INTERNAL, f"网关内部错误: {exc!r}"
                    )
            finally:
                self._queue.task_done()

    async def _run_one(self, job: Job) -> None:
        """把一个任务从排队推进到终态。

        流程说明：
            1. 取出时先看有没有被取消 —— 排队期间取消的任务在这里落终态；
            2. `preparing`：拉 `/object_info` 对图做形状预检（不占 GPU）。
               有违例就直接失败，**不往 ComfyUI 发** ——
               提交一次错图要付几分钟模型加载的代价；
            3. 提交前再看一次取消标记，这是「还没占用 GPU」的最后一个检查点；
            4. `submitted`：拿到 `prompt_id`；
            5. 轮询到结束，期间可能推进到 `running`；
            6. `postprocessing` → `done` / `failed`。

        Args:
            job: 要执行的任务。

        Returns:
            无。
        """
        if job.cancel_requested:
            await self._set_state(job, JobState.CANCELLED)
            return

        await self._set_state(job, JobState.PREPARING)

        try:
            if self._preflight:
                object_info = await self._comfy.object_info()
                violations = check_workflow(job.workflow, object_info)
                if violations:
                    await self._fail(
                        job,
                        FailureKind.COMFY_VALIDATION,
                        "提交前预检未通过",
                        detail={"violations": [str(v) for v in violations]},
                    )
                    return

            if job.cancel_requested:
                # 还没提交给 ComfyUI，取消是纯网关内部的事。
                await self._set_state(job, JobState.CANCELLED)
                return

            prompt_id = await self._comfy.submit(job.workflow, client_id=job.job_id)
        except ComfyValidationError as exc:
            # ⭐ VS-10：node_errors 必须原样带着，不要压成一句话。
            await self._fail(
                job, FailureKind.COMFY_VALIDATION, exc.message, detail=exc.node_errors
            )
            return
        except ComfyUnreachable as exc:
            await self._fail(job, FailureKind.COMFY_UNREACHABLE, str(exc))
            return

        await self._set_state(job, JobState.SUBMITTED, prompt_id=prompt_id)

        record = await self._poll_until_finished(job)
        if record is None:
            # 已经在轮询里落了终态（取消、超时或上游不可达）。
            return

        await self._set_state(job, JobState.POSTPROCESSING)

        if record.status == "success":
            await self._set_state(job, JobState.DONE, outputs=record.outputs)
            return

        kind = (
            FailureKind.OUT_OF_MEMORY
            if _looks_like_out_of_memory(record.messages)
            else FailureKind.INTERNAL
        )
        await self._fail(
            job,
            kind,
            f"ComfyUI 报告任务未成功（status={record.status}）",
            detail={"messages": record.messages},
        )

    async def _poll_until_finished(self, job: Job) -> JobRecord | None:
        """轮询 ComfyUI 直到任务结束。

        流程说明：
            1. 查 `/history` —— 有记录且已完成就结束轮询；
            2. 还没结束、且我们还认为它在排队时，查一次 `/queue`
               看它是不是已经开跑了（这是 M1 里 `running` 唯一的来源）；
            3. 处理取消与超时；
            4. 等一个轮询间隔，**但取消信号能立刻打断这次等待**。

        Args:
            job: 正在执行的任务。

        Returns:
            任务结束时的 `JobRecord`；如果任务是被取消、超时或因上游不可达
            而结束的，返回 `None`（那几种情况下终态已经在这里落好了）。
        """
        loop = asyncio.get_running_loop()
        deadline = None if self._timeout is None else loop.time() + self._timeout

        while True:
            try:
                record = await self._comfy.history(job.prompt_id)
            except ComfyUnreachable as exc:
                await self._fail(job, FailureKind.COMFY_UNREACHABLE, str(exc))
                return None

            if record is not None and record.finished:
                if job.cancel_requested and record.status != "success":
                    # 我们要求取消，而它确实没有成功 —— 这就是中断落地的样子。
                    await self._set_state(job, JobState.CANCELLED)
                    return None
                # ⚠️ 竞态：取消请求发出时它已经跑完了。
                # 产物是好的，**不要把一个已经存在的产物标成取消**。
                return record

            if job.state is JobState.SUBMITTED:
                try:
                    snapshot = await self._comfy.queue()
                except ComfyUnreachable as exc:
                    await self._fail(job, FailureKind.COMFY_UNREACHABLE, str(exc))
                    return None
                if job.prompt_id in snapshot.running:
                    await self._set_state(job, JobState.RUNNING)

            if deadline is not None and loop.time() > deadline:
                await self._interrupt_if_ours(job)
                await self._fail(
                    job,
                    FailureKind.TIMEOUT,
                    f"任务超过 {self._timeout} 秒仍未结束",
                    detail={"prompt_id": job.prompt_id},
                )
                return None

            # 取消信号能立刻打断这次等待，所以取消的响应时间与轮询间隔无关。
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(
                    job.cancel_event.wait(), timeout=self._poll_interval
                )
            job.cancel_event.clear()

    async def _interrupt_if_ours(self, job: Job) -> None:
        """确认 ComfyUI 正在跑的就是这个任务，才发中断请求。

        ⚠️ **ComfyUI 的中断接口没有参数，打掉的是此刻正在跑的那一个。**
        而这台服务器上 ComfyUI 仍监听 `0.0.0.0`，用户自己也在用它的网页界面 ——
        不先确认就中断，可能打掉的是用户手工提交的生成。

        三种情形：
            - 它正在跑 ⇒ 发中断；
            - 它还在 ComfyUI 队列里排着 ⇒ 从队列里删掉它，**不能发中断**
              （那会打掉排在它前面的、别人的任务）；
            - 两个队列里都没有 ⇒ 它已经跑完了，下一轮轮询会拿到记录，
              交给那里判定。

        Args:
            job: 要中断的任务。

        Returns:
            无。上游不可达时**静默返回** —— 连不上就发不了中断，
            而任务的失败会由轮询循环翻译成 `comfy_unreachable`，
            在这里再报一次只会掩盖那个更准确的结论。
        """
        try:
            snapshot = await self._comfy.queue()
        except ComfyUnreachable:
            return

        if job.prompt_id in snapshot.running:
            with contextlib.suppress(ComfyUnreachable):
                await self._comfy.interrupt()
        elif job.prompt_id in snapshot.pending:
            with contextlib.suppress(ComfyUnreachable):
                await self._comfy.delete_queued(job.prompt_id)
