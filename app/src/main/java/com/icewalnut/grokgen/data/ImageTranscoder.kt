package com.icewalnut.grokgen.data

import android.content.Context
import android.graphics.Bitmap
import android.graphics.ImageDecoder
import android.net.Uri
import android.os.Build
import androidx.annotation.RequiresApi
import java.io.File

/** 转码失败。 */
class TranscodeException(message: String, cause: Throwable? = null) : Exception(message, cause)

/** 图太大，手机上解不开。 */
class ImageTooBigException(cause: Throwable) : Exception("解码时内存不够", cause)

/**
 * 把一张系统相册里的图解码后重编成 JPEG。
 *
 * ⭐ **像素尺寸原样保留：不裁、不缩。**
 * 网关按首帧图的宽高比推算视频画布（契约 §2），改了尺寸就等于改了用户
 * 最终拿到的视频画幅 —— 而那**不会以任何形式报错**。
 */
object ImageTranscoder {

    /** 起始质量。首帧图的画质直接进视频，不值得为几 MB 省。 */
    private val QUALITY_LADDER = intArrayOf(95, 85, 75)

    private const val TEMP_PREFIX = "grokgen_upload_"
    private const val TEMP_SUFFIX = ".jpg"

    /**
     * 解码 [uri] 并重编成 JPEG，落到 `cacheDir` 的一个临时文件。
     *
     * **流程**：
     * 1. `ImageDecoder` 解码（软件位图、sRGB、拒绝半张图）
     * 2. 按 [QUALITY_LADDER] 逐级压，直到不超过 [maxBytes]
     * 3. `recycle()` 还内存
     *
     * ⚠️ **为什么落文件而不是留在内存里**：
     * - `writeTo()` 可能被调第二遍（见 `StreamingRequestBody.isOneShot`），文件能重开
     * - `file.length()` 顺带给了 `contentLength`，没有它就只能分块传，进度条也没有分母
     * - `ByteArrayOutputStream` 会和那个几十 MB 的 bitmap **同时驻留**，
     *   而且它按翻倍扩容，瞬时峰值还要再乘二
     *
     * @param context 取 `cacheDir` 与 `contentResolver`。
     * @param uri 系统相册给的 `content://`。
     * @param maxBytes 编出来不能超过这个大小。
     * @return 临时文件。**调用方负责删**。
     *
     * @throws TranscodeException 系统版本太老、解不开、或降到最低质量仍然超限。
     * @throws ImageTooBigException 解码时内存不够。
     */
    fun transcodeToJpeg(context: Context, uri: Uri, maxBytes: Long): File {
        // ⚠️ minSdk 是 26，而 ImageDecoder 要 28。
        //    **不为 26/27 写 BitmapFactory 的回退分支** —— HEIF 解码本身在 28 以下
        //    根本不存在，那条分支只可能失败，写了就是一段永远走不通的死代码
        //    （`Docs/Validation.md` §3 规则 ①）。
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.P) {
            throw TranscodeException("这台设备的系统版本太老（低于 Android 9），解不开这种格式的图")
        }

        val bitmap = decode(context, uri)
        return try {
            compressWithinBudget(context, bitmap, maxBytes)
        } finally {
            // ⭐ 立刻还内存，不等 GC —— 接下来还要读文件、发网络请求。
            bitmap.recycle()
        }
    }

    /** 清掉以前留下的临时文件（进程被杀在上传中途时会剩）。 */
    fun sweepStaleTempFiles(context: Context) {
        runCatching {
            context.cacheDir.listFiles { f -> f.name.startsWith(TEMP_PREFIX) }
                ?.forEach { it.delete() }
        }
    }

    // ⚠️ 这里用 @RequiresApi 而不是 @SuppressLint("NewApi")。
    //    两者对编译器的效果一样，但意思完全不同：
    //    SuppressLint 是「我知道有问题，别烦我」，
    //    RequiresApi 是「调这个函数之前必须先判版本」—— 后者会让 lint
    //    **继续检查调用方**，真有人忘了判版本时仍然会报。
    //    唯一的调用方 transcodeToJpeg 在第一行就判了。
    @RequiresApi(Build.VERSION_CODES.P)
    private fun decode(context: Context, uri: Uri): Bitmap {
        val source = ImageDecoder.createSource(context.contentResolver, uri)
        return try {
            ImageDecoder.decodeBitmap(source) { decoder, _, _ ->
                // 硬件位图在 compress() 上的行为跨厂商不一致，必须要软件位图。
                decoder.allocator = ImageDecoder.ALLOCATOR_SOFTWARE
                decoder.isMutableRequired = false
                // ⚠️ 半张图就算失败。不设这个的话，一个截断的 HEIC 会解出半张图，
                //    然后被我们高高兴兴传上去 —— 而那不会报错。
                decoder.setOnPartialImageListener { false }
            }
        } catch (e: OutOfMemoryError) {
            // ⚠️ **不许在这里悄悄缩图。** 缩了画布比例就跟着变，而那不报错。
            //    宁可明确失败。
            throw ImageTooBigException(e)
        } catch (t: Throwable) {
            throw TranscodeException("解不开这张图：${t.javaClass.simpleName}", t)
        }
    }

    private fun compressWithinBudget(context: Context, bitmap: Bitmap, maxBytes: Long): File {
        val target = File.createTempFile(TEMP_PREFIX, TEMP_SUFFIX, context.cacheDir)

        // ⚠️ HEIC 的压缩率大约是 JPEG 的两倍，所以一张 12 MB 的 HEIC 重编之后
        //    可能变成约 20 MB —— **这条降质量的路是真的会走到的**，不是摆设。
        //    ⭐ 降质量可以（只影响画质），**缩尺寸不行**（会改画布比例且不报错）。
        for (quality in QUALITY_LADDER) {
            target.outputStream().buffered().use { out ->
                bitmap.compress(Bitmap.CompressFormat.JPEG, quality, out)
            }
            if (target.length() <= maxBytes) return target
        }

        val actual = target.length()
        target.delete()
        throw TranscodeException(
            "这张图转成 JPEG 之后是 ${actual / 1024 / 1024} MB，" +
                "降到最低质量仍然超过 ${maxBytes / 1024 / 1024} MB 的上限"
        )
    }
}
