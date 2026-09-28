"""任务管理器 × 进度 ws：回放真实录制、断线、从未连上（VS-23、VS-24、VS-26）。

替身 `FakeComfyClient` 在这里**原样回放**服务器上录下的事件序列，含插件广播、
重复快照、以及重连后那条不带 `prompt_id` 的 `executing`（`fake_client.replay_recording`）。

⚠️ 与 `test_jobs.py` 一样**不 sleep**：全部靠 `manager.wait_until(...)` 等条件。
"""

import logging
from pathlib import Path

import pytest

from app.comfy.fake_client import FakeComfyClient
from app.comfy.workflows.h3_video import build_workflow
from app.core import jobs as jobs_module
from app.core.jobs import JobManager, JobState
from app.core.progress import Stage
from app.models.video_job import PromptParts, VideoJobRequest, VideoMode

RECORDINGS = Path(__file__).parent / "data" / "comfy_ws"
MODEL_NOT_IN_VRAM = RECORDINGS / "t2va_model_not_in_vram.jsonl"
DISCONNECT_AFTER_STEP3 = RECORDINGS / "t2va_disconnect_after_step3.jsonl"


async def start_manager(comfy: FakeComfyClient) -> JobManager:
    """已启动的管理器：轮询间隔 0、关预检、重连不等待。"""
    manager = JobManager(
        comfy,
        poll_interval_seconds=0,
        preflight=False,
        event_reconnect_initial_seconds=0,
        event_reconnect_max_seconds=0,
    )
    await manager.start()
    return manager


def submit_recorded_shape(manager: JobManager):
    """提交一个与录制同形状的任务（文生视频 + Turbo），节点 id 才对得上录制里的。"""
    request = VideoJobRequest(
        mode=VideoMode.T2VA, prompt=PromptParts(description="x"), duration_seconds=5.0, turbo=True
    )
    workflow, normalized = build_workflow(request)
    return manager.submit(request, workflow, normalized)


def record_readings(manager: JobManager, job) -> list[tuple]:
    """每次管理器通知「有变化」时，记下这个任务的 `(stage, progress)`，并去掉连续重复。

    ⚠️ 挂在 `_notify_changed` 上而不是轮询 `job.stage`：替身的事件是一口气吐出来的，
    中间不让出事件循环，从外面轮询会漏掉中间值。
    """
    readings: list[tuple] = []
    original = manager._notify_changed

    async def notify_and_record():
        current = (job.stage, job.progress)
        if not readings or readings[-1] != current:
            readings.append(current)
        await original()

    manager._notify_changed = notify_and_record
    return readings


async def wait_until_replayed(manager: JobManager, comfy: FakeComfyClient, job, readings: list[tuple]) -> None:
    """等一整份录制回放完。

    ⚠️ 不能等「`stage` 变成 `encoding`」：替身一口气吐完整份录制、中间不让出事件循环，
    等条件被检查时最后那条 `execution_success` 早已把 `stage` 清空了。
    所以等的是「剧本吐完、记录里出现过编码阶段、此刻已经清空」。
    """
    await manager.wait_until(
        lambda: comfy.pending_events == 0
        and (Stage.ENCODING, None) in readings
        and job.stage is None
    )


async def running_job(manager: JobManager, comfy: FakeComfyClient):
    """提交一个任务并让它在替身里进入「正在跑」，返回 `(job, prompt_id)`。"""
    job = submit_recorded_shape(manager)
    await manager.wait_for(job.job_id, {JobState.SUBMITTED})
    comfy.begin_running(job.prompt_id)
    await manager.wait_for(job.job_id, {JobState.RUNNING})
    return job, job.prompt_id


# ============ VS-23：真实录制经管理器回放 ============


async def test_recorded_events_drive_stage_and_progress_then_clear_on_done():
    """VS-23：回放「模型不在显存」那份录制 → 任务依次经过加载、8 步采样、解码、编码；
    任务结束后 `stage` / `progress` 置空。"""
    comfy = FakeComfyClient(gated=True)
    manager = await start_manager(comfy)
    job, prompt_id = await running_job(manager, comfy)
    readings = record_readings(manager, job)

    comfy.replay_recording(MODEL_NOT_IN_VRAM, prompt_id)
    await wait_until_replayed(manager, comfy, job, readings)
    comfy.complete(prompt_id)
    await manager.wait_for(job.job_id, {JobState.DONE})

    assert (Stage.LOADING_MODEL, None) in readings
    assert [p for s, p in readings if s is Stage.SAMPLING] == [step / 8 for step in range(1, 9)]
    assert (Stage.DECODING_VIDEO, None) in readings
    assert readings.index((Stage.LOADING_MODEL, None)) < readings.index((Stage.SAMPLING, 0.125))
    assert (job.stage, job.progress) == (None, None)
    await manager.aclose()


async def test_submit_and_events_use_the_same_client_id():
    """ComfyUI 只把用这个 client_id 提交的任务的进度发给这条连接 —— 两边不一致就一条进度也收不到，
    而且不报错（任务照样跑完）。"""
    comfy = FakeComfyClient(gated=True)
    manager = await start_manager(comfy)
    job, _ = await running_job(manager, comfy)

    assert comfy.event_client_ids, "进度 ws 一次都没连"
    assert set(comfy.submit_client_ids) == set(comfy.event_client_ids)
    assert comfy.submit_client_ids[0] != job.job_id  # 是网关共用的那一个，不是每个任务一个
    await manager.aclose()


# ============ VS-24：ws 断开，任务照常跑完，进度变回空 ============


async def test_disconnect_mid_sampling_clears_progress_and_the_job_still_finishes(caplog):
    """VS-24：回放「第 3 步后断开再重连」那份真实录制。

    断言四件事：
      ① 断开时 `stage` / `progress` 变回空；
      ② ⭐ 重连后 ComfyUI 补发的、不带 `prompt_id` 的 `executing sampler` **没有**让任务倒退回
         「正在加载模型」（录制里那时已经采样到 3/8）；
      ③ 下一个 `progress` 来了之后恢复成「采样中 50%」；
      ④ 任务照常 `done`，日志里没有 ERROR。
    """
    comfy = FakeComfyClient(gated=True)
    manager = await start_manager(comfy)
    job, prompt_id = await running_job(manager, comfy)
    readings = record_readings(manager, job)

    with caplog.at_level(logging.WARNING):
        comfy.replay_recording(DISCONNECT_AFTER_STEP3, prompt_id)
        await wait_until_replayed(manager, comfy, job, readings)
        comfy.complete(prompt_id)
        await manager.wait_for(job.job_id, {JobState.DONE})

    step3 = readings.index((Stage.SAMPLING, 3 / 8))
    step4 = readings.index((Stage.SAMPLING, 4 / 8))
    assert readings[step3 + 1] == (None, None), f"断开后没有置空：{readings[step3:step4 + 1]}"
    assert (Stage.LOADING_MODEL, None) not in readings[step3:], f"重连后倒退回了加载模型：{readings[step3:]}"
    assert readings[step3 + 2] == (Stage.SAMPLING, 4 / 8)
    assert len(comfy.event_client_ids) == 2, "断开之后没有重连"
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    await manager.aclose()


async def test_job_finishes_when_the_ws_never_connects(caplog):
    """VS-24：ws 从一开始就连不上（HTTP 照常）⇒ 任务照常 `done`，`stage` 始终为空，
    管理器一直在重连，断线 warning 只记一条而不是每次重连都刷。"""
    comfy = FakeComfyClient(gated=True, events_unreachable=True)
    manager = await start_manager(comfy)

    with caplog.at_level(logging.WARNING, logger="app.core.jobs"):
        job, prompt_id = await running_job(manager, comfy)
        await manager.wait_until(lambda: manager.event_disconnects >= 3)
        assert (job.stage, job.progress) == (None, None)
        comfy.complete(prompt_id)
        await manager.wait_for(job.job_id, {JobState.DONE})

    warnings = [r for r in caplog.records if "进度 ws 断开" in r.getMessage()]
    assert len(warnings) == 1, [r.getMessage() for r in warnings]
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    await manager.aclose()


async def test_an_unexpected_error_while_applying_an_event_does_not_kill_the_event_loop(monkeypatch, caplog):
    """事件循环里出任何意外都按断线处理、重连后继续 —— 这个循环死了，进度就再也不会有，
    而且**没有任何报错**（任务照样跑完）。"""
    comfy = FakeComfyClient(gated=True)
    manager = await start_manager(comfy)
    job, prompt_id = await running_job(manager, comfy)

    real_reading_after_event = jobs_module.reading_after_event
    calls = {"n": 0}

    def explode_once(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("植入的意外")
        return real_reading_after_event(*args, **kwargs)

    monkeypatch.setattr(jobs_module, "reading_after_event", explode_once)
    with caplog.at_level(logging.ERROR, logger="app.core.jobs"):
        comfy.push_event("executing", {"prompt_id": prompt_id, "node": "sampler"})
        await manager.wait_until(lambda: manager.event_disconnects >= 1)
        comfy.push_event("progress", {"prompt_id": prompt_id, "node": "sampler", "value": 2, "max": 8})
        await manager.wait_until(lambda: job.stage is Stage.SAMPLING)

    assert job.progress == 0.25
    assert any("未预期的异常" in r.getMessage() for r in caplog.records)
    await manager.aclose()


# ============ VS-26：不属于自己任务的事件不改动任何任务 ============


async def test_events_for_other_prompts_or_without_prompt_id_change_nothing():
    """VS-26：别人的 `prompt_id`（用户在 ComfyUI 网页上提交的）、没有 `prompt_id` 的事件，
    都不改动任务；之后属于它的事件照常生效（证明事件循环没被这些消息弄停）。"""
    comfy = FakeComfyClient(gated=True)
    manager = await start_manager(comfy)
    job, prompt_id = await running_job(manager, comfy)

    comfy.push_event("progress", {"prompt_id": "someone-else", "node": "sampler", "value": 5, "max": 8})
    comfy.push_event("executing", {"node": "sampler"})  # 重连后补发的那种
    comfy.push_event("dasiwa.system_monitor", {"cpu_percent": 1.0})
    comfy.push_event("progress", {"prompt_id": prompt_id, "node": "sampler", "value": 1, "max": 8})
    await manager.wait_until(lambda: job.stage is Stage.SAMPLING)

    assert job.progress == 0.125
    await manager.aclose()


async def test_events_after_the_job_left_the_gpu_are_ignored():
    """任务进了 `postprocessing` 之后迟到的事件不再改它 —— 否则「收尾中」会显示一个过期的进度。"""
    comfy = FakeComfyClient(gated=True)
    manager = await start_manager(comfy)
    job, prompt_id = await running_job(manager, comfy)
    comfy.complete(prompt_id)
    await manager.wait_for(job.job_id, {JobState.DONE})

    comfy.push_event("progress", {"prompt_id": prompt_id, "node": "sampler", "value": 8, "max": 8})
    # 迟到的事件不改任何东西，也就不会叫醒等待方；末尾放一个断线标记 ——
    # 断线处理一定会通知，而且排在前面那条事件之后，等到它就说明前面那条已经处理过了。
    comfy.push_disconnect()
    await manager.wait_until(lambda: manager.event_disconnects >= 1)

    assert (job.stage, job.progress) == (None, None)
    await manager.aclose()


@pytest.mark.parametrize("terminal", ["fail", "interrupt"])
async def test_stage_is_cleared_when_the_job_ends_without_success(terminal):
    comfy = FakeComfyClient(gated=True)
    manager = await start_manager(comfy)
    job, prompt_id = await running_job(manager, comfy)
    comfy.push_event("progress", {"prompt_id": prompt_id, "node": "sampler", "value": 4, "max": 8})
    await manager.wait_until(lambda: job.stage is Stage.SAMPLING)

    if terminal == "fail":
        comfy.fail(prompt_id)
        await manager.wait_for(job.job_id, {JobState.FAILED})
    else:
        await manager.cancel(job.job_id)
        await manager.wait_for(job.job_id, {JobState.CANCELLED})

    assert (job.stage, job.progress) == (None, None)
    await manager.aclose()
