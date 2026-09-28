package com.icewalnut.grokgen

import com.icewalnut.grokgen.net.CancelOutcome
import com.icewalnut.grokgen.net.ConnectionOutcome
import com.icewalnut.grokgen.net.GatewayApi
import com.icewalnut.grokgen.net.GatewayJson
import com.icewalnut.grokgen.net.JobClient
import com.icewalnut.grokgen.net.JobQueryOutcome
import com.icewalnut.grokgen.net.classifyCancelStatus
import com.icewalnut.grokgen.net.classifyJobQueryStatus
import com.icewalnut.grokgen.net.dto.HealthDto
import com.icewalnut.grokgen.net.dto.JobDto
import com.icewalnut.grokgen.net.dto.JobListDto
import com.icewalnut.grokgen.net.dto.JobSubmitRequestDto
import com.icewalnut.grokgen.net.dto.JobSubmittedDto
import com.icewalnut.grokgen.net.dto.UploadedImageDto
import kotlinx.coroutines.runBlocking
import okhttp3.MultipartBody
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import retrofit2.Response
import java.net.SocketTimeoutException
import kotlin.coroutines.cancellation.CancellationException

/**
 * 查询与取消的分档，以及 [JobClient] 对异常的处理。
 *
 * ⚠️ 这个文件住在 `src/test`，分层约束（VS-30）不扫测试目录，所以可以 import okhttp/retrofit。
 */
class JobQueryOutcomeTest {

    // ---- 状态码 ----

    @Test
    fun `查询 404 是不存在 别再问了`() {
        assertEquals(JobQueryOutcome.NotFound, classifyJobQueryStatus(404, """{"detail":"没有任务 x"}"""))
    }

    @Test
    fun `查询的其他非 2xx 带上码与原话`() {
        val outcome = classifyJobQueryStatus(500, """{"detail":"boom"}""") as JobQueryOutcome.HttpError
        assertEquals(500, outcome.code)
        assertEquals("boom", outcome.detail)
    }

    /**
     * VS-37 的重点：409 是「已经结束了」，不是失败，也不能和 404 混成一档。
     */
    @Test
    fun `取消 409 是已经结束了 404 是不存在 502 是中断没发出去`() {
        val body = """{"detail":"任务 job_x 已经结束或正在收尾，不能取消"}"""
        val finished = classifyCancelStatus(409, body)
        assertTrue(finished is CancelOutcome.AlreadyFinished)
        assertTrue((finished as CancelOutcome.AlreadyFinished).detail!!.contains("已经结束"))

        assertEquals(CancelOutcome.NotFound, classifyCancelStatus(404, null))
        assertTrue(classifyCancelStatus(502, null) is CancelOutcome.ComfyUnreachable)
        assertTrue(classifyCancelStatus(500, null) is CancelOutcome.HttpError)
    }

    // ---- JobClient 对异常的处理 ----

    /** 只实现任务那三个接口的假网关，其余调用直接失败。 */
    private class FakeGateway(
        private val onGetJob: suspend () -> Response<JobDto>,
    ) : GatewayApi {
        override suspend fun getJob(jobId: String): Response<JobDto> = onGetJob()
        override suspend fun listJobs(limit: Int): Response<JobListDto> = error("不该调到")
        override suspend fun cancelJob(jobId: String): Response<JobDto> = error("不该调到")
        override suspend fun health(): Response<HealthDto> = error("不该调到")
        override suspend fun uploadImage(file: MultipartBody.Part): Response<UploadedImageDto> = error("不该调到")
        override suspend fun submitJob(body: JobSubmitRequestDto): Response<JobSubmittedDto> = error("不该调到")
    }

    /**
     * ⚠️ 这一条守的是「退到后台就停止轮询」：收集轮询的协程被取消时，
     * 正在进行的请求抛 `CancellationException`。要是被当成网络失败吞掉，
     * 轮询循环会拿着一个「连不上」继续跑下去。
     */
    @Test
    fun `协程取消原样抛出 不被当成连不上吞掉`() = runBlocking {
        val gateway = FakeGateway { throw CancellationException("收集被取消") }
        try {
            JobClient.getJob(gateway, "job_x")
            fail("CancellationException 被吞掉了 —— 退到后台时轮询会停不下来")
        } catch (expected: CancellationException) {
            // 预期
        }
    }

    @Test
    fun `网络超时归到这一次没问到 不是任务失败`() = runBlocking {
        val gateway = FakeGateway { throw SocketTimeoutException("timeout") }
        val outcome = JobClient.getJob(gateway, "job_x")
        assertEquals(JobQueryOutcome.Unreachable(ConnectionOutcome.NoAnswer), outcome)
    }

    @Test
    fun `任务响应能被解析 包括新加的 mode 与 prompt 以及失败原因的原始 detail`() {
        // 失败任务的形状；detail 是 ComfyUI node_errors 的原文，契约要求不能丢。
        val body = """
            {"job_id":"job_20260928_0002","state":"failed","stage":null,"progress":null,
             "created_at":"2026-09-28T06:10:00.000000+00:00",
             "mode":"T2VA","prompt":{"description":"海浪","soundscape":"","music":""},
             "normalized":{"width":736,"height":416,"length_frames":124,"actual_duration_seconds":5.166666666666667,
                           "seed":1,"sampler":"euler","steps":8,"shift_video":6.0,"shift_audio":3.0,"notices":[]},
             "notices":[],"outputs":[],
             "failure_reason":{"kind":"comfy_validation","message":"workflow 校验失败",
                               "detail":{"2":{"errors":[{"type":"required_input_missing","details":"channels"}]}}}}
        """.trimIndent()

        val job = GatewayJson.decodeFromString(JobDto.serializer(), body)

        assertEquals("T2VA", job.mode)
        assertEquals("", job.prompt.music)
        assertEquals("comfy_validation", job.failureReason!!.kind)
        assertTrue(job.failureReason!!.detail.toString().contains("required_input_missing"))
    }
}
