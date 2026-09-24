package com.icewalnut.grokgen.net.dto

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * `GET /v1/health` 的响应。
 *
 * 字段与接口契约逐字对应。**这一层不做任何解释和兜底** ——
 * 「字段为空时界面显示什么」那个决定在 `ui/` 那边做一次，
 * 不散在每个 Composable 里的 `?: "未知"`。
 */
@Serializable
data class HealthDto(
    val gateway: GatewayDto,
    val comfy: ComfyDto,
)

@Serializable
data class GatewayDto(
    /** 网关自己的状态。实现里恒为 `"ok"` —— 它能回话本身就是信号。 */
    val status: String,
    /**
     * 网关所在检出的 git 短哈希，取不到时是 `"unknown"`。
     *
     * ⚠️ 它在网关进程里**只算一次**（`lru_cache`），
     * 所以重新部署但没重启进程时，这里报的是旧的。
     */
    val version: String,
    /**
     * 服务器上有没有 `ffprobe`。
     *
     * ⚠️ **为 `false` 时必须在界面上单独提示**，不能混在「在线」里。
     * 没有它，网关读不出上传图片的尺寸；而不指定画布尺寸时画布就是按图片比例
     * 推算的，读不出来就**静默退回默认尺寸**，表现为首帧图被拉伸变形 ——
     * 哪里都不报错。
     */
    val ffprobe: Boolean,
)

/**
 * ComfyUI 的状态。
 *
 * ⚠️ **这个对象的形状是变体的**：可达时有 [gpu] / [vramTotalBytes] / [vramFreeBytes]，
 * 不可达时这三个键**根本不存在**（不是 `null`），取而代之的是 [error]。
 *
 * ⇒ **必须先判 [reachable] 再取别的**，不能靠「取出来是不是 null」倒推。
 */
@Serializable
data class ComfyDto(
    val reachable: Boolean,
    /** 仅在不可达时存在。内容是网关那边格式化好的原因，含 ComfyUI 的地址。 */
    val error: String? = null,
    /** 仅在可达时存在，形如 `cuda:0 NVIDIA GeForce RTX 4080 SUPER : cudaMallocAsync`。 */
    val gpu: String? = null,
    @SerialName("vram_total_bytes") val vramTotalBytes: Long? = null,
    @SerialName("vram_free_bytes") val vramFreeBytes: Long? = null,
)
