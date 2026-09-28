package com.icewalnut.grokgen

import com.icewalnut.grokgen.net.ConnectionOutcome
import com.icewalnut.grokgen.net.SubmitOutcome
import com.icewalnut.grokgen.net.classifySubmitFailure
import com.icewalnut.grokgen.net.classifySubmitStatus
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.IOException
import java.net.ConnectException
import java.net.SocketTimeoutException
import java.net.UnknownHostException

/**
 * 提交任务的错误分档。
 *
 * ⚠️ 状态码的含义来自读网关代码（`server/app/api/jobs.py`），**没有对着真实网关逐个触发过**。
 */
class SubmitOutcomeTest {

    // ---- 状态码 ----

    @Test
    fun `每个状态码落到自己那一档`() {
        assertTrue(classifySubmitStatus(400, null) is SubmitOutcome.FirstFrameUnreadable)
        assertTrue(classifySubmitStatus(422, null) is SubmitOutcome.MalformedRequest)
        assertTrue(classifySubmitStatus(503, null) is SubmitOutcome.GatewayClosing)
    }

    @Test
    fun `没见过的状态码归到 HttpError 并带上码`() {
        val outcome = classifySubmitStatus(500, """{"detail":"boom"}""")
        assertTrue(outcome is SubmitOutcome.HttpError)
        assertEquals(500, (outcome as SubmitOutcome.HttpError).code)
        assertEquals("boom", outcome.detail)
    }

    @Test
    fun `400 带回网关的原话`() {
        val body = """{"detail":"读不出首帧图 grokgen/img_x.png 的尺寸：ffprobe 失败"}"""
        val outcome = classifySubmitStatus(400, body) as SubmitOutcome.FirstFrameUnreadable
        assertTrue(outcome.detail!!.contains("grokgen/img_x.png"))
    }

    @Test
    fun `422 数组形状的 detail 也能取出文字`() {
        // FastAPI 校验失败的真实形状：detail 是数组。loras 非空时就是这个。
        val body = """{"detail":[{"loc":["body","loras"],"msg":"Value error, LoRA 自由挑选要到 M6 才实现","type":"value_error"}]}"""
        val outcome = classifySubmitStatus(422, body) as SubmitOutcome.MalformedRequest
        assertTrue(outcome.detail!!.contains("M6"))
    }

    // ---- 异常 ----

    /**
     * ⚠️ 这一条是提交与上传/连接最大的不同：超时 = 不知道网关收没收到。
     * 说成「没连上」，用户会再点一次 —— 两个任务、两次 GPU。
     */
    @Test
    fun `超时一律是 MaybeSubmitted 不是连不上`() {
        assertEquals(SubmitOutcome.MaybeSubmitted, classifySubmitFailure(SocketTimeoutException("timeout")))
        assertEquals(SubmitOutcome.MaybeSubmitted, classifySubmitFailure(SocketTimeoutException("connect timed out")))
    }

    @Test
    fun `被包在 IOException 里的超时也要认出来`() {
        val wrapped = IOException("unexpected end of stream", SocketTimeoutException("timeout"))
        assertEquals(SubmitOutcome.MaybeSubmitted, classifySubmitFailure(wrapped))
    }

    @Test
    fun `确定没发出去的几种复用连接页的分档`() {
        assertEquals(
            SubmitOutcome.Unreachable(ConnectionOutcome.TailscaleDown),
            classifySubmitFailure(UnknownHostException("icewalnut")),
        )
        assertEquals(
            SubmitOutcome.Unreachable(ConnectionOutcome.ConnectionRefused),
            classifySubmitFailure(ConnectException("refused")),
        )
    }

    @Test
    fun `没见过的异常不伪装成别的`() {
        val boom = IllegalStateException("boom")
        val outcome = classifySubmitFailure(boom)
        assertTrue(outcome is SubmitOutcome.Unexpected)
        assertEquals(boom, (outcome as SubmitOutcome.Unexpected).throwable)
    }
}
