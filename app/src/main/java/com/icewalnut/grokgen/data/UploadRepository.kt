package com.icewalnut.grokgen.data

import android.content.Context
import android.graphics.BitmapFactory
import android.net.Uri
import android.provider.OpenableColumns
import com.icewalnut.grokgen.net.GatewayApi
import com.icewalnut.grokgen.net.ImageUploader
import com.icewalnut.grokgen.net.UploadOutcome
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File
import java.io.FileInputStream
import java.io.FileNotFoundException
import java.io.InputStream

/**
 * 本机读到的那张图的事实。
 *
 * ⭐ [width] / [height] 是**编码尺寸**，和网关用
 * `ffprobe -show_entries stream=width,height` 读到的是同一个口径 ——
 * 所以两者可以直接对照。上传页会把它们并排显示。
 *
 * @param sizeBytes 取不到时是 `-1`。
 */
data class LocalImageFacts(
    val width: Int,
    val height: Int,
    val sizeBytes: Long,
    val mimeType: String?,
    val plan: UploadPlan,
)

/**
 * 相册里的一张图 → 网关的一个 `asset_id`。
 *
 * ⚠️ 这个类 import `android.*` 与 `com.icewalnut.grokgen.net.*`，
 * **但绝不 import `okhttp3`** —— 那是 `SourceConstraintsTest`（VS-30）守的分层，
 * 也是 `net/ImageUploader` 存在的理由。
 */
class UploadRepository(
    private val context: Context,
    private val apiFactory: (String) -> GatewayApi,
) {

    init {
        // 进程被杀在上传中途时，cacheDir 里会留下临时文件，没人清就一直堆着。
        ImageTranscoder.sweepStaleTempFiles(context)
    }

    /**
     * 先读出本机能知道的事实，**不解码全图**。
     *
     * `inJustDecodeBounds` 只读文件头，不分配像素内存 —— 这一步很便宜，
     * 所以可以在用户刚选完图、还没点上传时就做，用来立刻显示尺寸。
     */
    suspend fun readLocalFacts(uri: Uri): LocalImageFacts = withContext(Dispatchers.IO) {
        val mime = context.contentResolver.getType(uri)

        val options = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        runCatching {
            context.contentResolver.openInputStream(uri)?.use { BitmapFactory.decodeStream(it, null, options) }
        }

        LocalImageFacts(
            width = options.outWidth,
            height = options.outHeight,
            sizeBytes = querySize(uri),
            mimeType = mime,
            plan = planUpload(mime),
        )
    }

    /**
     * 上传。
     *
     * **流程**：
     * 1. 按 MIME 决定直传还是转码（[planUpload]）
     * 2. 转码那条路先落一个临时文件
     * 3. **本地预检**大小，超限或为空就不发（见下）
     * 4. 流式发出去
     * 5. `finally` 删临时文件
     *
     * ⚠️ **第 3 步让网关的 400 与 413 从这个 App 变成不可达。**
     * 按 `Docs/Validation.md` §3 规则 ①，不为它们补端到端用例；
     * 哪天去掉了本地预检，它们就重新可达。
     *
     * @param onProgress 已发送 / 总字节。**这里不节流，调用方负责** ——
     *   每次 socket 写都触发一次 Compose 重组会把界面压垮。
     */
    suspend fun upload(
        baseUrl: String,
        uri: Uri,
        onProgress: (sent: Long, total: Long) -> Unit,
    ): UploadOutcome = withContext(Dispatchers.IO) {
        var temp: File? = null
        try {
            val plan = planUpload(context.contentResolver.getType(uri))

            val (openStream, length) = when (plan) {
                is UploadPlan.PassThrough -> {
                    // ⭐ 工厂，不是一个已经打开的流 —— 见 StreamingRequestBody.isOneShot。
                    val factory: () -> InputStream = {
                        context.contentResolver.openInputStream(uri)
                            ?: throw FileNotFoundException("打不开 $uri")
                    }
                    factory to querySize(uri)
                }

                UploadPlan.TranscodeToJpeg -> {
                    val file = ImageTranscoder.transcodeToJpeg(
                        context, uri, ImageUploader.MAX_UPLOAD_BYTES
                    )
                    temp = file
                    ({ FileInputStream(file) } as () -> InputStream) to file.length()
                }
            }

            // 本地预检：不把注定被拒的字节发出去。
            if (length == 0L) return@withContext UploadOutcome.EmptyBody
            if (length > ImageUploader.MAX_UPLOAD_BYTES) {
                return@withContext UploadOutcome.TooLarge(
                    "这张图 ${length / 1024 / 1024} MB，超过服务器 " +
                        "${ImageUploader.MAX_UPLOAD_BYTES / 1024 / 1024} MB 的上限"
                )
            }

            ImageUploader.upload(
                api = apiFactory(baseUrl),
                suffix = plan.format.suffix,
                contentType = plan.format.contentType,
                contentLength = length,
                openStream = openStream,
                onProgress = onProgress,
            )
        } catch (e: ImageTooBigException) {
            UploadOutcome.TooBigToDecode
        } catch (e: TranscodeException) {
            UploadOutcome.TranscodeFailed(e.message ?: "转码失败")
        } catch (e: FileNotFoundException) {
            UploadOutcome.SourceUnreadable
        } catch (e: SecurityException) {
            // 选图给的授权是临时的，App 被重建之后可能就没了。
            UploadOutcome.SourceUnreadable
        } catch (t: Throwable) {
            UploadOutcome.Unexpected(t)
        } finally {
            temp?.delete()
        }
    }

    /** 从 `OpenableColumns.SIZE` 取字节数，取不到返回 `-1`。 */
    private fun querySize(uri: Uri): Long {
        context.contentResolver.query(uri, arrayOf(OpenableColumns.SIZE), null, null, null)
            ?.use { cursor ->
                val index = cursor.getColumnIndex(OpenableColumns.SIZE)
                if (index >= 0 && cursor.moveToFirst() && !cursor.isNull(index)) {
                    return cursor.getLong(index)
                }
            }
        // 退一步：问文件描述符。再取不到就是 -1（分块传输，进度条没有分母）。
        return runCatching {
            context.contentResolver.openAssetFileDescriptor(uri, "r")?.use { it.length }
        }.getOrNull()?.takeIf { it >= 0 } ?: -1L
    }
}
