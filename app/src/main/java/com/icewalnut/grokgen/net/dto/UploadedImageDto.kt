package com.icewalnut.grokgen.net.dto

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * `POST /v1/uploads/image` 的成功响应。
 *
 * 2026-09-24 实测的真实响应：
 * ```json
 * {"asset_id":"grokgen/img_20260924_165801_fc9609.png","width":4,"height":3,"size_bytes":81}
 * ```
 */
@Serializable
data class UploadedImageDto(
    /**
     * ⚠️ **这是一个路径，不是不透明 id** —— 它就是网关将来填给 ComfyUI
     * `LoadImage` 的那个值（相对 ComfyUI `input/` 的路径）。
     *
     * ⚠️ **原样存、原样传回，不要解析它、不要自己拼。**
     * 网关自己生成落盘文件名，重名时 ComfyUI 还会再改一次名 ——
     * 拿本地文件名去拼会引用到另一张图**且不报错**（网关侧 VS-15 专门测过这条）。
     */
    @SerialName("asset_id") val assetId: String,

    /**
     * 网关用 ffprobe 从落盘文件读出来的像素宽高。
     *
     * ⚠️ **不指定画布尺寸时，视频画布就是按这两个数推出来的**（契约 §2）。
     * 所以这两个数错了，最终视频的画幅就错了，而**那不会报错**。
     * ⇒ 上传页要把它和本机读到的尺寸并排显示出来。
     */
    val width: Int,
    val height: Int,

    @SerialName("size_bytes") val sizeBytes: Long,
)
