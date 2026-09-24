package com.icewalnut.grokgen.net

import okhttp3.MediaType.Companion.toMediaTypeOrNull
import okhttp3.MultipartBody
import java.io.InputStream

/**
 * 上传一张图。
 *
 * ⭐ **这个对象存在的理由是分层**：`SourceConstraintsTest`（VS-30）禁止
 * `net/` 以外的任何文件 import `okhttp3` / `retrofit2`。
 * 所以 `data/UploadRepository` 不能自己拼 `MultipartBody.Part` ——
 * 那件事必须发生在这里，而这里对外只暴露 `String` / `Long` / `() -> InputStream`。
 */
object ImageUploader {

    /**
     * 网关的上限，32 MiB（`server/app/api/uploads.py` 的 `MAX_UPLOAD_BYTES`）。
     *
     * ⚠️ 网关是**读完整个 body 之后**才检查大小的。
     * 所以超限的文件要在**本地**就拦下来，不要白付一次十几 MB 的传输。
     */
    const val MAX_UPLOAD_BYTES: Long = 32L * 1024 * 1024

    /**
     * 发一次上传请求。
     *
     * @param api 已经配好 base URL 的接口。**要用 [GatewayClient.createForUpload]
     *   造出来的那个**，它的写超时才够长。
     * @param suffix 送给网关的后缀，形如 `.jpg`。**只有它决定网关收不收。**
     * @param contentType 声明的 MIME。网关不看，抓包时好认。
     * @param contentLength 字节数；`-1` 表示未知（分块传输）。
     * @param openStream 打开流的工厂 —— 见 [StreamingRequestBody.isOneShot]。
     * @param onProgress 已发送 / 总字节。**调用方负责节流。**
     * @return 成功、明确的拒绝、或传输层失败。
     */
    suspend fun upload(
        api: GatewayApi,
        suffix: String,
        contentType: String,
        contentLength: Long,
        openStream: () -> InputStream,
        onProgress: (sent: Long, total: Long) -> Unit,
    ): UploadOutcome {
        val body = StreamingRequestBody(
            contentType = contentType.toMediaTypeOrNull(),
            declaredLength = contentLength,
            openStream = openStream,
            onProgress = onProgress,
        )

        // ⚠️ 字段名必须是 `file`（网关那边 FastAPI 从参数名推的）。
        // ⚠️ 文件名用一个固定的 ASCII 名字，**不把用户的原文件名带上去** ——
        //    中文文件名在 Content-Disposition 里怎么编码，OkHttp 与 python-multipart
        //    未必一致，而那会表现为「后缀没读对 → 415」，排查方向完全错。
        //    反正网关只取后缀，主干名它自己会重新生成。
        val part = MultipartBody.Part.createFormData("file", "image$suffix", body)

        return try {
            val response = api.uploadImage(part)
            val uploaded = response.body()
            when {
                response.isSuccessful && uploaded != null -> UploadOutcome.Uploaded(uploaded)
                response.isSuccessful -> UploadOutcome.HttpError(response.code(), "响应体是空的")
                else -> classifyUploadStatus(response.code(), response.errorBody()?.string())
            }
        } catch (t: Throwable) {
            // 传输层失败复用连接页那套分档 —— 「手机没连 Tailscale」
            // 在上传时和在连接时是同一件事，说法应当一样。
            UploadOutcome.Unreachable(classifyNetworkFailure(t))
        }
    }
}
