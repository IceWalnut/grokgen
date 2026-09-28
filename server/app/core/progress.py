"""把 ComfyUI 的 WebSocket 事件翻译成契约 §2 的 `stage` / `progress`。

纯逻辑，不碰网络 —— 所以能直接拿真实录制（`tests/data/comfy_ws/*.jsonl`）回放来测（VS-23）。

映射规则与每一条的依据见执行文档 `server/Docs/implementation/M2R4b_comfy_ws_progress.md` §5。
最要紧的三条：

1. **采样节点开始执行、但第一个 `progress` 还没来，仍算 `loading_model`**。
   录制显示主模型是在采样节点内部、第一步之前才搬进显存的（模型不在显存时这段空档 22 秒）。
2. **没有 `prompt_id` 的事件一律不用**。重连后 ComfyUI 补发一条不带 `prompt_id` 的
   `executing {"node": "sampler"}`，若照常处理，一个采样到 37% 的任务会倒退回「正在加载模型」。
3. **认不出的节点不猜**，`stage` 置空。
"""

from dataclasses import dataclass
from enum import Enum

from app.comfy.client import ComfyEvent


class Stage(str, Enum):
    """`running` 时的细分阶段。取值与契约 §2 一致。"""

    LOADING_MODEL = "loading_model"
    SAMPLING = "sampling"
    DECODING_VIDEO = "decoding_video"
    DECODING_AUDIO = "decoding_audio"
    ENCODING = "encoding"


#: 会报告步数进度的采样节点。它们的 `progress` 事件才是 `sampling` 的百分比来源。
SAMPLER_CLASSES: frozenset[str] = frozenset(
    {"SamplerCustomAdvanced", "SamplerCustom", "KSampler", "KSamplerAdvanced"}
)

#: 非采样节点 → 阶段。只列网关自己拼的图里真会出现的节点（`comfy/workflows/h3_video.py`）；
#: 表里没有的节点 `stage` 置空而不是猜，免得将来换 workflow 时显示一个错的阶段。
STAGE_BY_CLASS: dict[str, Stage] = {
    # 加载与准备。模型文件读入、文本编码都发生在这些节点里；
    # 录制里模型不在显存时这一段约 14 秒（其中 `MiniMaxH3ImageToVideo` 的文本编码约 10 秒）。
    "UNETLoader": Stage.LOADING_MODEL,
    "CLIPLoader": Stage.LOADING_MODEL,
    "VAELoader": Stage.LOADING_MODEL,
    "LoraLoaderModelOnly": Stage.LOADING_MODEL,
    "MiniMaxH3SigmaShift": Stage.LOADING_MODEL,
    "MiniMaxH3ImageToVideo": Stage.LOADING_MODEL,
    "LoadImage": Stage.LOADING_MODEL,
    "BasicGuider": Stage.LOADING_MODEL,
    "BasicScheduler": Stage.LOADING_MODEL,
    "KSamplerSelect": Stage.LOADING_MODEL,
    "RandomNoise": Stage.LOADING_MODEL,
    # 采样之后。
    "VAEDecode": Stage.DECODING_VIDEO,
    "VAEDecodeAudio": Stage.DECODING_AUDIO,
    "CreateVideo": Stage.ENCODING,
    "SaveVideo": Stage.ENCODING,
}

#: 表示「这次执行结束了」的事件类型。结束之后 `stage` 置空，终态由轮询 `/history` 决定。
END_OF_EXECUTION_EVENTS: frozenset[str] = frozenset(
    {"execution_success", "execution_error", "execution_interrupted"}
)


@dataclass(frozen=True)
class StageReading:
    """一个任务此刻的细分阶段与进度。

    Attributes:
        stage: 细分阶段；不知道时为 `None`。
        progress: 采样进度 0..1，只在 `stage` 为 `sampling` 时有值。
    """

    stage: Stage | None
    progress: float | None


#: 「不知道在哪个阶段」。ws 没连上、任务不在跑、或看到了认不出的节点时都是它。
UNKNOWN_STAGE = StageReading(stage=None, progress=None)


def event_prompt_id(event: ComfyEvent) -> str | None:
    """取出事件所属任务的 `prompt_id`。

    Returns:
        字符串形式的 `prompt_id`；事件里没有、或不是字符串时返回 `None`
        —— 调用方据此**丢掉**这条事件（模块说明第 2 条）。
    """
    prompt_id = event.data.get("prompt_id")
    return prompt_id if isinstance(prompt_id, str) and prompt_id else None


def reading_after_event(
    event: ComfyEvent, workflow: dict, current: StageReading
) -> StageReading:
    """一条**已确认属于这个任务**的事件到来之后，任务的阶段与进度变成什么。

    流程说明：
        1. 执行结束类事件，或 `executing` 且 `node` 为 `null` ⇒ `UNKNOWN_STAGE`；
        2. `executing` 某个节点 ⇒ 按该节点在本任务 workflow 里的 `class_type` 查表；
           采样节点此时还没有步数，算 `loading_model`（模块说明第 1 条）；
           查不到 ⇒ `UNKNOWN_STAGE`；
        3. 采样节点的 `progress` ⇒ `sampling` + `value / max`（夹到 0..1）；
           `value` / `max` 不是数、`max` 不为正、节点不是采样节点 ⇒ 不变；
        4. 其余事件 ⇒ 不变。

    Args:
        event: ComfyUI 事件。调用方负责先用 `event_prompt_id` 确认它属于这个任务。
        workflow: 这个任务提交给 ComfyUI 的节点图（节点 id → `{"class_type": ...}`）。
            用它把事件里的节点 id 换成 `class_type`。
        current: 事件到来之前的读数。

    Returns:
        新的读数；事件不影响阶段时原样返回 `current`。
    """
    if event.type in END_OF_EXECUTION_EVENTS:
        return UNKNOWN_STAGE

    if event.type == "executing":
        node_id = event.data.get("node")
        if node_id is None:
            # 旧版 ComfyUI 用 `executing` + `node=null` 表示整次执行结束。
            return UNKNOWN_STAGE
        class_type = _class_type_of(workflow, node_id)
        if class_type in SAMPLER_CLASSES:
            return StageReading(stage=Stage.LOADING_MODEL, progress=None)
        stage = STAGE_BY_CLASS.get(class_type) if class_type is not None else None
        return StageReading(stage=stage, progress=None) if stage is not None else UNKNOWN_STAGE

    if event.type == "progress":
        if _class_type_of(workflow, event.data.get("node")) not in SAMPLER_CLASSES:
            return current
        value = event.data.get("value")
        maximum = event.data.get("max")
        if not _is_number(value) or not _is_number(maximum) or maximum <= 0:
            return current
        fraction = min(max(value / maximum, 0.0), 1.0)
        return StageReading(stage=Stage.SAMPLING, progress=fraction)

    return current


def _class_type_of(workflow: dict, node_id: object) -> str | None:
    """在任务自己的 workflow 里查一个节点 id 的 `class_type`；查不到返回 `None`。"""
    if not isinstance(node_id, str):
        return None
    node = workflow.get(node_id)
    if not isinstance(node, dict):
        return None
    class_type = node.get("class_type")
    return class_type if isinstance(class_type, str) else None


def _is_number(value: object) -> bool:
    """是不是一个可以参与除法的数。`bool` 是 `int` 的子类，要排除。"""
    return isinstance(value, (int, float)) and not isinstance(value, bool)
