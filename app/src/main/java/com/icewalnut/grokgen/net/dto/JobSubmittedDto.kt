package com.icewalnut.grokgen.net.dto

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * `POST /v1/jobs` 的成功响应（契约 §2）。
 */
@Serializable
data class JobSubmittedDto(
    @SerialName("job_id") val jobId: String,
    /** 提交后的状态，正常是 `"queued"`。 */
    val state: String,
    val normalized: NormalizedParamsDto,
    /**
     * 网关生成的中文句子，说明「你要的」和「你会得到的」差在哪。
     *
     * ⚠️ **原文显示，不要按前缀解析。**
     */
    val notices: List<String>,
)

/**
 * 网关归一化之后**真正送去生成**的参数。
 *
 * ⚠️ **不是装饰，App 必须显示**（契约 §2）：`width`/`height`、
 * `actualDurationSeconds`、`seed` 三个值都可能和用户填的不一样。
 *
 * ⚠️ 网关实际返回的这个对象里**还多一个 `notices`**（它的 `NormalizedParams` 模型带着它），
 * 与外层那份相同。这里故意不声明 —— 契约只列了九个字段，外层那份才是契约里的。
 * 靠 `ignoreUnknownKeys` 忽略掉。
 */
@Serializable
data class NormalizedParamsDto(
    val width: Int,
    val height: Int,
    @SerialName("length_frames") val lengthFrames: Int,
    @SerialName("actual_duration_seconds") val actualDurationSeconds: Double,
    val seed: Long,
    val sampler: String,
    val steps: Int,
    @SerialName("shift_video") val shiftVideo: Double,
    @SerialName("shift_audio") val shiftAudio: Double,
)
