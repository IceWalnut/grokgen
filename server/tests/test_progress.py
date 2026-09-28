"""ws 事件 → `stage` / `progress` 的映射（VS-23、VS-26）。

⭐ **判据的输入是真实录制**（`tests/data/comfy_ws/`，2026-09-28 在服务器上录的，
录法见 `scripts/lib/record_comfy_ws.py`），不是照文档手写的理想序列。
手写的只有 VS-26 那几条「构造出来的坏值」，每一条都注明它为什么构造得出来。
"""

import json
from pathlib import Path

import pytest

from app.comfy.client import ComfyEvent
from app.comfy.workflows.h3_video import build_workflow
from app.core.progress import (
    UNKNOWN_STAGE,
    Stage,
    StageReading,
    event_prompt_id,
    reading_after_event,
)
from app.models.video_job import PromptParts, VideoJobRequest, VideoMode

RECORDINGS = Path(__file__).parent / "data" / "comfy_ws"
MODEL_NOT_IN_VRAM = RECORDINGS / "t2va_model_not_in_vram.jsonl"
MODEL_IN_VRAM = RECORDINGS / "t2va_model_in_vram.jsonl"


def recorded_workflow() -> dict:
    """与录制时同一形状的图：文生视频、Turbo（录制脚本也是这么拼的）。"""
    request = VideoJobRequest(
        mode=VideoMode.T2VA, prompt=PromptParts(description="x"), duration_seconds=5.0, turbo=True
    )
    workflow, _ = build_workflow(request)
    return workflow


def load_recording(path: Path) -> tuple[str, list[ComfyEvent]]:
    """读一份录制，返回 `(录制时的 prompt_id, 全部文本事件)`。"""
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    prompt_id = next(
        r["data"]["prompt_id"] for r in records if r["kind"] == "note" and r["data"].get("action") == "submitted"
    )
    events = [
        ComfyEvent(type=r["data"]["type"], data=r["data"].get("data") or {})
        for r in records
        if r["kind"] == "text"
    ]
    return prompt_id, events


def replay(path: Path) -> list[StageReading]:
    """按网关的过滤规则回放一份录制，返回每条事件之后的读数（去掉连续重复）。"""
    prompt_id, events = load_recording(path)
    workflow = recorded_workflow()
    reading = UNKNOWN_STAGE
    readings: list[StageReading] = []
    for event in events:
        if event_prompt_id(event) != prompt_id:
            continue
        reading = reading_after_event(event, workflow, reading)
        if not readings or readings[-1] != reading:
            readings.append(reading)
    return readings


def stage_sequence(readings: list[StageReading]) -> list[Stage | None]:
    """把读数压成阶段序列（相邻相同的合并）。"""
    stages: list[Stage | None] = []
    for reading in readings:
        if not stages or stages[-1] != reading.stage:
            stages.append(reading.stage)
    return stages


# ============ VS-23：真实录制回放 ============


@pytest.mark.parametrize("path", [MODEL_NOT_IN_VRAM, MODEL_IN_VRAM], ids=lambda p: p.stem)
def test_recorded_run_goes_through_every_stage_in_order(path):
    """VS-23：阶段按 加载 → 采样 → 解码音频 → 解码视频 → 编码 → 空 的顺序**确实出现**。

    开头那个 `None` 是 `execution_start` / `execution_cached` 之后、第一个节点开始执行之前。

    ⭐ 这是正向断言（`Docs/Validation.md` §3 规则 ②）：把事件处理整段删掉，
    读数恒为空，这条会红 —— 只断言「没出现非法值」是挡不住那种改坏的。
    """
    assert stage_sequence(replay(path)) == [
        None,
        Stage.LOADING_MODEL,
        Stage.SAMPLING,
        Stage.DECODING_AUDIO,
        Stage.DECODING_VIDEO,
        Stage.ENCODING,
        None,
    ]


@pytest.mark.parametrize("path", [MODEL_NOT_IN_VRAM, MODEL_IN_VRAM], ids=lambda p: p.stem)
def test_sampling_progress_counts_the_eight_steps(path):
    """VS-23：采样期间 `progress` 取到录制里那 8 个值（1/8 … 8/8），单调不减，最后是 1.0；
    其他阶段 `progress` 一律为空。"""
    readings = replay(path)
    sampling = [r.progress for r in readings if r.stage is Stage.SAMPLING]

    assert sampling == [step / 8 for step in range(1, 9)]
    assert sampling == sorted(sampling)
    assert sampling[-1] == 1.0
    assert all(r.progress is None for r in readings if r.stage is not Stage.SAMPLING)


def test_sampler_that_has_not_reported_a_step_yet_counts_as_loading_the_model():
    """VS-23 的关键一条：采样节点开始执行、第一个 `progress` 还没来之前，仍算 `loading_model`。

    录制（模型不在显存）里这段空档 22 秒 —— 主模型是在采样节点内部才搬进显存的。
    标成「采样中 0%」的话，用户会看到 0% 卡 20 多秒。
    """
    prompt_id, events = load_recording(MODEL_NOT_IN_VRAM)
    workflow = recorded_workflow()
    reading = UNKNOWN_STAGE
    reading_when_sampler_starts = None
    for event in events:
        if event_prompt_id(event) != prompt_id:
            continue
        reading = reading_after_event(event, workflow, reading)
        if event.type == "executing" and event.data.get("node") == "sampler":
            reading_when_sampler_starts = reading

    assert reading_when_sampler_starts == StageReading(Stage.LOADING_MODEL, None)


def test_recording_contains_the_noise_the_filter_must_survive():
    """守住上面几条的前提：录制里**真的有**要被过滤掉的东西。

    若有人把录制「清理」成只剩干净事件，上面的断言照样绿，但它们就不再证明过滤是对的了。
    """
    prompt_id, events = load_recording(MODEL_NOT_IN_VRAM)
    types = {e.type for e in events}
    assert "dasiwa.system_monitor" in types  # 第三方插件每秒一条的广播
    assert "progress_state" in types  # 新版 ComfyUI 的重复快照
    assert any(e.type == "status" and event_prompt_id(e) is None for e in events)


# ============ VS-26：不属于这个任务、或看不懂的事件不改读数 ============

SAMPLING_HALFWAY = StageReading(Stage.SAMPLING, 0.5)


@pytest.mark.parametrize(
    "event",
    [
        # 重连后 ComfyUI 补发的那条（录在 t2va_disconnect_after_step3.jsonl 里）：没有 prompt_id。
        ComfyEvent("executing", {"node": "sampler"}),
        ComfyEvent("status", {"status": {"exec_info": {"queue_remaining": 1}}}),
        ComfyEvent("dasiwa.system_monitor", {"cpu_percent": 3.6}),
    ],
    ids=["reconnect-executing", "status", "plugin-broadcast"],
)
def test_events_without_a_prompt_id_are_not_attributed_to_any_job(event):
    """VS-26：没有 `prompt_id` 的事件，`event_prompt_id` 给 `None`，调用方据此丢掉。"""
    assert event_prompt_id(event) is None


@pytest.mark.parametrize(
    "data",
    [
        {"value": True, "max": 8, "node": "sampler"},  # bool 是 int 的子类
        {"value": 3, "max": 0, "node": "sampler"},  # 除以零
        {"value": "3", "max": 8, "node": "sampler"},
        {"value": 3, "max": 8},  # 不知道是哪个节点的进度
        {"value": 3, "max": 8, "node": "decode_video"},  # 不是采样节点
        {"value": 3, "max": 8, "node": "no_such_node"},
    ],
)
def test_malformed_progress_keeps_the_current_reading(data):
    """VS-26：坏的 `progress` 不改读数（构造出来的：录制里没有，但字段类型 ComfyUI 不保证）。"""
    event = ComfyEvent("progress", {"prompt_id": "p", **data})
    assert reading_after_event(event, recorded_workflow(), SAMPLING_HALFWAY) == SAMPLING_HALFWAY


def test_progress_beyond_the_maximum_is_clamped():
    event = ComfyEvent("progress", {"prompt_id": "p", "value": 9, "max": 8, "node": "sampler"})
    assert reading_after_event(event, recorded_workflow(), UNKNOWN_STAGE) == StageReading(Stage.SAMPLING, 1.0)


def test_an_unknown_node_clears_the_stage_instead_of_guessing():
    """VS-26：执行到一个表里没有的节点 ⇒ 空，而不是沿用上一个阶段或瞎猜。"""
    workflow = {**recorded_workflow(), "mystery": {"class_type": "SomeCustomNode", "inputs": {}}}
    event = ComfyEvent("executing", {"prompt_id": "p", "node": "mystery"})
    assert reading_after_event(event, workflow, SAMPLING_HALFWAY) == UNKNOWN_STAGE


@pytest.mark.parametrize(
    "event",
    [
        ComfyEvent("execution_success", {"prompt_id": "p"}),
        ComfyEvent("execution_error", {"prompt_id": "p"}),
        ComfyEvent("execution_interrupted", {"prompt_id": "p"}),
        ComfyEvent("executing", {"prompt_id": "p", "node": None}),  # 旧版 ComfyUI 的「结束」
    ],
    ids=lambda e: e.type,
)
def test_end_of_execution_clears_the_stage(event):
    assert reading_after_event(event, recorded_workflow(), SAMPLING_HALFWAY) == UNKNOWN_STAGE
