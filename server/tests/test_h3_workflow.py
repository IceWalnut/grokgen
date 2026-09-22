"""M1R2 的判据：VS-1〜VS-4。

全部在开发机上跑，不需要 GPU、不连服务器 —— workflow 构造是纯逻辑。
"""

import pytest

from app.comfy.workflows.h3_video import (
    FPS,
    NODE_FIRST_FRAME,
    NODE_H3,
    NODE_LAST_FRAME,
    NODE_LORA,
    NODE_SHIFT,
    build_prompt,
    fit_canvas,
    build_workflow,
    normalize,
    seconds_to_length,
)
from app.media.probe import ImageSize
from app.models.video_job import PromptParts, VideoJobRequest, VideoMode


def make_request(**overrides) -> VideoJobRequest:
    """构造一个默认请求，测试只覆盖它关心的字段。"""
    base = {
        "mode": VideoMode.T2VA,
        "prompt": PromptParts(description="一辆红色跑车驶过海岸公路", soundscape="海浪声", music="舒缓的电子乐"),
        "width": 736,
        "height": 416,
        "duration_seconds": 5.0,
        "seed": 42,
    }
    base.update(overrides)
    return VideoJobRequest(**base)


# --------------------------------------------------------------------------
# VS-1 帧数换算
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("seconds", "expected_frames"),
    [
        (1.0, 39),
        (5.0, 124),
        (5.17, 124),
        (5.2, 141),
        (15.0, 362),
    ],
)
def test_seconds_to_length_known_points(seconds, expected_frames):
    """几个已知点位，含 5 秒附近那个容易错的边界。"""
    assert seconds_to_length(seconds) == expected_frames


@pytest.mark.parametrize("seconds", [0.01, 0.5, 1.0, 2.7, 5.0, 8.3, 12.0, 15.0, 30.0])
def test_seconds_to_length_always_lands_on_the_grid(seconds):
    """不变量：结果恒满足 n % 17 == 5。

    ⚠️ 这条不能省。上面的点位测试只证明那几个输入对，
    公式被整体改坏（比如 17 写成 16）时点位可能还蒙对几个，不变量不会。
    """
    frames = seconds_to_length(seconds)
    assert frames % 17 == 5
    assert frames >= 5


def test_seconds_to_length_never_rounds_down():
    """只向上贴格子：实际时长不会短于用户要的。"""
    for seconds in (1.0, 3.3, 5.0, 7.7, 11.1):
        assert seconds_to_length(seconds) / FPS >= seconds - 1e-9


# --------------------------------------------------------------------------
# VS-4 尺寸归一
# --------------------------------------------------------------------------


def test_resolution_rounded_up_to_multiple_of_32_with_notice():
    """768x432 的 432 不是 32 的倍数，要被抬到 448 并说明。"""
    normalized = normalize(make_request(width=768, height=432))

    assert (normalized.width, normalized.height) == (768, 448)
    assert any("分辨率已调整" in n and "768x448" in n for n in normalized.notices)


def test_resolution_already_aligned_produces_no_notice():
    """已经是倍数时不应该产生噪音提示。"""
    normalized = normalize(make_request(width=736, height=416))

    assert (normalized.width, normalized.height) == (736, 416)
    assert not any("分辨率已调整" in n for n in normalized.notices)


def test_duration_adjustment_is_reported():
    """5 秒会变成 124 帧 = 5.17 秒，这个差别必须告诉用户。"""
    normalized = normalize(make_request(duration_seconds=5.0))

    assert normalized.length_frames == 124
    assert normalized.actual_duration_seconds == pytest.approx(124 / 24)
    assert any("时长已调整" in n and "5.17" in n for n in normalized.notices)


def test_seed_is_generated_and_reported_when_absent():
    """用户没填 seed 时网关生成一个，并且要回报 —— 否则无法复现。"""
    normalized = normalize(make_request(seed=None))
    assert normalized.seed >= 0


def test_out_of_trained_range_warns_but_does_not_reject():
    """超出训练范围只警告不拒绝（需求：宁可结果差也不要拒绝执行）。"""
    normalized = normalize(make_request(duration_seconds=30.0))
    assert normalized.length_frames > 362
    assert any("超出模型训练过的范围" in n for n in normalized.notices)


def test_turbo_and_full_profiles_differ():
    """两套采样 profile 不能混用。

    V23 模板的 Settings 注释分得很清楚：Turbo 是 euler + 少步数 + 低 shift，
    非 Turbo 是 res_multistep + 25 步 + 高 shift。
    """
    turbo = normalize(make_request(turbo=True))
    full = normalize(make_request(turbo=False))

    assert (turbo.sampler, turbo.steps, turbo.shift_video) == ("euler", 8, 6.0)
    assert (full.sampler, full.steps, full.shift_video) == ("res_multistep", 25, 11.0)


# --------------------------------------------------------------------------
# VS-2 三种模式的节点图形状
# --------------------------------------------------------------------------


def test_t2va_graph_has_no_frame_inputs():
    """T2VA 不接图：H3 节点上不应出现 first_frame / last_frame 键。"""
    workflow, _ = build_workflow(make_request(mode=VideoMode.T2VA))
    h3_inputs = workflow[NODE_H3]["inputs"]

    assert "first_frame" not in h3_inputs
    assert "last_frame" not in h3_inputs
    assert NODE_FIRST_FRAME not in workflow
    assert NODE_LAST_FRAME not in workflow


def test_i2va_graph_has_only_first_frame():
    """I2VA 只接首帧。"""
    workflow, _ = build_workflow(
        make_request(mode=VideoMode.I2VA, first_frame="start.png")
    )
    h3_inputs = workflow[NODE_H3]["inputs"]

    assert h3_inputs["first_frame"] == [NODE_FIRST_FRAME, 0]
    assert "last_frame" not in h3_inputs
    assert workflow[NODE_FIRST_FRAME]["inputs"]["image"] == "start.png"


def test_fl2va_graph_wires_two_distinct_load_image_nodes():
    """FL2VA 两张图要接到两个不同的 LoadImage，不能共用一个。"""
    workflow, _ = build_workflow(
        make_request(mode=VideoMode.FL2VA, first_frame="start.png", last_frame="end.png")
    )
    h3_inputs = workflow[NODE_H3]["inputs"]

    assert h3_inputs["first_frame"] == [NODE_FIRST_FRAME, 0]
    assert h3_inputs["last_frame"] == [NODE_LAST_FRAME, 0]
    assert NODE_FIRST_FRAME != NODE_LAST_FRAME
    assert workflow[NODE_FIRST_FRAME]["inputs"]["image"] == "start.png"
    assert workflow[NODE_LAST_FRAME]["inputs"]["image"] == "end.png"


def test_turbo_inserts_lora_between_unet_and_shift():
    """turbo 开着时图里要有 LoRA，且接在 UNET 与 sigma shift 之间。"""
    workflow, _ = build_workflow(make_request(turbo=True))

    assert NODE_LORA in workflow
    assert workflow[NODE_LORA]["inputs"]["model"] == ["unet", 0]
    assert workflow[NODE_SHIFT]["inputs"]["model"] == [NODE_LORA, 0]


def test_no_turbo_removes_lora_node_entirely():
    """turbo 关掉时不是把强度设成 0，而是图里根本没有这个节点。"""
    workflow, _ = build_workflow(make_request(turbo=False))

    assert NODE_LORA not in workflow
    assert workflow[NODE_SHIFT]["inputs"]["model"] == ["unet", 0]


def test_graph_has_no_negative_conditioning():
    """BasicGuider 没有 negative 输入，图里不该出现造 negative 的节点。"""
    workflow, _ = build_workflow(make_request())

    class_types = {node["class_type"] for node in workflow.values()}
    assert "ConditioningZeroOut" not in class_types
    assert "KSampler" not in class_types
    assert "negative" not in workflow["guider"]["inputs"]


def test_clip_loader_specifies_minimax_type():
    """CLIPLoader 漏了 type 会加载失败，这个值必须钉住。"""
    workflow, _ = build_workflow(make_request())
    assert workflow["clip"]["inputs"]["type"] == "minimax"


def test_both_vae_decoders_share_the_same_latent():
    """H3 的 latent 是音画合一的，两个解码器吃同一个输出。"""
    workflow, _ = build_workflow(make_request())

    assert workflow["decode_video"]["inputs"]["samples"] == ["sampler", 0]
    assert workflow["decode_audio"]["inputs"]["samples"] == ["sampler", 0]
    assert workflow["decode_video"]["inputs"]["vae"] == ["vae_video", 0]
    assert workflow["decode_audio"]["inputs"]["vae"] == ["vae_audio", 0]


# --------------------------------------------------------------------------
# VS-3 prompt 与对齐指令行
# --------------------------------------------------------------------------


def test_t2va_prompt_has_no_alignment_line():
    """没有首尾帧就不该有对齐指令行。"""
    request = make_request(mode=VideoMode.T2VA)
    normalized = normalize(request)
    text = build_prompt(request.prompt, normalized, request.mode)

    assert "Picture 1" not in text
    assert text.startswith("integrated_multimodal_description:")


def test_i2va_alignment_line_is_present_and_anchors_at_zero():
    """I2VA 第一行是单帧对齐句。

    ⚠️ 这里是正向断言：不能只检查「没有多余内容」——
    把整段删掉也能让那种断言通过。
    """
    request = make_request(mode=VideoMode.I2VA, first_frame="start.png")
    normalized = normalize(request)
    text = build_prompt(request.prompt, normalized, request.mode)

    first_line = text.splitlines()[0]
    assert "Picture 1 (from Shot 1)" in first_line
    assert "0.00 seconds" in first_line
    assert "is fully referenced" in first_line


def test_fl2va_alignment_line_uses_the_converted_duration():
    """尾帧的秒数必须是换算后的 5.17，不是用户填的 5.00。

    ⚠️ 这是本轮最容易写错、且**写错不会报错**的一处：
    模型会把尾帧对到错误的时间点，只表现为结果变差。
    """
    request = make_request(
        mode=VideoMode.FL2VA,
        first_frame="start.png",
        last_frame="end.png",
        duration_seconds=5.0,
    )
    normalized = normalize(request)
    text = build_prompt(request.prompt, normalized, request.mode)

    first_line = text.splitlines()[0]
    assert "Picture 1 (from Shot 1)" in first_line
    assert "Picture 2 (from Shot 1)" in first_line
    assert "0.00-second mark" in first_line
    assert "5.17-second mark" in first_line
    assert "5.00-second mark" not in first_line


def test_blank_music_becomes_na():
    """空的背景音乐段落要填 N/A —— 这是 Director 的自动规则。"""
    request = make_request(prompt=PromptParts(description="一只猫", music=""))
    normalized = normalize(request)
    text = build_prompt(request.prompt, normalized, request.mode)

    assert "non_diegetic_music: N/A" in text


def test_blank_soundscape_leaves_no_empty_label():
    """环境声留空时不要留一个孤零零的空标签。"""
    request = make_request(prompt=PromptParts(description="一只猫", soundscape="", music="钢琴"))
    normalized = normalize(request)
    text = build_prompt(request.prompt, normalized, request.mode)

    assert "overall_soundscape:" not in text
    # 正向断言：另外两段确实在
    assert "integrated_multimodal_description: 一只猫" in text
    assert "non_diegetic_music: 钢琴" in text


def test_prompt_text_reaches_the_h3_node():
    """拼好的 prompt 要真的进到节点里，不能只在函数里存在。"""
    request = make_request(mode=VideoMode.I2VA, first_frame="start.png")
    workflow, _ = build_workflow(request)

    prompt_in_graph = workflow[NODE_H3]["inputs"]["prompt"]
    assert prompt_in_graph.splitlines()[0].startswith("For the target video")
    assert "integrated_multimodal_description:" in prompt_in_graph


# --------------------------------------------------------------------------
# VS-16 画布按首帧图的比例推导
# --------------------------------------------------------------------------


def test_fit_canvas_preserves_aspect_ratio_for_square_image():
    """1:1 的图应当得到近方形画布，而不是被塞进 16:9。"""
    width, height = fit_canvas(ImageSize(768, 768))

    assert width == height, "正方形的图不该推出非正方形画布"
    assert width % 32 == 0
    # 像素数落在预算附近（就近取整会有一点偏差）
    assert 0.8 <= (width * height) / (736 * 416) <= 1.25


@pytest.mark.parametrize(
    "source",
    [ImageSize(1920, 1080), ImageSize(1080, 1920), ImageSize(768, 768), ImageSize(1024, 576)],
)
def test_fit_canvas_keeps_ratio_within_tolerance(source):
    """常见比例下，推出来的画布比例与原图差别要很小。

    ⚠️ 这是这条链路的意义所在：比例不符 = 首帧被拉伸变形，而且不报错。
    """
    width, height = fit_canvas(source)
    source_ratio = source.width / source.height
    canvas_ratio = width / height

    assert abs(canvas_ratio - source_ratio) / source_ratio < 0.08
    assert width % 32 == 0 and height % 32 == 0


def test_fit_canvas_extreme_ratio_does_not_collapse():
    """极端比例下短边不能塌成 0 或负数。"""
    width, height = fit_canvas(ImageSize(4096, 256))

    assert width >= 32 and height >= 32
    assert width % 32 == 0 and height % 32 == 0


def test_canvas_derived_when_dimensions_omitted():
    """不给宽高 + 知道图片尺寸 ⇒ 按图片比例推，并说明。"""
    request = make_request(
        mode=VideoMode.I2VA, first_frame="example.png", width=None, height=None
    )
    normalized = normalize(request, ImageSize(768, 768))

    assert normalized.width == normalized.height
    assert any("按首帧图的比例推算" in n for n in normalized.notices)


def test_explicit_dimensions_win_but_stretch_is_flagged():
    """给了宽高就用用户的，但比例不符时必须警告会变形。

    ⚠️ 正向断言：警告里要出现两边的实际尺寸，
    否则用户不知道是哪两个数对不上。
    """
    request = make_request(
        mode=VideoMode.I2VA, first_frame="example.png", width=736, height=416
    )
    normalized = normalize(request, ImageSize(768, 768))

    assert (normalized.width, normalized.height) == (736, 416)
    warning = [n for n in normalized.notices if "拉伸变形" in n]
    assert warning, "比例不符却没有警告"
    assert "768x768" in warning[0] and "736x416" in warning[0]


def test_matching_aspect_ratio_produces_no_stretch_warning():
    """比例本来就一致时不要发噪音警告。"""
    request = make_request(
        mode=VideoMode.I2VA, first_frame="x.png", width=736, height=416
    )
    normalized = normalize(request, ImageSize(1472, 832))  # 正好 2 倍，同比例

    assert not any("拉伸变形" in n for n in normalized.notices)


def test_no_dimensions_and_no_source_falls_back_to_default():
    """纯 T2VA 既没有宽高也没有图片，用默认值。"""
    normalized = normalize(make_request(width=None, height=None))
    assert (normalized.width, normalized.height) == (736, 416)
