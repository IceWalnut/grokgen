"""任务状态机与串行队列的判据（VS-9、VS-10、VS-17、VS-18）。

全部跑在开发机上，用 `FakeComfyClient` 顶掉 ComfyUI，**不占 GPU、不连服务器**。

⚠️ **这里直接对 `JobManager` 写裸 async 测试，不走 `TestClient`。**
`TestClient` 在自己的事件循环线程里跑应用，跨线程读 `job.state` 既不好写也不可靠；
而 `wait_for` 这种同步原语必须和被等待的对象在同一个事件循环里 await。
走 HTTP 的那部分判据在 `test_jobs_api.py`。

⚠️ **这里没有任何 `asyncio.sleep(0.1)` 式的等待。**
所有同步都靠 `manager.wait_for(...)` 等**条件**，不等时间 ——
等时间的测试要么慢要么 flaky，而且两者都会随机器负载变化。
`wait_for` 的 timeout 是兜底：超了说明真卡住了，不是抖动。
"""

from datetime import datetime, timezone

import pytest

from app.comfy.client import ComfyValidationError
from app.comfy.fake_client import FakeComfyClient
from app.comfy.workflows.h3_video import build_workflow
from app.core.jobs import (
    GPU_OCCUPYING_STATES,
    JobAlreadyFinished,
    JobManager,
    JobNotFound,
    JobState,
    Transition,
    peak_gpu_occupancy,
)
from app.models.video_job import PromptParts, VideoJobRequest, VideoMode

# ComfyUI 拒绝一张图时返回的结构。形状取自契约 §2 的示例。
NODE_ERRORS = {
    "2": {
        "errors": [
            {"type": "required_input_missing", "details": "channels"},
        ]
    }
}


def t2va_request() -> VideoJobRequest:
    """造一个最简单的请求：纯文本生视频，不需要首帧图。"""
    return VideoJobRequest(
        mode=VideoMode.T2VA,
        prompt=PromptParts(description="a cat walking on a table"),
        duration_seconds=5.0,
    )


async def make_manager(comfy: FakeComfyClient | None = None) -> tuple[JobManager, FakeComfyClient]:
    """造一个已启动的任务管理器，配受控模式的替身。

    Args:
        comfy: 自带的替身；不给就建一个受控模式的。

    Returns:
        `(manager, comfy)`。轮询间隔 0、关掉预检 ——
        预检要拉 `/object_info`，那是 `test_workflow_validation.py` 的事。
    """
    comfy = comfy or FakeComfyClient(gated=True)
    manager = JobManager(comfy, poll_interval_seconds=0, preflight=False)
    await manager.start()
    return manager, comfy


def submit(manager: JobManager):
    """归一化 + 建图 + 登记，模拟路由层做的那一套。"""
    request = t2va_request()
    workflow, normalized = build_workflow(request)
    return manager.submit(request, workflow, normalized)


# ============ VS-9：串行队列 ============


async def test_three_jobs_never_occupy_the_gpu_at_the_same_time():
    """VS-9：连续提交 3 个任务，任何时刻只有一个处于 submitted/running。

    三条互相独立的断言，抓的是不同的失败模式：
      ① 回放转移日志算峰值 —— 抓「状态重叠」；
      ② 替身在提交边界上自己守的那道 —— 抓「worker 被并发起了两份」，
         而且**不依赖网关自己的记账**，网关的日志写错了它照样会炸；
      ③ 提交顺序 —— 这是**正向的数**，见下。
    """
    manager, comfy = await make_manager()
    jobs = [submit(manager) for _ in range(3)]

    # 刚提交完，后两个必须还在排队 —— 串行队列不该让它们抢跑。
    assert [j.state for j in jobs[1:]] == [JobState.QUEUED, JobState.QUEUED]

    for job in jobs:
        await manager.wait_for(job.job_id, {JobState.SUBMITTED})
        # 此刻其余任务只能是「还没轮到」或「已经跑完」，不能也占着 GPU。
        others = [o for o in jobs if o is not job]
        assert all(
            o.state in {JobState.QUEUED, JobState.DONE} for o in others
        ), f"{job.job_id} 占着 GPU 时，其余任务的状态是 {[o.state for o in others]}"

        comfy.begin_running(job.prompt_id)
        await manager.wait_for(job.job_id, {JobState.RUNNING})
        comfy.complete(job.prompt_id)
        await manager.wait_for(job.job_id, {JobState.DONE})

    # ① 完备回放：状态是分段常数的，逐个转移检查就覆盖了整段历史。
    assert peak_gpu_occupancy(manager.transitions) == 1

    # ② 替身在边界上的独立证据。
    assert comfy.max_outstanding_submits == 1

    # ③ ⚠️ 正向的数：上面两条都是负向的 ——
    # 一个三个任务全卡在 queued、一个都没跑的队列也能让它们通过。
    # 所以必须断言三个 submitted 转移确实都发生了，且顺序就是提交顺序。
    submitted_order = [
        t.job_id for t in manager.transitions if t.state is JobState.SUBMITTED
    ]
    assert submitted_order == [j.job_id for j in jobs]
    assert all(j.state is JobState.DONE for j in jobs)

    await manager.aclose()


def test_peak_gpu_occupancy_actually_catches_an_overlap():
    """守住 VS-9 的判据函数本身：喂一条重叠的日志，它必须报 2。

    ⚠️ 没有这条，一个恒返回 0 的 `peak_gpu_occupancy` 也能让 VS-9 变绿 ——
    那就是一条永远不会失败的判据，而「一条从未失败过的判据不是判据」
    （`Docs/Validation.md` §4.1）。
    """
    now = datetime.now(timezone.utc)
    overlapping = [
        Transition(1, "job_a", JobState.SUBMITTED, now),
        Transition(2, "job_b", JobState.SUBMITTED, now),
        Transition(3, "job_a", JobState.DONE, now),
    ]
    assert peak_gpu_occupancy(overlapping) == 2

    serial = [
        Transition(1, "job_a", JobState.SUBMITTED, now),
        Transition(2, "job_a", JobState.DONE, now),
        Transition(3, "job_b", JobState.SUBMITTED, now),
    ]
    assert peak_gpu_occupancy(serial) == 1


def test_gpu_occupying_states_are_exactly_submitted_and_running():
    """钉住 VS-9 判据里「占着 GPU」的定义。

    这个集合被判据函数和测试共用，改动它等于改判据 ——
    所以它变了必须是一次显式的、看得见的决定。
    """
    assert GPU_OCCUPYING_STATES == {JobState.SUBMITTED, JobState.RUNNING}


# ============ VS-10：失败原因要带得回来 ============


async def test_rejected_workflow_keeps_the_node_errors_verbatim():
    """VS-10：ComfyUI 拒绝 workflow 时，node_errors 原文要出现在 failure_reason 里。

    ⚠️ 断言的是**逐字段相等**，不是「detail 不为空」——
    把 node_errors 压成一句字符串也能让「不为空」通过，
    而那恰恰是这条判据要防的事（排障时只有结构化原文有用）。
    """
    comfy = FakeComfyClient(gated=True, reject_with=NODE_ERRORS)
    manager, _ = await make_manager(comfy)

    job = submit(manager)
    await manager.wait_for(job.job_id, {JobState.FAILED})

    assert job.failure_reason is not None
    assert job.failure_reason.kind.value == "comfy_validation"
    assert job.failure_reason.detail == NODE_ERRORS
    # 正向地确认那条具体信息还在，没有在传递过程中被摊平。
    assert job.failure_reason.detail["2"]["errors"][0]["details"] == "channels"

    await manager.aclose()


async def test_unreachable_comfy_becomes_its_own_failure_kind():
    """ComfyUI 连不上要和「图写错了」分开报，App 的提示完全不同。"""
    comfy = FakeComfyClient(gated=True, unreachable_reason="connection refused")
    manager, _ = await make_manager(comfy)

    job = submit(manager)
    await manager.wait_for(job.job_id, {JobState.FAILED})

    assert job.failure_reason.kind.value == "comfy_unreachable"
    assert "connection refused" in job.failure_reason.message

    await manager.aclose()


async def test_out_of_memory_is_classified_apart_from_generic_failures():
    """显存不足要单独分类 —— 它是这个项目最该被明确提示的失败（需求 F7）。"""
    manager, comfy = await make_manager()
    job = submit(manager)
    await manager.wait_for(job.job_id, {JobState.SUBMITTED})
    comfy.fail(
        job.prompt_id,
        messages=[["execution_error", {"exception_message": "CUDA out of memory"}]],
    )
    await manager.wait_for(job.job_id, {JobState.FAILED})

    assert job.failure_reason.kind.value == "out_of_memory"
    # ComfyUI 的原始消息也要带着，分类只是额外加的一层。
    assert "CUDA out of memory" in str(job.failure_reason.detail)

    await manager.aclose()


async def test_generic_comfy_failure_keeps_the_messages():
    """认不出类别的失败落到 internal，但消息原文同样不能丢。"""
    manager, comfy = await make_manager()
    job = submit(manager)
    await manager.wait_for(job.job_id, {JobState.SUBMITTED})
    comfy.fail(job.prompt_id, messages=[["execution_error", {"node": "sampler"}]])
    await manager.wait_for(job.job_id, {JobState.FAILED})

    assert job.failure_reason.kind.value == "internal"
    assert "sampler" in str(job.failure_reason.detail)

    await manager.aclose()


# ============ VS-17：取消的三种情形 ============


async def test_cancelling_a_queued_job_never_touches_comfyui():
    """VS-17 情形一：还在网关队列里的任务，取消是纯内部的事。

    ⚠️ 正向断言「一次 ComfyUI 调用都没发生」——
    只断言状态变成 cancelled 的话，一个「先通知 ComfyUI 再取消」的
    错误实现也能通过，而那会白白打断别人的任务。
    """
    manager, comfy = await make_manager()

    first = submit(manager)
    second = submit(manager)
    # 让第一个占住 worker，第二个才会稳定地停在队列里。
    await manager.wait_for(first.job_id, {JobState.SUBMITTED})

    await manager.cancel(second.job_id)
    assert second.state is JobState.CANCELLED
    assert comfy.interrupts == 0
    assert comfy.deleted_from_queue == []
    # 它从头到尾没被提交过。
    assert second.prompt_id is None

    comfy.complete(first.prompt_id)
    await manager.wait_for(first.job_id, {JobState.DONE})
    await manager.aclose()


async def test_cancelling_a_running_job_interrupts_it():
    """VS-17 情形二：正在跑的任务，取消要真的发中断，终态是 cancelled。"""
    manager, comfy = await make_manager()
    job = submit(manager)
    await manager.wait_for(job.job_id, {JobState.SUBMITTED})
    comfy.begin_running(job.prompt_id)
    await manager.wait_for(job.job_id, {JobState.RUNNING})

    await manager.cancel(job.job_id)
    await manager.wait_for(job.job_id, {JobState.CANCELLED})

    assert comfy.interrupts == 1
    await manager.aclose()


async def test_cancelling_does_not_interrupt_someone_elses_running_job():
    """VS-17 情形二的反面：正在跑的不是我们要取消的那个，就不许发中断。

    ⚠️ **这是这一组里最重要的一条。** ComfyUI 的中断接口没有参数，
    打掉的是此刻正在跑的那一个。而服务器上 ComfyUI 仍监听 0.0.0.0，
    用户自己也在用网页界面提交任务 —— 不加判断就中断，会打掉用户的活。

    构造方式：让我们的任务停在「已提交但还没开始跑」，
    替身此时报告「正在跑的是另一个 prompt_id」（模拟用户手工提交的那个）。
    """
    manager, comfy = await make_manager()
    job = submit(manager)
    await manager.wait_for(job.job_id, {JobState.SUBMITTED})

    # 替身里塞一个「别人的任务正在跑」，我们的那个还排在后面。
    comfy._prompts["someone-elses-prompt"] = type(
        comfy._prompts[job.prompt_id]
    )(workflow={}, phase="running")

    await manager.cancel(job.job_id)

    # 我们的任务在 pending 里，所以应当走「从队列删掉」而不是「中断」。
    assert comfy.interrupts == 0, "打掉了别人正在跑的任务"
    assert comfy.deleted_from_queue == [job.prompt_id]

    await manager.aclose()


async def test_cancelling_a_finished_job_is_rejected():
    """VS-17 情形三：已经结束的任务不能取消，契约要求 409。"""
    manager, comfy = await make_manager()
    job = submit(manager)
    await manager.wait_for(job.job_id, {JobState.SUBMITTED})
    comfy.complete(job.prompt_id)
    await manager.wait_for(job.job_id, {JobState.DONE})

    with pytest.raises(JobAlreadyFinished):
        await manager.cancel(job.job_id)

    await manager.aclose()


async def test_a_job_that_finishes_while_being_cancelled_stays_done():
    """取消与完成撞在一起时，已经产出的视频不能被扔掉。

    ⚠️ 用户点取消的同一瞬间任务跑完了 —— 产物是好的，
    把它标成 cancelled 等于白付那几分钟 GPU，而且用户会以为视频没了。
    """
    manager, comfy = await make_manager()
    job = submit(manager)
    await manager.wait_for(job.job_id, {JobState.SUBMITTED})

    # 先让它成功结束，再请求取消 —— 模拟「取消晚了一步」。
    comfy.complete(job.prompt_id)
    await manager.wait_for(job.job_id, {JobState.DONE})

    with pytest.raises(JobAlreadyFinished):
        await manager.cancel(job.job_id)
    assert job.state is JobState.DONE
    assert job.outputs, "产物被弄丢了"

    await manager.aclose()


async def test_cancelling_an_unknown_job_is_not_found():
    """取消一个不存在的任务要明确报找不到，路由层翻译成 404。"""
    manager, _ = await make_manager()
    with pytest.raises(JobNotFound):
        await manager.cancel("job_20260101_9999")
    await manager.aclose()


# ============ VS-18：seed 只能算一次 ============


def test_the_reported_seed_is_the_one_actually_sent_to_comfyui():
    """VS-18：回报给 App 的 seed 与 workflow 里实际用的 seed 必须是同一个。

    ⚠️ **这条守的是一个不会报错的错误。** `normalize()` 在用户没填 seed 时
    会随机生成一个，而 `build_workflow()` 内部会调 `normalize()`。
    如果提交时算一次回给 App、执行时再算一次喂给 ComfyUI，
    两个 seed 不一样 —— 任务照样成功，视频照样出来，
    只是用户拿着回报的 seed **永远复现不出那个视频**。

    所以整条链路只归一化一次。这条测试钉的就是「同一次产出的这两个值是一致的」。
    """
    request = t2va_request()
    assert request.seed is None, "这条测试的前提是用户没填 seed"

    workflow, normalized = build_workflow(request)

    assert workflow["noise"]["inputs"]["noise_seed"] == normalized.seed


def test_two_normalizations_of_the_same_request_would_disagree():
    """证明上一条守的那个风险是真的，不是想象出来的。

    ⚠️ 这条测试断言的是**问题存在**，不是功能正确：
    对同一个没填 seed 的请求建两次图，两个 seed 就是不同的。
    没有它，上一条测试看起来只是在验证一个显然成立的恒等式，
    下一个人会觉得「只归一化一次」这个约束是多余的，随手把它改回去。
    """
    request = t2va_request()
    _, first = build_workflow(request)
    _, second = build_workflow(request)

    assert first.seed != second.seed


# ============ 状态机本身 ============


async def test_terminal_states_never_change_again():
    """终态不能再被推进 —— 否则「任务结束了」这件事就不可信了。"""
    manager, comfy = await make_manager()
    job = submit(manager)
    await manager.wait_for(job.job_id, {JobState.SUBMITTED})
    comfy.complete(job.prompt_id)
    await manager.wait_for(job.job_id, {JobState.DONE})

    from app.core.jobs import ALLOWED_TRANSITIONS

    for terminal in (JobState.DONE, JobState.FAILED, JobState.CANCELLED):
        assert ALLOWED_TRANSITIONS[terminal] == frozenset()

    await manager.aclose()


async def test_every_non_terminal_state_can_fail():
    """每个非终态都要能转到 failed —— 失败可能发生在任何一步（需求：失败必须可见）。"""
    from app.core.jobs import ALLOWED_TRANSITIONS, TERMINAL_STATES

    for state, allowed in ALLOWED_TRANSITIONS.items():
        if state in TERMINAL_STATES:
            continue
        assert JobState.FAILED in allowed, f"{state.value} 无法转到 failed"


async def test_submitted_can_skip_running_and_go_straight_to_postprocessing():
    """短任务可能在两次轮询之间跑完，我们根本没机会看到 running。

    ⚠️ 不允许这条直跳的话，正常路径上就会抛非法转移 ——
    这不是理论风险：M1 没有 WebSocket，观察本来就是离散的。
    """
    manager, comfy = await make_manager()
    job = submit(manager)
    await manager.wait_for(job.job_id, {JobState.SUBMITTED})

    # 直接完成，中间不经过 running。
    comfy.complete(job.prompt_id)
    await manager.wait_for(job.job_id, {JobState.DONE})

    seen = [t.state for t in manager.transitions if t.job_id == job.job_id]
    assert JobState.RUNNING not in seen
    assert seen[-1] is JobState.DONE

    await manager.aclose()


async def test_preflight_violations_never_reach_comfyui():
    """预检拦下的图不能被提交出去。

    ⚠️ 正向断言 `comfy.submitted == []` ——
    只断言任务失败的话，一个「提交了、被 ComfyUI 拒了、才失败」的实现
    也能通过，而那正是预检要省下的那几分钟模型加载开销（M1R3 的结论）。
    """
    # 给一个空的 object_info：图里每个节点的类型都会被判成「不存在」。
    comfy = FakeComfyClient(gated=True, object_info={})
    manager = JobManager(comfy, poll_interval_seconds=0, preflight=True)
    await manager.start()

    job = submit(manager)
    await manager.wait_for(job.job_id, {JobState.FAILED})

    assert comfy.submitted == []
    assert job.failure_reason.kind.value == "comfy_validation"
    assert job.prompt_id is None

    await manager.aclose()


async def test_timeout_fails_the_job_and_stops_it_on_comfyui():
    """超时要落成 failed(timeout)，并且把 ComfyUI 那边也停掉。

    ⚠️ 只把网关侧标成失败是不够的 —— ComfyUI 那边还在跑就还占着显存，
    下一个任务会因此排在一个「网关认为已经结束」的任务后面。
    """
    manager_comfy = FakeComfyClient(gated=True)
    manager = JobManager(
        manager_comfy, poll_interval_seconds=0, timeout_seconds=0.01, preflight=False
    )
    await manager.start()

    job = submit(manager)
    await manager.wait_for(job.job_id, {JobState.SUBMITTED})
    manager_comfy.begin_running(job.prompt_id)

    await manager.wait_for(job.job_id, {JobState.FAILED})
    assert job.failure_reason.kind.value == "timeout"
    assert job.failure_reason.detail["prompt_id"] == job.prompt_id
    assert manager_comfy.interrupts == 1

    await manager.aclose()


async def test_no_timeout_by_default_lets_a_slow_job_finish():
    """默认不超时：一个跑得久的任务不会被网关杀掉。

    ⚠️ 这条对应配置里那个「默认留空」的决定。
    现有耗时读数全是热启动的，冷启动含模型加载没测过 ——
    在没有依据的情况下拍一个秒数，误杀一次就是几分钟 GPU。
    """
    manager, comfy = await make_manager()
    assert manager._timeout is None

    job = submit(manager)
    await manager.wait_for(job.job_id, {JobState.SUBMITTED})
    comfy.begin_running(job.prompt_id)
    await manager.wait_for(job.job_id, {JobState.RUNNING})

    # 让轮询转很多圈，确认没有人来杀它。
    for _ in range(50):
        await manager.wait_for(job.job_id, {JobState.RUNNING}, timeout=1.0)
    assert job.state is JobState.RUNNING

    comfy.complete(job.prompt_id)
    await manager.wait_for(job.job_id, {JobState.DONE})
    await manager.aclose()


async def test_jobs_are_listed_newest_first():
    """列表按创建时间倒序 —— 队列页要先看到刚提交的那个。"""
    manager, comfy = await make_manager()
    first = submit(manager)
    second = submit(manager)
    third = submit(manager)

    listed = manager.list_jobs()
    assert [j.job_id for j in listed] == [third.job_id, second.job_id, first.job_id]
    assert manager.count() == 3

    await manager.aclose()


async def test_closing_the_manager_rejects_new_submissions():
    """关停后不再收新任务，路由层把它翻译成 503。"""
    from app.core.jobs import ManagerClosing

    manager, _ = await make_manager()
    await manager.aclose()

    with pytest.raises(ManagerClosing):
        submit(manager)
