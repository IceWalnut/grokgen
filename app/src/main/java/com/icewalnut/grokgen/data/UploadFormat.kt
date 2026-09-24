package com.icewalnut.grokgen.data

/**
 * 我们**可以发给网关**的三种图片格式。
 *
 * ⚠️ 网关只看 multipart 里那个 filename 的**后缀**，**完全不嗅探内容**
 * （2026-09-24 实测：一个内容是文本的 `.png` 照样通过后缀这一关）。
 * 所以这个枚举定的是「我们送什么后缀」，不是「文件真的是什么」。
 *
 * 网关的白名单是 `.png .jpg .jpeg .webp`，其余返回 415。
 */
enum class UploadFormat(val suffix: String, val contentType: String) {
    Png(".png", "image/png"),
    Jpeg(".jpg", "image/jpeg"),
    Webp(".webp", "image/webp"),
}

/** 这张图该怎么传。 */
sealed interface UploadPlan {

    /**
     * 原字节直传，一个字节都不动。
     *
     * ⭐ 不重编码就**没有画质损失，也不会改变像素尺寸** ——
     * 而尺寸是网关推算视频画布的依据，改了就等于改了用户最终拿到的画幅。
     */
    data class PassThrough(val format: UploadFormat) : UploadPlan

    /**
     * 解码后重新编成 JPEG。
     *
     * ⚠️ **像素尺寸保持不变：不裁、不缩。** 理由同上。
     */
    data object TranscodeToJpeg : UploadPlan
}

/** 这个方案最终会送出去的格式。 */
val UploadPlan.format: UploadFormat
    get() = when (this) {
        is UploadPlan.PassThrough -> format
        UploadPlan.TranscodeToJpeg -> UploadFormat.Jpeg
    }

/**
 * 按 MIME 类型决定怎么传。
 *
 * ⚠️ **认不出来的一律转码，不要拒绝。**
 * 这台手机相册里约三分之一是 HEIC（实测 1063 / 2697），直接传上去必然 415；
 * 而 `ContentResolver.getType()` 对某些 provider 会返回 `null` ——
 * **取不到类型就拒绝，会把本来能用的图挡在外面**。转码这条路对任何解得开的图都成立。
 *
 * ⚠️ MIME 可能带参数（`image/jpeg;charset=binary`）、大小写不定、或者是 `null`，
 * 三种都要落到确定的分支上。
 *
 * @param rawMimeType `ContentResolver.getType(uri)` 的原始返回值，可为 null。
 * @return 直传（带确定的格式）或转码。
 */
fun planUpload(rawMimeType: String?): UploadPlan {
    val mime = rawMimeType
        ?.substringBefore(';')
        ?.trim()
        ?.lowercase()
        ?: return UploadPlan.TranscodeToJpeg

    return when (mime) {
        "image/png" -> UploadPlan.PassThrough(UploadFormat.Png)
        "image/jpeg" -> UploadPlan.PassThrough(UploadFormat.Jpeg)
        "image/webp" -> UploadPlan.PassThrough(UploadFormat.Webp)
        // ⚠️ `image/jpg` 不是标准写法，但个别 provider 真的这么报。
        //    归到 Jpeg 是安全的：万一内容其实不是 JPEG，网关读尺寸那一步会失败
        //    并返回 500，**不会静默产出一张错的图**（2026-09-24 那个 0x0 的洞已经补上）。
        "image/jpg" -> UploadPlan.PassThrough(UploadFormat.Jpeg)
        // heic / heif / avif / gif / bmp / tiff / 以及所有没见过的
        else -> UploadPlan.TranscodeToJpeg
    }
}
