package com.icewalnut.grokgen

import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * 两条扫源码的约束：**VS-30**（分层）与 **VS-41**（颜色字面量）。
 *
 * 形式照抄网关那边的 `server/tests/test_layering.py` —— 那条判据在 M1 证明过价值。
 * 它有三个要害性质，缺一条这个测试就会变成摆设：
 *
 * 1. **源码根从注入的绝对路径拿，不靠当前工作目录猜**（见 [srcRoot]）
 * 2. **先收集全部违例再一次性断言**，每条是 `文件:行号 说明` —— 一次运行报出所有问题
 * 3. **守卫的守卫**：单独一个测试确认豁免名单指向的文件真的存在（见最后一个测试）
 *
 * ⚠️ 判据写成「扫源码」而不是「测试能跑通」是有理由的：后者是**消极判据** ——
 * 有人在 `ui/` 里直接发了个请求而测试恰好没走到那条路，它照样是绿的。
 */
class SourceConstraintsTest {

    // ---- 被扫的范围与豁免 ----

    /** `net/` 包下的文件可以说 HTTP。用路径片段判断，不是前缀匹配整个包。 */
    private val netPackage = "com/icewalnut/grokgen/net/"

    /** `ui/theme/` 包下的文件可以写颜色字面量。 */
    private val themePackage = "com/icewalnut/grokgen/ui/theme/"

    /** 唯一允许出现服务器地址与端口的文件。 */
    private val serverAddressFile = "com/icewalnut/grokgen/net/ServerAddress.kt"

    private val forbiddenImportRoots = listOf(
        "okhttp3",
        "retrofit2",
        "java.net.HttpURLConnection",
        "java.net.URL",
    )

    /** 地址的特征串。用端口和域名片段，不用完整 URL 的正则 —— 便宜，而且不容易被绕开。 */
    private val addressMarkers = listOf("tail22a711", "ts.net", ":7869", "100.64.")

    // ---- 测试 ----

    @Test
    fun `除 net 包外不许 import HTTP 库`() {
        val violations = mutableListOf<String>()

        forEachKotlinFile { file, relativePath ->
            if (relativePath.startsWith(netPackage)) return@forEachKotlinFile

            file.readLines().forEachIndexed { index, line ->
                val trimmed = line.trim()
                if (!trimmed.startsWith("import ")) return@forEachIndexed
                val imported = trimmed.removePrefix("import ").substringBefore(" as ").trim()
                val hit = forbiddenImportRoots.firstOrNull {
                    imported == it || imported.startsWith("$it.")
                }
                if (hit != null) {
                    violations += "$relativePath:${index + 1} import $imported"
                }
            }
        }

        assertTrue(
            "这些文件绕过了 net/ 那层直接说 HTTP（App 架构文档 §3.1）：\n  " +
                violations.joinToString("\n  "),
            violations.isEmpty(),
        )
    }

    @Test
    fun `除 ServerAddress 外不许出现服务器地址字面量`() {
        val violations = mutableListOf<String>()

        forEachKotlinFile { file, relativePath ->
            if (relativePath == serverAddressFile) return@forEachKotlinFile

            eachStringLiteral(file.readText()) { lineNumber, literal ->
                val hit = addressMarkers.firstOrNull { literal.contains(it) }
                if (hit != null) {
                    violations += "$relativePath:$lineNumber 字面量里有 \"$hit\""
                }
            }
        }

        assertTrue(
            "服务器地址被硬编码在下面这些地方。它只能来自 net/ServerAddress.kt ——\n" +
                "已经装在手机上的那个版本改不了地址，而 Tailscale IP 在设备重装后会变：\n  " +
                violations.joinToString("\n  "),
            violations.isEmpty(),
        )
    }

    @Test
    fun `除 ui theme 包外不许出现颜色字面量`() {
        val violations = mutableListOf<String>()
        val colorCall = Regex("""\bColor\s*\(\s*0x[0-9A-Fa-f]{6,8}""")
        val hexInLiteral = Regex("""#[0-9A-Fa-f]{6}(?:[0-9A-Fa-f]{2})?\b""")

        forEachKotlinFile { file, relativePath ->
            if (relativePath.startsWith(themePackage)) return@forEachKotlinFile

            val text = file.readText()

            // Color(0x…) 是代码不是字符串，所以要在**去掉注释后的代码**上找。
            eachCodeLine(text) { lineNumber, code ->
                if (colorCall.containsMatchIn(code)) {
                    violations += "$relativePath:$lineNumber 有 Color(0x…) 字面量"
                }
            }

            // "#RRGGBB" 这种是写在字符串里的，所以在字面量里找。
            eachStringLiteral(text) { lineNumber, literal ->
                if (hexInLiteral.containsMatchIn(literal)) {
                    violations += "$relativePath:$lineNumber 字面量里有 #RRGGBB 形式的颜色"
                }
            }
        }

        assertTrue(
            "颜色字面量只能出现在 ui/theme/ 下（VS-41）。散进各个页面之后，\n" +
                "换配色就变成一次全仓库搜索替换，而那种替换会顺手改到不该改的地方且不报错：\n  " +
                violations.joinToString("\n  "),
            violations.isEmpty(),
        )
    }

    /**
     * 守卫的守卫。
     *
     * ⚠️ **没有这条，上面三条会静默变松。** 豁免名单里的文件一旦被改名或移走，
     * 豁免就指向一个不存在的路径 —— 而豁免一个不存在的文件**不会报错**。
     * 同理，源码根要是解析错了，上面三条会扫到零个文件然后全部通过。
     */
    @Test
    fun `豁免名单与源码根本身是有效的`() {
        assertTrue(
            "源码根不存在：$srcRoot —— 上面三条约束会扫到零个文件然后静默通过",
            srcRoot.isDirectory,
        )

        val kotlinFileCount = srcRoot.walkTopDown().count { it.isFile && it.extension == "kt" }
        assertTrue(
            "源码根下一个 .kt 都没扫到（$srcRoot）—— 约束已经失去意义",
            kotlinFileCount > 0,
        )

        assertTrue(
            "豁免名单里的 $serverAddressFile 不存在 —— 地址约束已经失去意义",
            File(srcRoot, serverAddressFile).isFile,
        )

        assertTrue(
            "豁免的包 $netPackage 不存在 —— 分层约束已经失去意义",
            File(srcRoot, netPackage).isDirectory,
        )

        assertTrue(
            "豁免的包 $themePackage 不存在 —— 颜色约束已经失去意义",
            File(srcRoot, themePackage).isDirectory,
        )
    }

    // ---- 工具 ----

    /**
     * 源码根。
     *
     * ⚠️ **不靠当前工作目录猜。** Gradle 的 Test 任务默认工作目录是工程目录，
     * 但那是个约定不是保证；而一旦猜错，扫描会得到零个文件然后**静默通过** ——
     * 那正是这几条约束最不能出的错。
     * 所以路径由 `build.gradle.kts` 用 `systemProperty` 注入，读不到就直接失败。
     */
    private val srcRoot: File by lazy {
        val path = System.getProperty("grokgen.srcRoot")
            ?: error(
                "系统属性 grokgen.srcRoot 没有被设置。" +
                    "它应该由 build.gradle.kts 的 tasks.withType<Test> 注入。",
            )
        File(path)
    }

    private fun forEachKotlinFile(action: (file: File, relativePath: String) -> Unit) {
        srcRoot.walkTopDown()
            .filter { it.isFile && it.extension == "kt" }
            .sortedBy { it.path }
            .forEach { file ->
                action(file, file.relativeTo(srcRoot).invariantSeparatorsPath)
            }
    }

    /**
     * 遍历源码里的每一个**字符串字面量**。
     *
     * ⚠️ **为什么不能简单地「把 `//` 之后删掉当注释」**：
     * `"http://icewalnut-1060..."` 会被从 `//` 处截断，
     * **于是硬编码的地址反而检测不到了**。那是假阴性，比误报危险得多。
     * 所以这里带引号状态扫 —— 只有在引号**外面**的 `//` 才算注释。
     *
     * 已知的局限，**明说不装**：
     * - 原始字符串 `"""…"""` 只做最朴素的处理（按普通引号对待）
     * - 字符串模板里 `${'$'}{…}` 的表达式内容也会被当成字面量内容扫到
     *
     * 这两条造成的都是**假阳性**（误报）—— 方向是安全的：
     * 误报会被人看见并处理，漏报不会。
     */
    private fun eachStringLiteral(text: String, action: (lineNumber: Int, literal: String) -> Unit) {
        var line = 1
        var i = 0
        var inBlockComment = false

        while (i < text.length) {
            val c = text[i]

            if (c == '\n') {
                line++
                i++
                continue
            }

            if (inBlockComment) {
                if (c == '*' && i + 1 < text.length && text[i + 1] == '/') {
                    inBlockComment = false
                    i += 2
                } else {
                    i++
                }
                continue
            }

            if (c == '/' && i + 1 < text.length) {
                when (text[i + 1]) {
                    '/' -> {
                        while (i < text.length && text[i] != '\n') i++
                        continue
                    }
                    '*' -> {
                        inBlockComment = true
                        i += 2
                        continue
                    }
                }
            }

            if (c == '"') {
                val startLine = line
                val sb = StringBuilder()
                i++
                while (i < text.length) {
                    val ch = text[i]
                    if (ch == '\\' && i + 1 < text.length) {
                        sb.append(text[i + 1])
                        i += 2
                        continue
                    }
                    if (ch == '"') {
                        i++
                        break
                    }
                    if (ch == '\n') line++
                    sb.append(ch)
                    i++
                }
                action(startLine, sb.toString())
                continue
            }

            i++
        }
    }

    /**
     * 遍历每一行**去掉注释与字符串之后的代码**。
     *
     * 用来找 `Color(0x…)` 这种写在代码里、不在字符串里的东西。
     * 把字符串内容挖掉是为了避免注释和文档里提到的示例被误判。
     */
    private fun eachCodeLine(text: String, action: (lineNumber: Int, code: String) -> Unit) {
        var inBlockComment = false

        text.lines().forEachIndexed { index, raw ->
            val out = StringBuilder()
            var i = 0
            var inString = false

            while (i < raw.length) {
                val c = raw[i]

                if (inBlockComment) {
                    if (c == '*' && i + 1 < raw.length && raw[i + 1] == '/') {
                        inBlockComment = false
                        i += 2
                    } else {
                        i++
                    }
                    continue
                }

                if (inString) {
                    if (c == '\\' && i + 1 < raw.length) {
                        i += 2
                    } else {
                        if (c == '"') inString = false
                        i++
                    }
                    continue
                }

                if (c == '/' && i + 1 < raw.length) {
                    if (raw[i + 1] == '/') break
                    if (raw[i + 1] == '*') {
                        inBlockComment = true
                        i += 2
                        continue
                    }
                }

                if (c == '"') {
                    inString = true
                    i++
                    continue
                }

                out.append(c)
                i++
            }

            action(index + 1, out.toString())
        }
    }
}
