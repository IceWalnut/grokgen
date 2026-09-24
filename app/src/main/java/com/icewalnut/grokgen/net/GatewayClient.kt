package com.icewalnut.grokgen.net

import kotlinx.serialization.json.Json
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import retrofit2.Retrofit
import retrofit2.converter.kotlinx.serialization.asConverterFactory
import java.util.concurrent.TimeUnit

/**
 * 装配 [GatewayApi]。
 *
 * ⚠️ 这个文件和 [ServerAddress] 一样属于 `net/` 包，
 * 是**唯二**允许 import HTTP 库的地方（VS-30）。
 */
object GatewayClient {

    /**
     * ⚠️ `ignoreUnknownKeys = true`：网关将来往响应里加字段，不该让 App 崩。
     *
     * ⚠️ 但**不要**顺手加 `coerceInputValues` 之类的宽容开关 ——
     * 那会把「字段类型不对」这种真错误也吞掉，而契约对不上正是要看见的东西。
     */
    private val json = Json {
        ignoreUnknownKeys = true
    }

    /**
     * 连接超时故意设得短。
     *
     * 理由：连接页的整个价值就是**快速**告诉用户连不上、以及为什么。
     * 等 30 秒才说「连不上」，用户早就自己去点别的了。
     *
     * ⚠️ 读超时可以长一点，但 M2R1 只有 `/v1/health` 这一个很小的响应，
     * 而网关那边查 ComfyUI 的超时是 2 秒，所以整体不会慢。
     */
    private const val CONNECT_TIMEOUT_SECONDS = 5L
    private const val READ_TIMEOUT_SECONDS = 15L

    fun create(baseUrl: String): GatewayApi {
        val okHttp = OkHttpClient.Builder()
            .connectTimeout(CONNECT_TIMEOUT_SECONDS, TimeUnit.SECONDS)
            .readTimeout(READ_TIMEOUT_SECONDS, TimeUnit.SECONDS)
            // ⚠️ 不开重试。重试会把「连接被拒绝」拖成「超时」，
            //    而那两者正是 VS-31 要分开的两种情况。
            .retryOnConnectionFailure(false)
            .build()

        return Retrofit.Builder()
            .baseUrl(baseUrl)
            .client(okHttp)
            .addConverterFactory(json.asConverterFactory("application/json".toMediaType()))
            .build()
            .create(GatewayApi::class.java)
    }
}
