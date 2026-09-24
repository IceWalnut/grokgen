package com.icewalnut.grokgen.net

import com.icewalnut.grokgen.net.dto.UploadedImageDto
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonObject

/**
 * 上传一张图之后到底发生了什么。
 *
 * 与 [ConnectionOutcome] 同一个形式。**传输层那几档直接复用它** ——
 * 「手机没连上 Tailscale」在上传时和在连接时是同一件事，说法应当一样。
 */
sealed interface UploadOutcome {

    /** 成功。 */
    data class Uploaded(val image: UploadedImageDto) : UploadOutcome

    /**
     * 连不上，或者连上了但传输失败。
     *
     * 复用连接页那套分档与文案，不要抄第二份。
     */
    data class Unreachable(val reason: ConnectionOutcome) : UploadOutcome

    // ---- 网关明确拒绝的几档 ----

    /**
     * 415：网关不认这个后缀。
     *
     * ⚠️ **正常情况下走不到这一档** —— `planUpload()` 保证我们只发
     * `.jpg` / `.png` / `.webp`。真看到它说明转码那条路被绕过了。
     */
    data object UnsupportedFormat : UploadOutcome

    /** 413：超过网关 32 MiB 的上限。 */
    data class TooLarge(val detail: String?) : UploadOutcome

    /** 400：网关收到的 body 是空的。 */
    data object EmptyBody : UploadOutcome

    /** 502：网关活着，但它连不上 ComfyUI，图存不进去。 */
    data class ComfyUnreachable(val detail: String?) : UploadOutcome

    /**
     * 500：网关读不出这张图的尺寸。
     *
     * ⭐ 2026-09-24 之后，「内容不是图片」也落在这一档 ——
     * 在那之前它会静默返回 200 加一个 0×0 的 asset。
     */
    data class NotAnImage(val detail: String?) : UploadOutcome

    /** 422：请求形状不对。**这是 App 的 bug，不是用户操作错了。** */
    data class MalformedRequest(val detail: String?) : UploadOutcome

    /** 其他非 2xx。原样带上状态码与响应体，不要压成一句话。 */
    data class HttpError(val code: Int, val detail: String?) : UploadOutcome

    // ---- 还没发出去就失败的几档 ----

    /** 图读不到了 —— 上传途中被删、或者选图授权失效。 */
    data object SourceUnreadable : UploadOutcome

    /** 解码或重编码失败。 */
    data class TranscodeFailed(val reason: String) : UploadOutcome

    /** 图太大，手机上解不开。 */
    data object TooBigToDecode : UploadOutcome

    /** 没见过的失败。**不要伪装成别的。** */
    data class Unexpected(val throwable: Throwable) : UploadOutcome
}

/**
 * 把网关的 HTTP 状态码翻译成 [UploadOutcome]。
 *
 * ⚠️ **纯函数**，所以能在开发机上直接测。
 *
 * 每个码的含义都是 2026-09-24 对着真实网关实测出来的，不是照契约猜的。
 */
fun classifyUploadStatus(code: Int, errorBody: String?): UploadOutcome {
    val detail = parseFastApiDetail(errorBody)
    return when (code) {
        400 -> UploadOutcome.EmptyBody
        413 -> UploadOutcome.TooLarge(detail)
        415 -> UploadOutcome.UnsupportedFormat
        422 -> UploadOutcome.MalformedRequest(detail)
        500 -> UploadOutcome.NotAnImage(detail)
        502 -> UploadOutcome.ComfyUnreachable(detail)
        else -> UploadOutcome.HttpError(code, detail)
    }
}

private val lenientJson = Json { ignoreUnknownKeys = true; isLenient = true }

/**
 * 从 FastAPI 的错误体里取出给人看的那句话。
 *
 * ⚠️ **`detail` 有两种形状**：
 * - 一般错误：`{"detail": "不支持的图片格式 .heic，允许：[...]"}`
 * - 422：`{"detail": [ {"loc": [...], "msg": "...", "type": "..."}, ... ]}`
 *
 * 按字符串硬取会在 422 上抛异常 —— 而 **422 恰恰是「App 自己发错了」那一档**，
 * 最不该在解析阶段就把信息丢掉。
 *
 * ⚠️ 解析失败时返回 `null`，**不要抛** —— 这个函数是在展示错误的路上调的，
 * 在这里抛异常等于把一个可见的失败变成一次崩溃。
 *
 * @return 一句话；取不到则 `null`。
 */
fun parseFastApiDetail(body: String?): String? {
    val text = body?.trim().orEmpty()
    if (text.isEmpty()) return null

    return try {
        when (val detail = lenientJson.parseToJsonElement(text).jsonObject["detail"]) {
            is JsonPrimitive -> detail.content.takeIf { it.isNotBlank() }
            is JsonArray -> detail
                .mapNotNull { (it as? JsonObject)?.get("msg")?.let { m -> (m as? JsonPrimitive)?.content } }
                .takeIf { it.isNotEmpty() }
                ?.joinToString("；")
            else -> null
        }
    } catch (t: Throwable) {
        // 响应体不是 JSON（比如反向代理返回的一页 HTML）。
        // 返回 null，让上层用自己的兜底文案 —— 但**不要把原文吞掉当成成功**。
        null
    }
}
