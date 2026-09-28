package com.icewalnut.grokgen.net

import retrofit2.Response
import kotlin.coroutines.cancellation.CancellationException

/**
 * 任务的查询、列表与取消。
 *
 * 形式照 [JobSubmitter]：发请求 → 看状态码分类 → 捕获异常分类，
 * 对外只返回分好档的结果，**不把 Retrofit 的 `Response` 漏给 `net/` 之外**（VS-30）。
 */
object JobClient {

    /**
     * 队列页一次拉多少条。
     *
     * 网关的上限是 200；M2 只有一个用户、任务只存内存，20 条在一次网关重启之间够用。
     * 超出时列表响应的 `total` 会大于条数，界面据此提示「只显示最近 N 条」。
     */
    const val LIST_LIMIT = 20

    suspend fun getJob(api: GatewayApi, jobId: String): JobResult =
        query { api.getJob(jobId) }

    suspend fun listJobs(api: GatewayApi, limit: Int = LIST_LIMIT): JobListResult =
        query { api.listJobs(limit) }

    /**
     * 取消。
     *
     * ⚠️ **不重试。** 取消本身是幂等的，但重试会把「连不上」拖成更长的等待，
     * 而用户需要尽快知道取消到底有没有发出去。
     */
    suspend fun cancel(api: GatewayApi, jobId: String): CancelOutcome =
        try {
            val response = api.cancelJob(jobId)
            val job = response.body()
            when {
                response.isSuccessful && job != null -> CancelOutcome.Accepted(job)
                response.isSuccessful -> CancelOutcome.HttpError(response.code(), "响应体是空的")
                else -> classifyCancelStatus(response.code(), response.errorBody()?.string())
            }
        } catch (e: CancellationException) {
            throw e
        } catch (t: Throwable) {
            CancelOutcome.Unreachable(classifyNetworkFailure(t))
        }

    /**
     * ⚠️ **`CancellationException` 必须原样抛出，不能归到「连不上」。**
     * App 退到后台时，收集轮询的协程被取消，正在进行的请求会抛它 ——
     * 把它当成网络失败吞掉，轮询就不会停，VS-36 那条「后台不轮询」会悄悄失效。
     */
    private inline fun <T : Any> query(call: () -> Response<T>): JobQueryOutcome<T> =
        try {
            val response = call()
            val body = response.body()
            when {
                response.isSuccessful && body != null -> JobQueryOutcome.Found(body)
                response.isSuccessful -> JobQueryOutcome.HttpError(response.code(), "响应体是空的")
                else -> classifyJobQueryStatus(response.code(), response.errorBody()?.string())
            }
        } catch (e: CancellationException) {
            throw e
        } catch (t: Throwable) {
            JobQueryOutcome.Unreachable(classifyNetworkFailure(t))
        }
}
