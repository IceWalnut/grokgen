package com.icewalnut.grokgen

import com.icewalnut.grokgen.model.DurationPreset
import com.icewalnut.grokgen.model.FirstFrameImage
import com.icewalnut.grokgen.model.GenerateForm
import com.icewalnut.grokgen.model.GenerateMode
import com.icewalnut.grokgen.model.toSubmitRequest
import com.icewalnut.grokgen.net.GatewayJson
import com.icewalnut.grokgen.net.dto.JobSubmitRequestDto
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonObject
import org.junit.Assert.assertNotNull
import org.junit.Assert.fail
import org.junit.Test

/**
 * **VS-34**：提交请求体与契约逐字段一致。
 *
 * 对固定的表单输入，用**线上同一个** [GatewayJson] 序列化，与手写的 golden 文件比对。
 *
 * ⚠️ **为什么非要 golden**：网关那边 pydantic 会把没声明的字段**静默丢掉**，
 * 缺省的字段按默认值处理 —— 于是 App 多发一个键、少发一个键、把 `null` 写成别的，
 * 请求照样 200、任务照样成功，只是行为悄悄不对。逐键比对是唯一挡得住的办法。
 *
 * ⚠️ golden 文件是**对着契约 §2 手写的**，不是从代码生成的；
 * **不提供自动覆盖开关**（`Docs/Validation.md` §2 Level 2）——
 * 有了那个开关，「改坏了」和「改好了」就是同一个动作。
 *
 * ⚠️ 比较在 `JsonElement` 层面做：键顺序无关，但**值按原文比**，
 * 所以 `5` 与 `5.0` 算不同 —— 这是故意的，数字的写法变了也要看见。
 */
class SubmitRequestGoldenTest {

    private val uploadedImage = FirstFrameImage(
        assetId = "grokgen/img_20260924_165801_fc9609.png",
        width = 1472,
        height = 832,
        uri = "content://media/picker/0/1",
    )

    @Test
    fun `图生视频 默认参数`() {
        val form = GenerateForm(
            mode = GenerateMode.ImageToVideo,
            description = "一辆红色跑车在夜间湿滑的城市街道上加速驶过",
            soundscape = "引擎低吼，轮胎碾过积水",
            music = "低沉的合成器",
            firstFrame = uploadedImage,
            duration = DurationPreset.S5,
            turbo = true,
            seedInput = "",
        )
        assertMatchesGolden("submit_i2va_default.json", toSubmitRequest(form))
    }

    /**
     * 最可能出错的一条：用户选过图、又切回文生视频。
     * 图留在表单里（切回来还要在），但**请求里不能带它** ——
     * 否则网关收到的是「T2VA + 一张首帧图」，而它不检查模式与图是否一致。
     *
     * 顺带守两件事：prompt 首尾空白被去掉；留空的环境声 / 背景音乐发 `""` 而不是 `N/A`。
     */
    @Test
    fun `文生视频 表单里留着之前选的图 也不发首帧`() {
        val form = GenerateForm(
            mode = GenerateMode.TextToVideo,
            description = "  海浪拍打礁石的慢镜头  ",
            soundscape = "",
            music = "   ",
            firstFrame = uploadedImage,
            duration = DurationPreset.S10,
            turbo = true,
            seedInput = "",
        )
        assertMatchesGolden("submit_t2va_image_left_over.json", toSubmitRequest(form))
    }

    @Test
    fun `图生视频 八秒 关掉Turbo 填了种子`() {
        val form = GenerateForm(
            mode = GenerateMode.ImageToVideo,
            description = "女孩在樱花树下转身微笑",
            soundscape = "微风，远处的鸟鸣",
            music = "轻快的钢琴",
            firstFrame = uploadedImage.copy(assetId = "grokgen/img_20260928_101500_a1b2c3.jpg"),
            duration = DurationPreset.S8,
            turbo = false,
            seedInput = " 849302114 ",
        )
        assertMatchesGolden("submit_i2va_seed_full.json", toSubmitRequest(form))
    }

    // ---- 比对 ----

    private fun assertMatchesGolden(goldenName: String, request: JobSubmitRequestDto) {
        val actual = GatewayJson.parseToJsonElement(
            GatewayJson.encodeToString(JobSubmitRequestDto.serializer(), request),
        ).jsonObject
        val expected = GatewayJson.parseToJsonElement(readGolden(goldenName)).jsonObject

        val problems = diff(expected, actual, path = "")
        if (problems.isNotEmpty()) {
            fail(
                "请求体与 golden 文件 $goldenName 不一致（golden 是对着契约 §2 手写的，" +
                    "不要为了让测试通过去改它）：\n  " + problems.joinToString("\n  ") +
                    "\n实际发出的是：\n$actual",
            )
        }
    }

    /**
     * 先比键的集合（多了、少了都报键名），再逐键比值；嵌套对象递归。
     * 收集全部差异再一次性报，不在第一个差异处停下。
     */
    private fun diff(expected: JsonObject, actual: JsonObject, path: String): List<String> {
        val problems = mutableListOf<String>()

        (expected.keys - actual.keys).sorted().forEach { problems += "缺少键 $path$it" }
        (actual.keys - expected.keys).sorted().forEach { problems += "多出键 $path$it（网关会静默丢掉它）" }

        (expected.keys intersect actual.keys).sorted().forEach { key ->
            val e: JsonElement = expected.getValue(key)
            val a: JsonElement = actual.getValue(key)
            if (e is JsonObject && a is JsonObject) {
                problems += diff(e, a, "$path$key.")
            } else if (e != a) {
                problems += "键 $path$key 的值不同：golden 是 $e，实际是 $a"
            }
        }
        return problems
    }

    /** 从 classpath 读 golden。**读不到就失败，不跳过** —— 跳过等于这条判据不存在。 */
    private fun readGolden(name: String): String {
        val stream = javaClass.classLoader?.getResourceAsStream("golden/$name")
        assertNotNull("classpath 上找不到 golden/$name（应在 app/src/test/resources/golden/ 下）", stream)
        return stream!!.bufferedReader(Charsets.UTF_8).use { it.readText() }
    }
}
