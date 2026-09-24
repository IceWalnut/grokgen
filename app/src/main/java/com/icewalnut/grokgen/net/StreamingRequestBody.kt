package com.icewalnut.grokgen.net

import okhttp3.MediaType
import okhttp3.RequestBody
import okio.BufferedSink
import java.io.InputStream

/**
 * 把一个输入流直接写进 socket 的请求体，**不先读成 ByteArray**。
 *
 * 相册原图几 MB 到十几 MB（本机实测最大 12.1 MB），读成字节数组是没必要的内存峰值，
 * 而且转码那条路上还会和一个约 48 MB 的 bitmap 抢内存。
 *
 * @param contentType 声明的类型。网关**不看它**（它只看 filename 的后缀），
 *   但写对了在抓包时更好认。
 * @param declaredLength 字节数。`-1` 表示未知，OkHttp 会改用分块传输。
 * @param openStream ⭐ **打开流的工厂，不是一个已经打开的流。** 理由见 [isOneShot]。
 * @param onProgress 已发送字节数 / 总字节数。**调用方负责节流** ——
 *   每次 socket 写都回调一次会把 Compose 重组压垮。
 */
internal class StreamingRequestBody(
    private val contentType: MediaType?,
    private val declaredLength: Long,
    private val openStream: () -> InputStream,
    private val onProgress: (sent: Long, total: Long) -> Unit,
) : RequestBody() {

    override fun contentType(): MediaType? = contentType

    override fun contentLength(): Long = declaredLength

    /**
     * 返回 `false` = 「我可以被写第二遍」。
     *
     * ⚠️⚠️ **这个返回值和 [openStream] 是一个工厂这件事必须一起改。**
     *
     * OkHttp 在几种情况下会重发请求体：3xx 重定向、401/407 认证挑战。
     * （`retryOnConnectionFailure(false)` 只关掉了**传输层重试**，管不到这两条。）
     *
     * 我们能承受重发，是因为 [openStream] 每次都**重新打开一次流**。
     * 哪天有人把它改成持有一个现成的 `InputStream` 却忘了把这里改成 `true`，
     * 第二次写会拿到一个**已经读空的流** —— 于是发出去一个零字节的 body，
     * 网关返回 **400「上传内容为空」**，
     * 而真正的原因（流不能重读）**一个字都不会出现在任何地方**。
     */
    override fun isOneShot(): Boolean = false

    override fun writeTo(sink: BufferedSink) {
        var sent = 0L
        openStream().use { input ->
            val buffer = ByteArray(BUFFER_BYTES)
            while (true) {
                val read = input.read(buffer)
                if (read == -1) break
                sink.write(buffer, 0, read)
                sent += read
                onProgress(sent, declaredLength)
            }
        }
    }

    private companion object {
        const val BUFFER_BYTES = 64 * 1024
    }
}
