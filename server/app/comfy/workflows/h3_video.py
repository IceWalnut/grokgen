"""把一次视频生成请求拼成 ComfyUI 的 API 格式 workflow。

**这个模块是纯逻辑：不碰网络、不读文件、不占 GPU。**
拼错 workflow 是这个项目的主要风险，而 ComfyUI 的报错未必指向真正的原因，
所以这里的每一条规则都要能在开发机上被断言。

参考来源有两份，结构上一致：

* `Docs/knowledge/DasiwaMinimaxH3WorkflowsT2VA_cMMH3V23.json` ——
  **用户实际在用的模板**，采样参数与 sigma shift 以它为准；
* ComfyUI 自带的 `video_minimax_h3_i2v.json` / `_t2v.json` ——
  官方模板，帧数换算公式取自它。
"""

import math
import random

from app.core.config import H3_AUDIO_VAE, H3_VIDEO_VAE, settings
from app.media.probe import ImageSize
from app.models.video_job import (
    FULL_PROFILE,
    RESOLUTION_MULTIPLE,
    NormalizedParams,
    PromptParts,
    TURBO_PROFILE,
    VideoJobRequest,
    VideoMode,
)

#: 网关自己生成 seed 时的上限，等于 2**53。
#:
#: ⚠️ **为什么不是 2**63（int64 的范围）**：JSON 的数字没有类型，
#: 而很多客户端的 JSON 解析器把数字一律读成双精度浮点 ——
#: 双精度只能精确表示到 2**53，再大就会被静默舍入。
#:
#: M1R7 实测：网关生成的 `3631950862913668918` 经 `jq` 解析后变成
#: `3631950862913669000`，**差了 82**。JavaScript 的 `JSON.parse` 同理。
#:
#: seed 的全部用途就是复现，一个差 82 的 seed 什么都复现不了，**而且不会报错**。
#: 2**53 ≈ 9.0e15 个取值远远够用，所以把生成范围压到这条安全线以内，
#: 比要求每个客户端都用 64 位整数解析器更可靠。
#:
#: ⚠️ 用户**自己填**的 seed 不受这个上限约束 —— 那是他们的值，网关原样回显。
MAX_SAFE_SEED = 2**53

FPS = 24.0

# 用户没给尺寸、也没有首帧图时的默认画布。M1 实测跑通过的尺寸。
DEFAULT_WIDTH = 736
DEFAULT_HEIGHT = 416

# 宽高比差多少才值得提示「会被拉伸」。
# 2% 以内是贴 32 倍数造成的，不值得打扰用户。
ASPECT_TOLERANCE = 0.02

# 帧数必须落在 17k+5 的格子上（5, 22, 39, …, 124, …）。
# 节点 tooltip：「Frame count at 24 fps, snapped up to the model's 17k+5 grid
# (124 = ~5s; trained range is ~124-362, longer is untested)」。
FRAME_GRID_STEP = 17
FRAME_GRID_OFFSET = 5

# 训练过的帧数区间，换算成 24 fps 约 5.2–15.1 秒。
# 超出不拒绝（需求里「宁可结果差也不要拒绝执行」），但要在 notices 里说明。
TRAINED_FRAME_RANGE = (124, 362)

# 节点 id 用有意义的字符串而不是数字。
# ComfyUI 的 API 格式只要求 key 唯一，用名字可读性高得多，出错时报的也是这个名字。
NODE_UNET = "unet"
NODE_LORA = "turbo_lora"
NODE_SHIFT = "sigma_shift"
NODE_CLIP = "clip"
NODE_VAE_VIDEO = "vae_video"
NODE_VAE_AUDIO = "vae_audio"
NODE_FIRST_FRAME = "first_frame"
NODE_LAST_FRAME = "last_frame"
NODE_H3 = "h3_image_to_video"
NODE_GUIDER = "guider"
NODE_SCHEDULER = "scheduler"
NODE_SAMPLER_SELECT = "sampler_select"
NODE_NOISE = "noise"
NODE_SAMPLER = "sampler"
NODE_DECODE_VIDEO = "decode_video"
NODE_DECODE_AUDIO = "decode_audio"
NODE_CREATE_VIDEO = "create_video"
NODE_SAVE_VIDEO = "save_video"


def seconds_to_length(seconds: float, fps: float = FPS) -> int:
    """把秒数换算成模型接受的帧数，向上贴到 17k+5 的格子。

    公式抄自官方模板里的 `ComfyMathExpression`：

        max(5, round(a * 24)) + (5 - (max(5, round(a * 24)) % 17)) % 17

    ⚠️ 换算结果几乎总是比用户要的长一点：5 秒 → 124 帧 = 5.17 秒。
    调用方必须把真实时长回报给用户，否则会被当成模型不准。

    Args:
        seconds: 期望时长，秒。
        fps: 帧率，模型按 24 训练，一般不要改。

    Returns:
        帧数，满足 `n % 17 == 5` 且 `n >= 5`。
    """
    raw = max(FRAME_GRID_OFFSET, round(seconds * fps))
    padding = (FRAME_GRID_OFFSET - (raw % FRAME_GRID_STEP)) % FRAME_GRID_STEP
    return raw + padding


def _round_up_to_multiple(value: int, multiple: int = RESOLUTION_MULTIPLE) -> int:
    """把尺寸向上取到 `multiple` 的倍数。"""
    return math.ceil(value / multiple) * multiple


def _round_to_multiple(value: float, multiple: int = RESOLUTION_MULTIPLE) -> int:
    """把尺寸就近取到 `multiple` 的倍数，最小一格。

    推画布时用「就近」而不是「向上」：向上取整会让实际像素数系统性地
    超出预算，长宽比也会被推歪。

    Args:
        value: 原始值，可以是小数。
        multiple: 倍数基数。

    Returns:
        至少等于 `multiple` 的整数。
    """
    return max(multiple, int(round(value / multiple)) * multiple)


def fit_canvas(
    source: ImageSize,
    target_pixels: int | None = None,
    multiple: int = RESOLUTION_MULTIPLE,
) -> tuple[int, int]:
    """按图片的宽高比算出一块画布，总像素数接近预算，两边都是 32 的倍数。

    **为什么要有这个函数**：原生节点对 `first_frame` 用的是 `crop="disabled"`，
    也就是**直接拉伸**。如果画布比例和图片不一样，首帧就会变形，
    而且这不会以任何形式报错。正解是让画布去适配图片。

    做法照官方 i2v 模板：它在 subgraph 外面接
    `ImageScaleToTotalPixels(0.9 MP, 32 的倍数) → GetImageSize` 驱动画布。

    ⚠️ **像素预算不用官方那个 0.9 MP**（约 1344x768）。M1 实测跑通的是
    `736x416` = 0.306 MP，默认就用它，放在配置里便于以后调大。
    **抄的是做法，不是参数。**

    Args:
        source: 源图尺寸。
        target_pixels: 目标总像素数，默认取配置里的 `canvas_target_pixels`。
        multiple: 宽高都要贴到的倍数。

    Returns:
        `(width, height)`，两边都是 `multiple` 的倍数且至少 `multiple`。
    """
    budget = target_pixels if target_pixels is not None else settings.canvas_target_pixels
    scale = math.sqrt(budget / (source.width * source.height))
    return (
        _round_to_multiple(source.width * scale, multiple),
        _round_to_multiple(source.height * scale, multiple),
    )


def _aspect_ratio(width: int, height: int) -> float:
    """宽高比。"""
    return width / height


def _resolve_canvas(
    request: VideoJobRequest, source_size: ImageSize | None, notices: list[str]
) -> tuple[int, int]:
    """定出最终画布尺寸，并把与用户预期的差别写进 `notices`。

    三种情形（契约文档 §2）：

    1. **用户给了宽高** —— 用他的，向上取到 32 的倍数。
       若同时有首帧图且比例对不上，**明确提示会被拉伸**。
    2. **没给宽高、但知道首帧图尺寸** —— 按图片比例推画布。这是推荐路径。
    3. **都没有**（纯 T2VA，或探测失败）—— 用默认值。

    ⚠️ 情形 1 那条提示不能省。首帧是 `crop="disabled"`（拉伸）不是裁剪，
    比例不符时画面会变形，而**这不会以任何形式报错**。

    Args:
        request: 原始请求。
        source_size: 首帧图尺寸，未知时为 `None`。
        notices: 就地追加提示的列表。

    Returns:
        `(width, height)`，都是 32 的倍数。
    """
    if request.width is not None or request.height is not None:
        width = _round_up_to_multiple(request.width or DEFAULT_WIDTH)
        height = _round_up_to_multiple(request.height or DEFAULT_HEIGHT)
        if (request.width, request.height) != (width, height):
            notices.append(
                f"分辨率已调整：{request.width}x{request.height} → {width}x{height}"
                f"（模型要求宽高都是 {RESOLUTION_MULTIPLE} 的倍数）"
            )
        if source_size is not None:
            source_ratio = _aspect_ratio(source_size.width, source_size.height)
            canvas_ratio = _aspect_ratio(width, height)
            if abs(source_ratio - canvas_ratio) / source_ratio > ASPECT_TOLERANCE:
                notices.append(
                    f"⚠️ 首帧图是 {source_size.width}x{source_size.height}，"
                    f"与画布 {width}x{height} 比例不同，**画面会被拉伸变形**"
                    "（模型对首帧是拉伸，不是裁剪）。留空宽高可让画布按图片比例自动计算"
                )
        return width, height

    if source_size is not None:
        width, height = fit_canvas(source_size)
        notices.append(
            f"画布按首帧图的比例推算为 {width}x{height}"
            f"（首帧图 {source_size.width}x{source_size.height}），避免拉伸变形"
        )
        return width, height

    return DEFAULT_WIDTH, DEFAULT_HEIGHT


def normalize(
    request: VideoJobRequest, source_size: ImageSize | None = None
) -> NormalizedParams:
    """把用户填的参数归一化成模型真正接受的值，并记录差异。

    流程说明：
        1. 定画布 —— 用户给了宽高就用他的（向上取到 32 的倍数）；
           没给且知道首帧图尺寸，就按图片比例推（`fit_canvas`）；
           都没有就用默认值；
        2. 时长换算成 17k+5 的帧数，并反算真实时长；
        3. seed 为空时随机生成（要回报，否则用户无法复现）；
        4. 按 turbo 与否选采样 profile；
        5. 每一处「用户填的」与「实际用的」不一致，都往 `notices` 里写一条中文说明。

    ⚠️ **本函数仍然是纯函数**：图片尺寸由调用方探测好再传进来，这里不读文件。

    Args:
        request: App 提交的原始请求。
        source_size: 首帧图的尺寸。给了它、且用户没有指定宽高时，
            画布按图片比例推导。

    Returns:
        `NormalizedParams`，其中 `notices` 是给用户看的提示，不是日志。
    """
    notices: list[str] = []
    width, height = _resolve_canvas(request, source_size, notices)

    length_frames = seconds_to_length(request.duration_seconds)
    actual_duration = length_frames / FPS
    if abs(actual_duration - request.duration_seconds) > 0.005:
        notices.append(
            f"时长已调整：请求 {request.duration_seconds:.2f} 秒，"
            f"实际 {length_frames} 帧 = {actual_duration:.2f} 秒"
            f"（模型只接受 {FRAME_GRID_STEP}k+{FRAME_GRID_OFFSET} 的帧数）"
        )

    low, high = TRAINED_FRAME_RANGE
    if not low <= length_frames <= high:
        notices.append(
            f"⚠️ {length_frames} 帧超出模型训练过的范围（{low}–{high} 帧，"
            f"约 {low / FPS:.1f}–{high / FPS:.1f} 秒），结果可能不稳定"
        )

    profile = TURBO_PROFILE if request.turbo else FULL_PROFILE

    return NormalizedParams(
        width=width,
        height=height,
        length_frames=length_frames,
        actual_duration_seconds=actual_duration,
        seed=request.seed if request.seed is not None else random.randrange(MAX_SAFE_SEED),
        sampler=profile["sampler"],
        steps=request.steps if request.steps is not None else profile["steps"],
        shift_video=profile["shift_video"],
        shift_audio=profile["shift_audio"],
        notices=notices,
    )


def _alignment_line(mode: VideoMode, actual_duration_seconds: float) -> str | None:
    """给了首/尾帧时，全局 prompt 第一行必须有的对齐指令。

    它说明「第几张图对应视频的第几秒」。用 Director 时这一行是自动注入的，
    绕开 Director 之后必须自己拼。

    写法抄自用户那份 V23 模板里的两个示例 MarkdownNote（`#2695` / `#2696`）。
    ⚠️ **Director 的文档里写的是 `<Picture 1> (from [Shot 1])`，带尖括号和方括号，
    与模板示例不一致。** 两种写法 ComfyUI 都不报错，差别只在生成质量上，
    没有便宜的验证方式。这里按模板示例（不带括号）—— 那是作者自己给的例子。

    Args:
        mode: 生成模式。
        actual_duration_seconds: **换算后的真实时长**，不是用户填的那个。
            写错会让模型把尾帧对到错误的时间点，而且不会报错。

    Returns:
        对齐指令行；`T2VA` 没有首尾帧，返回 `None`。
    """
    if mode is VideoMode.T2VA:
        return None
    if mode is VideoMode.I2VA:
        return (
            "For the target video, at 0.00 seconds into the target video, "
            "Picture 1 (from Shot 1) is fully referenced."
        )
    # FL2VA：两个端点，分隔符是 em-dash，秒数两位小数。
    return (
        "How the reference pictures align with the target video — "
        "Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; "
        f"Picture 2 (from Shot 1) aligns with the {actual_duration_seconds:.2f}-second "
        "mark of the target video."
    )


def build_prompt(parts: PromptParts, normalized: NormalizedParams, mode: VideoMode) -> str:
    """拼出送给模型的完整 prompt 文本。

    结构是对齐指令行（可选）加三个带标签的段落。

    ⚠️ 空的 `non_diegetic_music` 要写成 `N/A` 而不是留空 ——
    这是 Director 的自动规则（V23 模板 Quick Start 注释），绕开它之后要自己复制。

    Args:
        parts: 用户填的三段内容。
        normalized: 归一化结果，其中的真实时长要写进对齐指令行。
        mode: 生成模式，决定有没有对齐指令行。

    Returns:
        完整 prompt，段落之间空一行。
    """
    blocks: list[str] = []

    alignment = _alignment_line(mode, normalized.actual_duration_seconds)
    if alignment is not None:
        blocks.append(alignment)

    blocks.append(f"integrated_multimodal_description: {parts.description.strip()}")

    soundscape = parts.soundscape.strip()
    if soundscape:
        blocks.append(f"overall_soundscape: {soundscape}")

    blocks.append(f"non_diegetic_music: {parts.music.strip() or 'N/A'}")

    return "\n\n".join(blocks)


def build_workflow(
    request: VideoJobRequest, source_size: ImageSize | None = None
) -> tuple[dict, NormalizedParams]:
    """拼出完整的 API 格式 workflow。

    流程说明：
        1. 先归一化参数，拿到真实的尺寸、帧数、seed 与采样 profile；
        2. 拼 prompt（含对齐指令行）；
        3. 按节点图连线：UNET →（turbo 时过 LoRA）→ sigma shift → guider / scheduler，
           H3 节点产出 conditioning 与音画合一的 latent，采样后分别用
           video VAE 与 audio VAE 解码，再合成视频保存。

    ⚠️ 图里不含 `ComfySwitchNode` / `Primitive*` / `ComfyMathExpression` ——
    那些是给 UI 用的，网关在这里算完直接填字面量。

    Args:
        request: App 提交的原始请求。
        source_size: 首帧图的尺寸。给了它、且用户没指定宽高时，
            画布按图片比例推导，避免首帧被拉伸。

    Returns:
        `(workflow, normalized)`。`workflow` 直接就是 `POST /prompt` 的 `prompt` 字段，
        `normalized` 要回给 App 显示。
    """
    normalized = normalize(request, source_size)
    prompt_text = build_prompt(request.prompt, normalized, request.mode)

    workflow: dict[str, dict] = {
        NODE_UNET: {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": settings.h3_unet, "weight_dtype": "default"},
        },
        NODE_CLIP: {
            "class_type": "CLIPLoader",
            "inputs": {
                "clip_name": settings.h3_clip,
                # ⚠️ type 不能省：漏了它 CLIP 加载不起来。
                "type": "minimax",
                "device": "default",
            },
        },
        NODE_VAE_VIDEO: {"class_type": "VAELoader", "inputs": {"vae_name": H3_VIDEO_VAE}},
        NODE_VAE_AUDIO: {"class_type": "VAELoader", "inputs": {"vae_name": H3_AUDIO_VAE}},
    }

    # turbo 时在 UNET 与 sigma shift 之间插一个 LoRA；否则直接相连。
    model_source = NODE_UNET
    if request.turbo:
        workflow[NODE_LORA] = {
            "class_type": "LoraLoaderModelOnly",
            "inputs": {
                "model": [NODE_UNET, 0],
                "lora_name": settings.h3_turbo_lora,
                "strength_model": 1.0,
            },
        }
        model_source = NODE_LORA

    workflow[NODE_SHIFT] = {
        "class_type": "MiniMaxH3SigmaShift",
        "inputs": {
            "model": [model_source, 0],
            "shift_video": normalized.shift_video,
            "shift_audio": normalized.shift_audio,
        },
    }

    h3_inputs: dict = {
        "clip": [NODE_CLIP, 0],
        "vae": [NODE_VAE_VIDEO, 0],
        "prompt": prompt_text,
        "width": normalized.width,
        "height": normalized.height,
        "length": normalized.length_frames,
    }
    # 模式的全部差别就在这里：接几张图。三种模式共用同一个节点。
    if request.first_frame is not None:
        workflow[NODE_FIRST_FRAME] = {
            "class_type": "LoadImage",
            "inputs": {"image": request.first_frame},
        }
        h3_inputs["first_frame"] = [NODE_FIRST_FRAME, 0]
    if request.last_frame is not None:
        workflow[NODE_LAST_FRAME] = {
            "class_type": "LoadImage",
            "inputs": {"image": request.last_frame},
        }
        h3_inputs["last_frame"] = [NODE_LAST_FRAME, 0]

    workflow[NODE_H3] = {"class_type": "MiniMaxH3ImageToVideo", "inputs": h3_inputs}

    workflow.update(
        {
            # ⚠️ BasicGuider 只吃 model + conditioning，没有 negative ——
            # 所以不需要 ConditioningZeroOut 之类的东西。
            NODE_GUIDER: {
                "class_type": "BasicGuider",
                "inputs": {"model": [NODE_SHIFT, 0], "conditioning": [NODE_H3, 0]},
            },
            NODE_SCHEDULER: {
                "class_type": "BasicScheduler",
                "inputs": {
                    "model": [NODE_SHIFT, 0],
                    "scheduler": "simple",
                    "steps": normalized.steps,
                    "denoise": 1.0,
                },
            },
            NODE_SAMPLER_SELECT: {
                "class_type": "KSamplerSelect",
                "inputs": {"sampler_name": normalized.sampler},
            },
            NODE_NOISE: {
                "class_type": "RandomNoise",
                "inputs": {"noise_seed": normalized.seed},
            },
            NODE_SAMPLER: {
                "class_type": "SamplerCustomAdvanced",
                "inputs": {
                    "noise": [NODE_NOISE, 0],
                    "guider": [NODE_GUIDER, 0],
                    "sampler": [NODE_SAMPLER_SELECT, 0],
                    "sigmas": [NODE_SCHEDULER, 0],
                    "latent_image": [NODE_H3, 1],
                },
            },
            # H3 的 latent 是音画合一的，同一个输出喂给两个 VAE，各取所需。
            NODE_DECODE_VIDEO: {
                "class_type": "VAEDecode",
                "inputs": {"samples": [NODE_SAMPLER, 0], "vae": [NODE_VAE_VIDEO, 0]},
            },
            NODE_DECODE_AUDIO: {
                "class_type": "VAEDecodeAudio",
                "inputs": {"samples": [NODE_SAMPLER, 0], "vae": [NODE_VAE_AUDIO, 0]},
            },
            NODE_CREATE_VIDEO: {
                "class_type": "CreateVideo",
                "inputs": {
                    "images": [NODE_DECODE_VIDEO, 0],
                    "fps": FPS,
                    "audio": [NODE_DECODE_AUDIO, 0],
                },
            },
            # format/codec 都用 auto：实测产出 H.264 + AAC 的 MP4，手机直接能播，
            # 不需要再过一遍 ffmpeg。
            NODE_SAVE_VIDEO: {
                "class_type": "SaveVideo",
                "inputs": {
                    "video": [NODE_CREATE_VIDEO, 0],
                    "filename_prefix": "video/grokgen",
                    "format": "auto",
                    "codec": "auto",
                },
            },
        }
    )

    return workflow, normalized
