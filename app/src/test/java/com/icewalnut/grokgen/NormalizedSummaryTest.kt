package com.icewalnut.grokgen

import com.icewalnut.grokgen.model.SummaryRow
import com.icewalnut.grokgen.model.formatSecondsForChip
import com.icewalnut.grokgen.model.summarizeNormalized
import com.icewalnut.grokgen.net.GatewayJson
import com.icewalnut.grokgen.net.dto.JobSubmittedDto
import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * 「网关实际使用的参数」那张卡片的格式化，以及提交响应能不能被解析。
 */
class NormalizedSummaryTest {

    /**
     * 网关提交响应的真实形状。
     *
     * ⚠️ 注意 `normalized` 里**多了一个 `notices`**：网关的 `NormalizedParams` 模型带着它，
     * 契约 §2 的示例里没有。这里故意写上，守住「多一个键不让 App 崩」。
     * `actual_duration_seconds` 也是网关的原值 124/24 = 5.1666…，**网关不取整**。
     */
    private val submitResponse = """
        {
          "job_id": "job_20260928_0001",
          "state": "queued",
          "normalized": {
            "width": 736, "height": 416,
            "length_frames": 124, "actual_duration_seconds": 5.166666666666667,
            "seed": 849302114, "sampler": "euler", "steps": 8,
            "shift_video": 6.0, "shift_audio": 3.0,
            "notices": ["时长已调整：请求 5.00 秒，实际 124 帧 = 5.17 秒（模型只接受 17k+5 的帧数）"]
          },
          "notices": ["时长已调整：请求 5.00 秒，实际 124 帧 = 5.17 秒（模型只接受 17k+5 的帧数）"]
        }
    """.trimIndent()

    @Test
    fun `提交响应能被解析 normalized 里多出来的键被忽略`() {
        val job = GatewayJson.decodeFromString(JobSubmittedDto.serializer(), submitResponse)
        assertEquals("job_20260928_0001", job.jobId)
        assertEquals(124, job.normalized.lengthFrames)
        assertEquals(1, job.notices.size)
    }

    @Test
    fun `五行覆盖契约要求显示的九个字段`() {
        val job = GatewayJson.decodeFromString(JobSubmittedDto.serializer(), submitResponse)
        assertEquals(
            listOf(
                SummaryRow("分辨率", "736 × 416"),
                SummaryRow("时长", "5.17 秒 · 124 帧"),
                SummaryRow("种子", "849302114"),
                SummaryRow("采样", "euler · 8 步 · Turbo"),
                SummaryRow("shift", "6.0 / 3.0"),
            ),
            summarizeNormalized(job.normalized, turbo = true),
        )
    }

    @Test
    fun `时长按钮上整数秒不带小数点`() {
        assertEquals("5", formatSecondsForChip(5.0))
        assertEquals("8", formatSecondsForChip(8.0))
        assertEquals("2.50", formatSecondsForChip(2.5))
    }

    @Test
    fun `非 Turbo 档显示为标准`() {
        val job = GatewayJson.decodeFromString(JobSubmittedDto.serializer(), submitResponse)
        val sampling = summarizeNormalized(job.normalized, turbo = false).first { it.label == "采样" }
        assertEquals("euler · 8 步 · 标准", sampling.value)
    }

    @Test
    fun `不知道档位时不猜 只显示 sampler 与步数`() {
        val job = GatewayJson.decodeFromString(JobSubmittedDto.serializer(), submitResponse)
        val sampling = summarizeNormalized(job.normalized, turbo = null).first { it.label == "采样" }
        assertEquals("euler · 8 步", sampling.value)
    }
}
