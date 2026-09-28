package com.icewalnut.grokgen

import com.icewalnut.grokgen.data.JobPoller
import com.icewalnut.grokgen.net.ConnectionOutcome
import com.icewalnut.grokgen.net.JobQueryOutcome
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.flow.collect
import kotlinx.coroutines.flow.toList
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.yield
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * **VS-36 的 Level 2 证据**：轮询在该停的时候停，判据是**数请求次数**。
 *
 * 等待函数被换成「立刻返回、记下时长」的版本，所以这些测试在毫秒内跑完，
 * 而且能精确断言「一共问了几次」「每次之间等了多久」。
 *
 * ⚠️ 执行文档要求「不要用跑一会儿看日志来判」—— 那判不出「多轮询了几次」。
 */
class JobPollerTest {

    /**
     * 按顺序吐出预设结果、并数被调了几次的假查询。
     *
     * ⚠️ 默认**剧本用完还被调就直接失败** —— 否则「该停没停」会表现为测试挂死，
     * 而不是一条说清楚原因的失败。只有专门测「一直不停」的场景才打开 [repeatLast]。
     */
    private class ScriptedFetch<T>(
        private val script: List<JobQueryOutcome<T>>,
        private val repeatLast: Boolean = false,
    ) {
        var calls = 0
            private set

        suspend fun fetch(): JobQueryOutcome<T> {
            if (calls >= script.size && !repeatLast) {
                throw AssertionError("剧本只有 ${script.size} 步，却被问了第 ${calls + 1} 次 —— 该停的时候没停")
            }
            val outcome = script[calls.coerceAtMost(script.lastIndex)]
            calls++
            return outcome
        }
    }

    private val waits = mutableListOf<Long>()
    private val poller = JobPoller(wait = { waits += it })

    private val unreachable = JobQueryOutcome.Unreachable(ConnectionOutcome.NoAnswer)

    @Test
    fun `任务在第 N 次查询时到终态 恰好问 N 次就停`() = runBlocking {
        val states = listOf("queued", "running", "running", "done")
        val fetch = ScriptedFetch(states.map { JobQueryOutcome.Found(it) })

        val emitted = poller.poll(fetch::fetch, isFinished = { it == "done" }).toList()

        assertEquals("到终态之后不许再问", 4, fetch.calls)
        assertEquals(4, emitted.size)
        // 到终态那一次之后不等待 —— 等待只发生在两次查询之间。
        assertEquals(listOf(2_000L, 2_000L, 2_000L), waits)
    }

    @Test
    fun `收集被取消后不再发请求 这就是退到后台时的样子`() = runBlocking {
        // 永远不到终态的任务：只有取消收集能让它停。
        val fetch = ScriptedFetch(listOf(JobQueryOutcome.Found("running")), repeatLast = true)
        val thirdCallSeen = CompletableDeferred<Unit>()
        val gatedPoller = JobPoller(wait = { if (fetch.calls >= 3) thirdCallSeen.complete(Unit); yield() })

        val job = launch {
            gatedPoller.poll(fetch::fetch, isFinished = { false }).collect()
        }
        thirdCallSeen.await()
        job.cancelAndJoin()
        val callsAtCancel = fetch.calls

        // 取消之后给调度器一些机会 —— 如果循环还活着，计数会继续涨。
        repeat(20) { yield() }
        assertEquals("取消收集之后请求数不许再增长", callsAtCancel, fetch.calls)
        assertTrue(callsAtCancel >= 3)
    }

    @Test
    fun `连续失败时退避 成功一次就恢复正常间隔 失败不结束轮询`() = runBlocking {
        val fetch = ScriptedFetch(
            listOf(
                unreachable, unreachable, unreachable,
                JobQueryOutcome.Found("running"),
                JobQueryOutcome.Found("done"),
            ),
        )

        val emitted = poller.poll(fetch::fetch, isFinished = { it == "done" }).toList()

        assertEquals(5, fetch.calls)
        // 失败 1 次 → 2 秒；2 次 → 4 秒；3 次 → 8 秒；成功 → 回到 2 秒。
        assertEquals(listOf(2_000L, 4_000L, 8_000L, 2_000L), waits)
        // 前三次发出去的是「这次没问到」，不是任务失败 —— 界面据此显示「连接中断，重试中」。
        assertTrue(emitted.take(3).all { it is JobQueryOutcome.Unreachable })
    }

    @Test
    fun `退避封顶 30 秒`() {
        assertEquals(2_000L, JobPoller.delayAfter(0))
        assertEquals(2_000L, JobPoller.delayAfter(1))
        assertEquals(16_000L, JobPoller.delayAfter(4))
        assertEquals(30_000L, JobPoller.delayAfter(5))
        assertEquals("失败很多次也不许溢出成负数", 30_000L, JobPoller.delayAfter(1_000))
    }

    @Test
    fun `查到 404 就停 不当成网络问题一直重试`() = runBlocking {
        val fetch = ScriptedFetch<String>(listOf(JobQueryOutcome.NotFound))

        val emitted = poller.poll(fetch::fetch, isFinished = { false }).toList()

        assertEquals(1, fetch.calls)
        assertEquals(listOf<JobQueryOutcome<String>>(JobQueryOutcome.NotFound), emitted)
        assertTrue(waits.isEmpty())
    }

    @Test
    fun `队列全部是终态时 只问一次就停`() = runBlocking {
        val fetch = ScriptedFetch(listOf(JobQueryOutcome.Found(listOf("done", "failed", "cancelled"))))

        poller.poll(fetch::fetch, isFinished = { items -> items.all { it in setOf("done", "failed", "cancelled") } })
            .toList()

        assertEquals(1, fetch.calls)
    }
}
