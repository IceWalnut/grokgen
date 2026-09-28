package com.icewalnut.grokgen.net

import com.icewalnut.grokgen.net.dto.JobSubmitRequestDto

/**
 * 提交一个生成任务。
 *
 * 形式照 [ImageUploader]：发请求 → 看状态码分类 → 捕获异常分类，
 * 对外只返回 [SubmitOutcome]，不把 Retrofit 的 `Response` 漏给上层（VS-30）。
 *
 * ⚠️ **不重试。** 重试会把一次提交变成两个任务。
 * [GatewayClient.create] 已经关了 `retryOnConnectionFailure`，这里也不在外面套循环。
 */
object JobSubmitter {

    /**
     * @param api 已经配好 base URL 的接口，用 [GatewayClient.create] 造的那个即可 ——
     *   网关的提交接口只做一次 ffprobe 就返回，不等生成开始。
     */
    suspend fun submit(api: GatewayApi, request: JobSubmitRequestDto): SubmitOutcome =
        try {
            val response = api.submitJob(request)
            val submitted = response.body()
            when {
                response.isSuccessful && submitted != null -> SubmitOutcome.Submitted(submitted)
                // 2xx 却没有响应体：任务**可能**已经登记了，不能说成失败。
                response.isSuccessful -> SubmitOutcome.HttpError(response.code(), "响应体是空的")
                else -> classifySubmitStatus(response.code(), response.errorBody()?.string())
            }
        } catch (t: Throwable) {
            classifySubmitFailure(t)
        }
}
