package com.icewalnut.grokgen

import com.icewalnut.grokgen.net.StreamingRequestBody
import com.icewalnut.grokgen.net.UploadOutcome
import com.icewalnut.grokgen.net.classifyUploadStatus
import com.icewalnut.grokgen.net.parseFastApiDetail
import okhttp3.MediaType.Companion.toMediaType
import okio.Buffer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.ByteArrayInputStream
import java.io.InputStream

/**
 * 上传的错误分档、FastAPI 错误体解析、以及请求体的可重放性。
 *
 * ⚠️ 这个文件住在 `src/test`，而扫源码那条约束（VS-30）**不扫测试目录** ——
 * 所以这里 import okhttp/okio 是可以的。
 */
class UploadOutcomeTest {

    // ---- 状态码分档 ----

    @Test
    fun `每个状态码落到自己那一档`() {
        assertEquals(UploadOutcome.EmptyBody, classifyUploadStatus(400, null))
        assertEquals(UploadOutcome.UnsupportedFormat, classifyUploadStatus(415, null))
        assertTrue(classifyUploadStatus(413, null) is UploadOutcome.TooLarge)
        assertTrue(classifyUploadStatus(422, null) is UploadOutcome.MalformedRequest)
        assertTrue(classifyUploadStatus(500, null) is UploadOutcome.NotAnImage)
        assertTrue(classifyUploadStatus(502, null) is UploadOutcome.ComfyUnreachable)
    }

    @Test
    fun `没见过的状态码归到 HttpError 并带上码，不许静默当成功`() {
        val outcome = classifyUploadStatus(418, """{"detail":"teapot"}""")
        assertTrue(outcome is UploadOutcome.HttpError)
        assertEquals(418, (outcome as UploadOutcome.HttpError).code)
        assertEquals("teapot", outcome.detail)
    }

    @Test
    fun `415 的原文要带回来`() {
        // 这是网关 2026-09-24 实测返回的原话。
        val body = """{"detail":"不支持的图片格式 .heic，允许：['.jpeg', '.jpg', '.png', '.webp']"}"""
        // 415 本身不带 detail（文案是固定的），但解析函数要能吃下它。
        assertTrue(parseFastApiDetail(body)!!.contains(".heic"))
    }

    // ---- FastAPI 的 detail 有两种形状 ----

    @Test
    fun `detail 是字符串时直接取`() {
        assertEquals("上传内容为空", parseFastApiDetail("""{"detail":"上传内容为空"}"""))
    }

    @Test
    fun `detail 是数组时也要能取出来，不能抛`() {
        // ⭐ 这是这一组里最重要的一条。
        //    422 的 detail 是数组，而 422 恰恰是「App 自己发错了」那一档 ——
        //    最不该在解析阶段就把信息丢掉，更不该在这里崩。
        val body = """
            {"detail":[{"loc":["body","file"],"msg":"Field required","type":"missing"}]}
        """.trimIndent()
        assertEquals("Field required", parseFastApiDetail(body))
    }

    @Test
    fun `detail 数组有多条时拼起来`() {
        val body = """{"detail":[{"msg":"A"},{"msg":"B"}]}"""
        assertEquals("A；B", parseFastApiDetail(body))
    }

    @Test
    fun `解析不了就返回 null，不要抛`() {
        // 这个函数是在「展示错误」的路上调的。
        // 在这里抛异常等于把一个可见的失败变成一次崩溃。
        assertNull(parseFastApiDetail(null))
        assertNull(parseFastApiDetail(""))
        assertNull(parseFastApiDetail("   "))
        assertNull(parseFastApiDetail("<html>502 Bad Gateway</html>"))
        assertNull(parseFastApiDetail("{not json"))
        assertNull(parseFastApiDetail("""{"other":"field"}"""))
        assertNull(parseFastApiDetail("""{"detail":""}"""))
    }

    // ---- 请求体可以被写第二遍 ----

    @Test
    fun `请求体连写两遍产出相同字节`() {
        // ⭐ 本轮性价比最高的一条测试：不用设备、不用网络，
        //    就能证明 OkHttp 重发请求体时不会发出半截或空 body。
        //
        //    真出问题的话表现是：网关返回 400「上传内容为空」，
        //    而真正的原因（流不能重读）一个字都不会出现在任何地方。
        val payload = ByteArray(200_000) { (it % 251).toByte() }
        val body = StreamingRequestBody(
            contentType = "image/jpeg".toMediaType(),
            declaredLength = payload.size.toLong(),
            openStream = { ByteArrayInputStream(payload) },   // 工厂：每次给一个新的流
            onProgress = { _, _ -> },
        )

        val first = Buffer().also { body.writeTo(it) }.readByteArray()
        val second = Buffer().also { body.writeTo(it) }.readByteArray()

        assertTrue("第一遍写出来的字节不对", payload.contentEquals(first))
        assertTrue("第二遍写出来的字节和第一遍不同 —— 重发会发出错的内容", first.contentEquals(second))
    }

    @Test
    fun `isOneShot 必须是 false，否则 OkHttp 不会重发`() {
        // 这条和上一条是一对：上一条证明「能重发」，这条证明「声明了能重发」。
        // 少了任何一条，另一条都没有意义。
        val body = StreamingRequestBody(
            contentType = null,
            declaredLength = 1,
            openStream = { ByteArrayInputStream(byteArrayOf(7)) },
            onProgress = { _, _ -> },
        )
        assertTrue(
            "isOneShot 是 true 的话 OkHttp 不会重发；" +
                "而如果同时把 openStream 改成持有一个现成的流，第二遍会写出零字节",
            !body.isOneShot(),
        )
    }

    @Test
    fun `进度回调加起来等于总字节数`() {
        val payload = ByteArray(150_000) { 1 }
        var last = 0L
        var calls = 0
        val body = StreamingRequestBody(
            contentType = null,
            declaredLength = payload.size.toLong(),
            openStream = { ByteArrayInputStream(payload) },
            onProgress = { sent, _ -> last = sent; calls++ },
        )

        body.writeTo(Buffer())

        assertEquals(payload.size.toLong(), last)
        assertTrue("进度应当被回调多次，实际 $calls 次", calls > 1)
    }

    @Test
    fun `contentLength 如实回报，未知时是 -1`() {
        fun bodyWith(length: Long) = StreamingRequestBody(
            contentType = null,
            declaredLength = length,
            openStream = { ByteArrayInputStream(ByteArray(0)) as InputStream },
            onProgress = { _, _ -> },
        )
        assertEquals(12345L, bodyWith(12345L).contentLength())
        // -1 会让 OkHttp 改用分块传输 —— 那时进度条没有分母，是可接受的降级。
        assertEquals(-1L, bodyWith(-1L).contentLength())
    }
}
