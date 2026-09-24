package com.icewalnut.grokgen

import com.icewalnut.grokgen.data.UploadFormat
import com.icewalnut.grokgen.data.UploadPlan
import com.icewalnut.grokgen.data.format
import com.icewalnut.grokgen.data.planUpload
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * MIME → 怎么传。
 *
 * ⭐ **这是 M2R2 里少数几块能在开发机上测的逻辑之一。**
 * 这一轮真正的风险（解码、转码、流式上传、Photo Picker）**一条都测不了**，
 * 只能上真机。所以这里能守住的那一件事就格外重要：
 * **1/3 的相册照片是 HEIC，判错就意味着三选一会失败。**
 */
class UploadFormatTest {

    // ---- 直传的三种 ----

    @Test
    fun `png jpeg webp 原字节直传`() {
        assertEquals(UploadPlan.PassThrough(UploadFormat.Png), planUpload("image/png"))
        assertEquals(UploadPlan.PassThrough(UploadFormat.Jpeg), planUpload("image/jpeg"))
        assertEquals(UploadPlan.PassThrough(UploadFormat.Webp), planUpload("image/webp"))
    }

    @Test
    fun `非标准的 image slash jpg 也当 JPEG`() {
        // 个别 provider 真的这么报。
        assertEquals(UploadPlan.PassThrough(UploadFormat.Jpeg), planUpload("image/jpg"))
    }

    // ---- 要转码的 ----

    @Test
    fun `HEIC 与 HEIF 要转码`() {
        // ⭐ 这一条对应的是真实数据：这台手机相册里 1063 张是 HEIC。
        //    判错的后果不是报错，是用户每选三张就有一张传不上去。
        assertEquals(UploadPlan.TranscodeToJpeg, planUpload("image/heic"))
        assertEquals(UploadPlan.TranscodeToJpeg, planUpload("image/heif"))
    }

    @Test
    fun `其他没见过的格式一律转码，不拒绝`() {
        for (mime in listOf("image/avif", "image/gif", "image/bmp", "image/tiff", "image/x-icon")) {
            assertEquals("$mime 应当走转码", UploadPlan.TranscodeToJpeg, planUpload(mime))
        }
    }

    @Test
    fun `MIME 取不到时转码，不是拒绝`() {
        // ⚠️ ContentResolver.getType() 对某些 provider 会返回 null。
        //    取不到类型就拒绝，会把本来能用的图挡在外面。
        assertEquals(UploadPlan.TranscodeToJpeg, planUpload(null))
        assertEquals(UploadPlan.TranscodeToJpeg, planUpload(""))
        assertEquals(UploadPlan.TranscodeToJpeg, planUpload("   "))
    }

    // ---- 形态上的干扰 ----

    @Test
    fun `大小写与参数都不影响判断`() {
        assertEquals(UploadPlan.PassThrough(UploadFormat.Jpeg), planUpload("IMAGE/JPEG"))
        assertEquals(UploadPlan.PassThrough(UploadFormat.Jpeg), planUpload("image/jpeg; charset=binary"))
        assertEquals(UploadPlan.PassThrough(UploadFormat.Png), planUpload("  image/png  "))
    }

    @Test
    fun `不是图片的 MIME 也走转码`() {
        // 走到解码那一步会失败，用户会看到「解不开这张图」——
        // 那比在这里判「不是图片」准确，因为 MIME 本来就可能是错的。
        assertEquals(UploadPlan.TranscodeToJpeg, planUpload("application/octet-stream"))
        assertEquals(UploadPlan.TranscodeToJpeg, planUpload("text/plain"))
    }

    // ---- 不变量 ----

    @Test
    fun `凡是能送出去的后缀都在网关的白名单里`() {
        // ⭐ 这条比上面所有点位加起来更值钱。
        //
        // 点位表挡不住有人往 UploadFormat 里加一个 `.bmp`：那些测试照样全绿，
        // 而真机上会得到一个 415 —— 一个本可以在开发机上挡住的失败。
        //
        // 白名单是网关 2026-09-24 实测报出来的原文：
        //   不支持的图片格式 .heic，允许：['.jpeg', '.jpg', '.png', '.webp']
        val gatewayAllowList = setOf(".jpeg", ".jpg", ".png", ".webp")

        for (format in UploadFormat.entries) {
            assertTrue(
                "UploadFormat.$format 的后缀 ${format.suffix} 不在网关白名单里 —— " +
                    "送上去会得到 415",
                format.suffix in gatewayAllowList,
            )
        }
    }

    @Test
    fun `任何 MIME 都能得到一个确定的格式，不会抛异常`() {
        // planUpload 是在用户选完图之后、发请求之前调的，
        // 这里抛异常等于选完图直接崩。
        val inputs = listOf(
            null, "", "image/png", "image/heic", "nonsense", "/", ";", "image/",
            "image/png;;;", "IMAGE/WEBP", "image/jpeg;q=0.9",
        )
        for (mime in inputs) {
            val plan = planUpload(mime)
            assertTrue(
                "$mime 得到的格式后缀是空的",
                plan.format.suffix.isNotEmpty(),
            )
        }
    }
}
