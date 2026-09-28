package com.icewalnut.grokgen.net.dto

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * `POST /v1/jobs` 的请求体，字段与契约 §2 逐字对应。
 *
 * ⚠️⚠️ **所有属性都不给默认值 —— 这不是疏忽。**
 * kotlinx.serialization 默认 `encodeDefaults = false`：带默认值的属性等于默认值时
 * **直接从 JSON 里省略**。于是 `val width: Int? = null` 发出去是「没有 width 这个键」，
 * 而不是 `"width": null`。网关恰好也把缺省当 null 处理，所以**今天不会出错** ——
 * 但契约写的是 `null`，而且 `loras` 这种字段哪天默认值和网关不一致时，
 * 请求照样 200、行为悄悄不对。不给默认值 ⇒ 构造时每个字段都必须写明。
 *
 * VS-34 的 golden 测试守这一条（`SubmitRequestGoldenTest`）。
 */
@Serializable
data class JobSubmitRequestDto(
    /** 任务类型判别位，目前只有 `"h3_video"`。M4 接 SD 后会有第二种。 */
    val type: String,
    /** `"T2VA"` / `"I2VA"`。M2 不发 `"FL2VA"`。 */
    val mode: String,
    val prompt: PromptPartsDto,
    /**
     * 上传接口返回的 `asset_id`，**原样传回**。T2VA 时为 `null`。
     *
     * ⚠️ 网关**不检查**「I2VA 却没带首帧图」—— 会照常生成一个不带图的视频。
     * 这道闸在 App 端（`validate`），见 `model/GenerateForm.kt`。
     */
    @SerialName("first_frame_asset_id") val firstFrameAssetId: String?,
    /** 尾帧，只有 FL2VA 用。M2 恒为 `null`。 */
    @SerialName("last_frame_asset_id") val lastFrameAssetId: String?,
    /**
     * 期望画布尺寸。M2 恒为 `null` —— 让网关按首帧图比例推画布（契约 §2）。
     * 首帧是**拉伸**不是裁剪，App 自己填一个比例不符的尺寸会让图变形且不报错。
     */
    val width: Int?,
    val height: Int?,
    /** 期望时长。**网关会换算成 17k+5 帧**，实际时长在响应的 `normalized` 里。 */
    @SerialName("duration_seconds") val durationSeconds: Double,
    /** 选定一整套采样参数，不是一个开关（契约 §2）。 */
    val turbo: Boolean,
    /** M2 恒为 `null` —— 由 [turbo] 选定的那套参数决定。 */
    val steps: Int?,
    /** `null` 时网关随机取一个并回报。 */
    val seed: Long?,
    /**
     * M6 才实现，现在网关**只接受空数组**，非空会 422。
     * 元素类型暂写 `String`：M6 之前它永远是空的，真实形状等 M6 定。
     */
    val loras: List<String>,
)

/**
 * Director 的三段式 prompt。
 *
 * ⚠️ 环境声、背景音乐留空时发 **空字符串**，不要在 App 里换成 `N/A` ——
 * 那条规则由网关负责（`server/app/models/video_job.py` 的 `PromptParts`）。
 */
@Serializable
data class PromptPartsDto(
    val description: String,
    val soundscape: String,
    val music: String,
)
