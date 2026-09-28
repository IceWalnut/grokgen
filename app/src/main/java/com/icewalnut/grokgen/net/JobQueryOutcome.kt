package com.icewalnut.grokgen.net

import com.icewalnut.grokgen.net.dto.JobDto
import com.icewalnut.grokgen.net.dto.JobListDto

/**
 * 查一次任务（或列表）之后发生了什么。
 *
 * ⭐ **「连不上网关」和「任务失败」是两件事，这里的分档必须把它们分开**（App 架构文档 §7）：
 * [Unreachable] 只说明这一次没问到，任务本身在服务器上照常跑。
 * 把它显示成任务失败，用户会重新提交 —— 再付一次 GPU。
 */
sealed interface JobQueryOutcome<out T> {

    data class Found<T>(val value: T) : JobQueryOutcome<T>

    /**
     * 404：网关里没有这个任务。
     *
     * ⚠️ **最常见的原因是网关重启过** —— 任务只存在网关内存里（执行文档 §4.7）。
     * 这是「别再问了」，轮询要停，**不要当成网络问题一直重试**。
     */
    data object NotFound : JobQueryOutcome<Nothing>

    /** 这一次没问到。**任务状态未知，不是失败。** 复用连接页的分档。 */
    data class Unreachable(val reason: ConnectionOutcome) : JobQueryOutcome<Nothing>

    /** 其他非 2xx。原样带上状态码与响应体。 */
    data class HttpError(val code: Int, val detail: String?) : JobQueryOutcome<Nothing>
}

/** 单个任务的查询结果。 */
typealias JobResult = JobQueryOutcome<JobDto>

/** 列表的查询结果。 */
typealias JobListResult = JobQueryOutcome<JobListDto>

/**
 * 取消之后发生了什么。
 *
 * ⚠️ 契约里 409 和 404 是完全不同的意思，**混成一档 App 就不知道要不要继续问**。
 */
sealed interface CancelOutcome {

    /**
     * 200：网关接受了取消。
     *
     * ⚠️⚠️ **[job] 的状态可能还不是 `cancelled`。** 运行中的任务是异步中断的，
     * 终态由网关后台落下（契约 §2，2026-09-28 按实现更正）。调用方要**继续轮询**。
     */
    data class Accepted(val job: JobDto) : CancelOutcome

    /**
     * 409：任务已经结束，或正在收尾（`postprocessing` 不可取消）。
     *
     * ⚠️ 这**不是失败** —— 用户想让它停，而它已经停了（或马上就好）。显示「已经结束了」。
     */
    data class AlreadyFinished(val detail: String?) : CancelOutcome

    /** 404：网关里没有这个任务（多半是网关重启过）。 */
    data object NotFound : CancelOutcome

    /** 502：网关连不上 ComfyUI，中断请求没发出去。**任务状态不变，可能还在跑。** */
    data class ComfyUnreachable(val detail: String?) : CancelOutcome

    /** 连不上网关。**取消没发出去**，任务照常在跑。 */
    data class Unreachable(val reason: ConnectionOutcome) : CancelOutcome

    data class HttpError(val code: Int, val detail: String?) : CancelOutcome
}

/**
 * 把查询接口的非 2xx 状态码翻译成 [JobQueryOutcome]。**纯函数**，能在开发机上直接测。
 */
fun classifyJobQueryStatus(code: Int, errorBody: String?): JobQueryOutcome<Nothing> =
    when (code) {
        404 -> JobQueryOutcome.NotFound
        else -> JobQueryOutcome.HttpError(code, parseFastApiDetail(errorBody))
    }

/**
 * 把取消接口的非 2xx 状态码翻译成 [CancelOutcome]。**纯函数**。
 *
 * 含义来自网关实现（`server/app/api/jobs.py` 的 `cancel_job`）。
 */
fun classifyCancelStatus(code: Int, errorBody: String?): CancelOutcome {
    val detail = parseFastApiDetail(errorBody)
    return when (code) {
        409 -> CancelOutcome.AlreadyFinished(detail)
        404 -> CancelOutcome.NotFound
        502 -> CancelOutcome.ComfyUnreachable(detail)
        else -> CancelOutcome.HttpError(code, detail)
    }
}
