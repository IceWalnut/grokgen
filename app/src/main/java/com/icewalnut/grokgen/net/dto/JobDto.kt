package com.icewalnut.grokgen.net.dto

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement

/**
 * 一个任务的完整状态：`GET /v1/jobs/{id}`、列表的每一项、取消的返回值都是这个形状（契约 §2）。
 *
 * ⚠️⚠️ **可空字段给了默认值 `null` —— 这与 `JobSubmitRequestDto` 的规则正好相反，是故意的。**
 * - **请求**：`null` 必须写出来（契约要求），所以请求数据类不给默认值；
 * - **响应**：网关省略某个键时不能让解析崩掉，所以响应数据类给默认值。
 * 两个方向的风险不同：请求多一个或少一个键会被网关静默吞掉，
 * 响应少一个键则会让整页解析失败。
 */
@Serializable
data class JobDto(
    @SerialName("job_id") val jobId: String,
    /**
     * 契约的 8 个状态之一。**故意用 `String` 不用枚举** —— 网关将来加一个状态时，
     * 枚举会让整条响应解析失败，而字符串只会让那一项显示成「认不出的状态」。
     */
    val state: String,
    /**
     * 任务在跑时的细分阶段（网关从 ComfyUI 的 WebSocket 拿，M2R4b 起）。
     * **可以为 `null`**：刚开始、网关与 ComfyUI 的实时连接中断、或网关认不出当前节点时都为空。
     */
    val stage: String? = null,
    /** 采样进度 0..1，只在 `sampling` 阶段有值。 */
    val progress: Double? = null,
    /** ISO 8601 带时区，例如 `2026-09-28T05:58:08.419404+00:00`。 */
    @SerialName("created_at") val createdAt: String,
    /** 提交时的模式，原样回显（2026-09-28 网关加入）。 */
    val mode: String,
    /** 提交时的 prompt，**用户原文**：留空的是 `""`，不是 `N/A`。 */
    val prompt: PromptPartsDto,
    val normalized: NormalizedParamsDto,
    val notices: List<String> = emptyList(),
    val outputs: List<JobOutputDto> = emptyList(),
    @SerialName("failure_reason") val failureReason: FailureReasonDto? = null,
)

/**
 * 一个产物的位置。
 *
 * ⚠️ M1 阶段网关只填得出 `kind` 与文件位置；契约里的 `item_id` / `codec` 等是 M3 的目标形状。
 * App 取视频走 `GET /v1/jobs/{id}/video`，**用不到这里的文件名**，这里只是如实解析。
 */
@Serializable
data class JobOutputDto(
    val kind: String,
    val filename: String? = null,
    val subfolder: String? = null,
)

/**
 * 失败原因。
 *
 * @param kind `comfy_validation` / `out_of_memory` / `comfy_unreachable` / `timeout` / `internal`。
 * @param message 一句话，可直接显示。
 * @param detail 结构化原始信息（`comfy_validation` 时是 ComfyUI 的 `node_errors` 原文）。
 *   ⚠️ **契约要求不能丢** —— 排障时只有它有用。形状不定，所以原样存成 [JsonElement]。
 */
@Serializable
data class FailureReasonDto(
    val kind: String,
    val message: String,
    val detail: JsonElement? = null,
)

/** `GET /v1/jobs` 的响应。 */
@Serializable
data class JobListDto(
    val items: List<JobDto>,
    /** 过滤后、**被 `limit` 截断前**的条数。大于 `items.size` 说明列表被截掉了。 */
    val total: Int,
)
