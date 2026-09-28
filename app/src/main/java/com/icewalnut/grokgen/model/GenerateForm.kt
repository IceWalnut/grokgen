package com.icewalnut.grokgen.model

import com.icewalnut.grokgen.net.dto.JobSubmitRequestDto
import com.icewalnut.grokgen.net.dto.PromptPartsDto

/** 生成模式。M2 只做这两种（FL2VA 不做，App 架构文档 §4）。 */
enum class GenerateMode(val wireValue: String) {
    TextToVideo("T2VA"),
    ImageToVideo("I2VA"),
}

/**
 * 时长的三档。
 *
 * ⚠️ 这是**请求**的时长。网关会换算成 17k+5 帧，实际时长在提交后的
 * `normalized.actual_duration_seconds` 里（5 秒 → 5.17 秒）。
 * **App 不在本地预测实际时长** —— 那等于在 App 里抄一份网关的帧数换算规则，
 * 两份迟早对不上。
 *
 * ⭐ 三档都落在模型训练过的帧数范围内：网关的 `TRAINED_FRAME_RANGE` 是 124–362 帧
 * （24 fps 下约 5.17–15.1 秒），换算后 5 秒 = 124 帧、8 秒 = 192 帧、10 秒 = 243 帧。
 * 最初定过 3 秒一档，换算是 73 帧，低于训练范围 —— 网关照样生成但会提示结果可能不稳定，
 * 所以 2026-09-28 改成了 5 / 8 / 10。
 *
 * ⚠️ 只有 5 秒在这台 4080 SUPER 上实测过（M1R7）。8、10 秒显存够不够还不知道。
 */
enum class DurationPreset(val seconds: Double) {
    S5(5.0),
    S8(8.0),
    S10(10.0),
    ;

    /** 界面上点一下切到下一档，循环。 */
    fun next(): DurationPreset = entries[(ordinal + 1) % entries.size]
}

/**
 * 首帧图。
 *
 * ⭐ 生成页拿到的是一个 `assetId` 字符串，不是一个「上传结果」对象（App 架构文档 §8）：
 * M3 从媒体库挑、M5 用 SD 现生成，最后都收敛成同一个字符串。
 *
 * @param assetId 网关返回的 `asset_id`。**原样存、原样传回，不要解析它、不要自己拼。**
 * @param width 网关用 ffprobe 读到的宽，像素。显示给用户看画布会按什么比例推。
 * @param height 同上。
 * @param uri 手机上那张图的 `content://` URI，**只用来显示缩略图**，不发给网关。
 */
data class FirstFrameImage(
    val assetId: String,
    val width: Int,
    val height: Int,
    val uri: String,
)

/**
 * 生成页的表单，是用户填的**原样**。
 *
 * 规整（去空白、解析种子）在 [toSubmitRequest] 里做一次，不散在界面里。
 *
 * @param firstFrame 文生视频模式下也可能不为空 —— 用户选过图再切回文生视频时，
 *   图要留着，切回来还在。**发请求时按模式决定带不带它**，见 [toSubmitRequest]。
 * @param seedInput 用户在种子输入框里敲的原文。空 = 让网关随机。
 */
data class GenerateForm(
    val mode: GenerateMode = GenerateMode.ImageToVideo,
    val description: String = "",
    val soundscape: String = "",
    val music: String = "",
    val firstFrame: FirstFrameImage? = null,
    val duration: DurationPreset = DurationPreset.S5,
    val turbo: Boolean = true,
    val seedInput: String = "",
)

/** 表单不能提交的原因。每一种在界面上都有自己的一句话。 */
enum class FormProblem {
    /** 画面描述是空的。 */
    MissingDescription,

    /**
     * 图生视频却没选首帧图。
     *
     * ⚠️ **这道闸只在 App 端。** 网关不检查它：`I2VA` 不带首帧图会照常生成一个
     * 不带图的视频，不报错（2026-09-28 读网关代码确认，已记进 TODO）。
     */
    MissingFirstFrame,

    /** 种子填了，但不是 0 到 `Long.MAX_VALUE` 之间的整数。 */
    InvalidSeed,
}

/**
 * 检查表单能不能提交。
 *
 * @return 第一个问题；能提交则 `null`。按界面上从上到下的顺序报。
 */
fun validate(form: GenerateForm): FormProblem? = when {
    form.mode == GenerateMode.ImageToVideo && form.firstFrame == null -> FormProblem.MissingFirstFrame
    form.description.isBlank() -> FormProblem.MissingDescription
    form.seedInput.isNotBlank() && parseSeed(form.seedInput) == null -> FormProblem.InvalidSeed
    else -> null
}

/**
 * 把种子输入框的原文解析成种子。
 *
 * 只接受十进制非负整数；前后空白去掉。负数、小数、超出 `Long` 范围的都不接受 ——
 * 它们在网关那边要么被拒、要么含义不清，不如在这里就说清楚。
 *
 * @return 解析出的种子；空白输入或非法输入都返回 `null`，调用方用 [validate] 区分两者。
 */
fun parseSeed(input: String): Long? {
    val trimmed = input.trim()
    if (trimmed.isEmpty() || !trimmed.all { it in '0'..'9' }) return null
    return trimmed.toLongOrNull()
}

/**
 * 把表单变成 `POST /v1/jobs` 的请求体。
 *
 * ⚠️ 调用前必须先过 [validate]；这里不重复校验，传进来非法表单会抛。
 *
 * 规则（每一条都有 golden 用例守着，见 `SubmitRequestGoldenTest`）：
 * - **文生视频模式下，即使表单里留着之前选过的图，`first_frame_asset_id` 也发 `null`**；
 * - `width` / `height` / `steps` / `last_frame_asset_id` 固定 `null`，`loras` 固定 `[]`；
 * - prompt 三段只去首尾空白；环境声、背景音乐留空就发 `""`，**不在 App 里换成 `N/A`**
 *   （那条规则由网关负责）；
 * - 种子输入框留空发 `null`。
 */
fun toSubmitRequest(form: GenerateForm): JobSubmitRequestDto {
    val problem = validate(form)
    require(problem == null) { "表单没过校验就被拿去提交了：$problem" }

    val firstFrameAssetId = when (form.mode) {
        GenerateMode.TextToVideo -> null
        GenerateMode.ImageToVideo -> form.firstFrame!!.assetId
    }

    return JobSubmitRequestDto(
        type = "h3_video",
        mode = form.mode.wireValue,
        prompt = PromptPartsDto(
            description = form.description.trim(),
            soundscape = form.soundscape.trim(),
            music = form.music.trim(),
        ),
        firstFrameAssetId = firstFrameAssetId,
        lastFrameAssetId = null,
        width = null,
        height = null,
        durationSeconds = form.duration.seconds,
        turbo = form.turbo,
        steps = null,
        seed = parseSeed(form.seedInput),
        loras = emptyList(),
    )
}
