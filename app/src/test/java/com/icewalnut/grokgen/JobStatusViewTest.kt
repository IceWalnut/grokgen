package com.icewalnut.grokgen

import com.icewalnut.grokgen.model.JobPhase
import com.icewalnut.grokgen.model.isTerminalState
import com.icewalnut.grokgen.model.jobStatusView
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 契约的 8 个状态 → 界面上的呈现（App 架构文档 §6）。
 */
class JobStatusViewTest {

    @Test
    fun `八个状态各落到自己那一组`() {
        val expected = mapOf(
            "queued" to JobPhase.Queued,
            "preparing" to JobPhase.Queued,
            "submitted" to JobPhase.Generating,
            "running" to JobPhase.Generating,
            "postprocessing" to JobPhase.Finishing,
            "done" to JobPhase.Done,
            "failed" to JobPhase.Failed,
            "cancelled" to JobPhase.Cancelled,
        )
        expected.forEach { (state, phase) ->
            assertEquals("state=$state", phase, jobStatusView(state, null, null).phase)
        }
    }

    @Test
    fun `只有排队中和生成中能取消 收尾中不能`() {
        assertTrue(jobStatusView("queued", null, null).canCancel)
        assertTrue(jobStatusView("running", null, null).canCancel)
        // 契约：postprocessing 取消返回 409。按钮直接不给。
        assertFalse(jobStatusView("postprocessing", null, null).canCancel)
        assertFalse(jobStatusView("done", null, null).canCancel)
    }

    @Test
    fun `终态是白名单 认不出的状态不算终态`() {
        assertTrue(isTerminalState("done"))
        assertTrue(isTerminalState("failed"))
        assertTrue(isTerminalState("cancelled"))
        assertFalse(isTerminalState("running"))
        assertFalse(isTerminalState("postprocessing"))
        // 网关将来加的新状态：宁可多问几次，也不要把还在跑的任务当成结束了。
        assertFalse(isTerminalState("paused"))
    }

    @Test
    fun `认不出的状态原样显示 不崩溃`() {
        val view = jobStatusView("paused", null, null)
        assertEquals(JobPhase.Unknown, view.phase)
        assertTrue(view.label.contains("paused"))
        assertFalse(view.isTerminal)
        assertFalse(view.canCancel)
    }

    // ---- stage / progress：现在恒为 null，但契约定义了它们有值时的样子 ----

    @Test
    fun `stage 为空时显示不带百分比的生成中 不是异常`() {
        val view = jobStatusView("running", null, null)
        assertEquals("生成中", view.label)
        assertNull(view.progressFraction)
    }

    @Test
    fun `加载模型单独显示 不和采样混成生成中`() {
        val view = jobStatusView("running", "loading_model", null)
        assertEquals("正在加载模型", view.label)
        assertTrue(view.explanation!!.contains("无关"))
    }

    @Test
    fun `采样阶段带百分比`() {
        val view = jobStatusView("running", "sampling", 0.42)
        assertEquals("采样中 42%", view.label)
        assertEquals(0.42f, view.progressFraction!!, 1e-6f)
    }

    @Test
    fun `采样但还没有百分比时不编造数字`() {
        val view = jobStatusView("running", "sampling", null)
        assertEquals("采样中", view.label)
        assertNull(view.progressFraction)
    }

    @Test
    fun `越界的 progress 夹到 0 到 1 之间`() {
        assertEquals("采样中 100%", jobStatusView("running", "sampling", 1.7).label)
        assertEquals("采样中 0%", jobStatusView("running", "sampling", -0.3).label)
    }

    @Test
    fun `progress 只在采样阶段画进度条`() {
        // 契约：progress 只在 sampling 时有意义。别的阶段即便带了值也不画。
        assertNull(jobStatusView("running", "encoding", 0.5).progressFraction)
        assertEquals("编码中", jobStatusView("running", "encoding", null).label)
        assertEquals("解码中", jobStatusView("running", "decoding_video", null).label)
    }
}
