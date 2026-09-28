package com.icewalnut.grokgen.data

import com.icewalnut.grokgen.net.JobQueryOutcome
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow

/**
 * 轮询（App 架构文档 §7）。
 *
 * **只在「前台 且 有任务未到终态」时轮询**，这两个条件分别这样落地：
 * - **「有任务未到终态」**：[poll] 在 [isFinished] 为真、或查到 404 时**自己结束**；
 * - **「前台」**：[poll] 返回的是**冷 Flow**，只在被收集时才跑。界面用
 *   `collectAsStateWithLifecycle`（经 `stateIn(WhileSubscribed(0))`）收集，
 *   App 退到后台时收集被取消，这个循环随之停止 —— **不需要另外写一套「暂停」逻辑**。
 *
 * ⭐ VS-36 的判据是**数请求次数**，不是「看起来停了」（执行文档 M2R4）。
 * 所以等待函数 [wait] 可以注入：测试里换成立刻返回并记下时长的版本，
 * 就能在毫秒内跑完「第 N 次到终态」「中途取消」「连续失败」这些场景并精确计数。
 *
 * @param wait 等待给定毫秒数。默认是协程的 `delay`。
 */
class JobPoller(
    private val wait: suspend (Long) -> Unit = { delay(it) },
) {

    /**
     * 反复调用 [fetch]，每次的结果都发出去。
     *
     * 流程：
     * 1. 调一次 [fetch]，结果**先发出去**（界面马上能看到）；
     * 2. `Found` 且 [isFinished] ⇒ 结束；`NotFound` ⇒ 结束（别再问了）；
     * 3. 否则等一段时间再来：成功后等 [INTERVAL_MILLIS]；连续失败时退避，见 [delayAfter]。
     *
     * ⚠️ **失败不结束轮询。** 网络抖动时任务在服务器上照常跑，
     * 这里要做的是「连接中断，重试中」，而不是放弃 —— 放弃等于让用户以为任务丢了。
     *
     * @param fetch 查一次。**必须把 `CancellationException` 原样抛出**（`JobClient` 已保证），
     *   否则退到后台时这个循环停不下来。
     * @param isFinished 拿到的值是否已经不需要再问（详情：任务到终态；队列：全部到终态）。
     */
    fun <T> poll(
        fetch: suspend () -> JobQueryOutcome<T>,
        isFinished: (T) -> Boolean,
    ): Flow<JobQueryOutcome<T>> = flow {
        // 连续失败的次数。成功一次就清零 —— 退避只针对「一直连不上」，不累积历史。
        var consecutiveFailures = 0
        while (true) {
            val outcome = fetch()
            emit(outcome)
            when (outcome) {
                is JobQueryOutcome.Found -> {
                    if (isFinished(outcome.value)) return@flow
                    consecutiveFailures = 0
                }
                JobQueryOutcome.NotFound -> return@flow
                is JobQueryOutcome.Unreachable, is JobQueryOutcome.HttpError -> consecutiveFailures++
            }
            wait(delayAfter(consecutiveFailures))
        }
    }

    companion object {
        /**
         * 正常间隔 2 秒。
         *
         * 依据（App 架构文档 §1.2）：采样只有 8 步，约每 9 秒一个进度变化，2 秒已经远密于此；
         * 只有一个用户，服务端压力为零。
         */
        const val INTERVAL_MILLIS = 2_000L

        /** 退避的封顶值。连不上的时候也至少每 30 秒试一次，恢复后不用等太久。 */
        const val MAX_BACKOFF_MILLIS = 30_000L

        /**
         * 下一次查询前等多久。
         *
         * 连续失败 0 次 → 2 秒；1 次 → 2 秒；2 次 → 4 秒；3 次 → 8 秒；… 封顶 30 秒。
         * 即第一次失败后仍按正常间隔重试一次（多半只是一次抖动），之后才开始加倍。
         */
        fun delayAfter(consecutiveFailures: Int): Long {
            if (consecutiveFailures <= 1) return INTERVAL_MILLIS
            // 限制移位次数，防止失败很多次后 shl 溢出成负数。
            val shift = (consecutiveFailures - 1).coerceAtMost(10)
            return (INTERVAL_MILLIS shl shift).coerceAtMost(MAX_BACKOFF_MILLIS)
        }
    }
}
