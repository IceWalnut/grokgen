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

    /**
     * 上传时的写/读超时。
     *
     * 12 MB 过 Tailscale 要多久**本轮会实测**；60 秒是留了余量的猜测值，
     * 测完若差得远就改这里并说明依据。
     */
    private const val UPLOAD_TRANSFER_TIMEOUT_SECONDS = 60L

    /**
     * 上传专用的客户端 —— 超时和上面那个不一样。
     *
     * ⚠️ [create] 那几个值是给 `/v1/health` 调的：响应只有几百字节，
     * 所以 `writeTimeout` 用的是 OkHttp 默认的 **10 秒**。
     * **那对一条 12 MB 的上传是错的** —— tailnet 链路一抖，
     * 一次写卡住 10 秒就超时，而用户看到的会是连接页那套词汇
     * 「服务器没有应答」，**那是个错误的诊断**。
     *
     * ⭐ **连接超时仍然保持 5 秒不变** —— VS-31 那套「连不上」的分档
     * 依赖它快速失败，放宽了就分不清「连不上」和「传得慢」。
     */
    fun createForUpload(baseUrl: String): GatewayApi {
        val okHttp = OkHttpClient.Builder()
            .connectTimeout(CONNECT_TIMEOUT_SECONDS, TimeUnit.SECONDS)
            .writeTimeout(UPLOAD_TRANSFER_TIMEOUT_SECONDS, TimeUnit.SECONDS)
            .readTimeout(UPLOAD_TRANSFER_TIMEOUT_SECONDS, TimeUnit.SECONDS)
            .retryOnConnectionFailure(false)
            // ⚠️ 网关从不重定向。真出现了，我们想看到一个明确的 3xx，
            //    而不是一次莫名其妙的第二遍请求体写入。
            .followRedirects(false)
            .build()

        return buildRetrofit(baseUrl, okHttp)
    }

    fun create(baseUrl: String): GatewayApi {
        val okHttp = OkHttpClient.Builder()
            .connectTimeout(CONNECT_TIMEOUT_SECONDS, TimeUnit.SECONDS)
            .readTimeout(READ_TIMEOUT_SECONDS, TimeUnit.SECONDS)
            // ⚠️ 不开重试。重试会把「连接被拒绝」拖成「超时」，
            //    而那两者正是 VS-31 要分开的两种情况。
            .retryOnConnectionFailure(false)
            .build()

        return buildRetrofit(baseUrl, okHttp)
    }

    private fun buildRetrofit(baseUrl: String, okHttp: OkHttpClient): GatewayApi =
        Retrofit.Builder()
            .baseUrl(baseUrl)
            .client(okHttp)
            .addConverterFactory(json.asConverterFactory("application/json".toMediaType()))
            .build()
            .create(GatewayApi::class.java)
}
