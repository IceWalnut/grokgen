package com.icewalnut.grokgen

import com.icewalnut.grokgen.model.DurationPreset
import com.icewalnut.grokgen.model.FirstFrameImage
import com.icewalnut.grokgen.model.FormProblem
import com.icewalnut.grokgen.model.GenerateForm
import com.icewalnut.grokgen.model.GenerateMode
import com.icewalnut.grokgen.model.parseSeed
import com.icewalnut.grokgen.model.toSubmitRequest
import com.icewalnut.grokgen.model.validate
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.fail
import org.junit.Test

/**
 * 生成页表单的校验与种子解析。
 *
 * 请求体的**形状**由 `SubmitRequestGoldenTest`（VS-34）守；这里守的是
 * 「什么样的表单根本不该发出去」。
 */
class GenerateFormTest {

    private val image = FirstFrameImage("grokgen/img_x.png", 1472, 832, "content://x")
    private val valid = GenerateForm(
        mode = GenerateMode.ImageToVideo,
        description = "画面",
        firstFrame = image,
    )

    // ---- validate ----

    @Test
    fun `合法表单没有问题`() {
        assertNull(validate(valid))
        assertNull(validate(valid.copy(mode = GenerateMode.TextToVideo, firstFrame = null)))
    }

    @Test
    fun `图生视频没选首帧图不能提交`() {
        // ⚠️ 网关不检查这一条，App 是唯一的闸。
        assertEquals(FormProblem.MissingFirstFrame, validate(valid.copy(firstFrame = null)))
    }

    @Test
    fun `画面描述为空或只有空白不能提交`() {
        assertEquals(FormProblem.MissingDescription, validate(valid.copy(description = "")))
        assertEquals(FormProblem.MissingDescription, validate(valid.copy(description = "  \n ")))
    }

    @Test
    fun `种子填了但不合法不能提交`() {
        listOf("-1", "abc", "1.5", "1e3", "9223372036854775808", "12 34").forEach { input ->
            assertEquals("种子输入 \"$input\"", FormProblem.InvalidSeed, validate(valid.copy(seedInput = input)))
        }
    }

    @Test
    fun `种子留空或只有空白等于随机 可以提交`() {
        assertNull(validate(valid.copy(seedInput = "")))
        assertNull(validate(valid.copy(seedInput = "   ")))
    }

    // ---- parseSeed ----

    @Test
    fun `种子解析的边界`() {
        assertEquals(0L, parseSeed("0"))
        assertEquals(849302114L, parseSeed(" 849302114 "))
        assertEquals(Long.MAX_VALUE, parseSeed("9223372036854775807"))
        assertNull(parseSeed(""))
        assertNull(parseSeed("-1"))
        assertNull(parseSeed("+1"))
        assertNull(parseSeed("9223372036854775808"))
        assertNull(parseSeed("abc"))
    }

    // ---- toSubmitRequest 的前置条件 ----

    @Test
    fun `没过校验的表单拿去转请求体要响亮地失败`() {
        try {
            toSubmitRequest(valid.copy(firstFrame = null))
            fail("图生视频没选图时 toSubmitRequest 应当抛异常，而不是发一个不带图的 I2VA")
        } catch (expected: IllegalArgumentException) {
            // 预期
        }
    }

    // ---- DurationPreset ----

    @Test
    fun `时长三档循环切换`() {
        assertEquals(DurationPreset.S8, DurationPreset.S5.next())
        assertEquals(DurationPreset.S10, DurationPreset.S8.next())
        assertEquals(DurationPreset.S5, DurationPreset.S10.next())
    }
}
