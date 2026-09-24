package com.icewalnut.grokgen.net

/**
 * 后端网关的地址。
 *
 * ⭐ **这是整个 App 里唯一允许出现服务器地址与端口字面量的文件。**
 * 这条约束由 `SourceConstraintsTest` 强制，编号 VS-30。
 *
 * ⚠️ **为什么这条对 App 比对网关更重要**：网关写死了地址，改一行重新部署即可；
 * 而 **App 写死了地址，已经装在手机上的那个版本就永远连的是旧地址**。
 *
 * ⚠️ **默认值用 MagicDNS 域名，不用 IP** —— Tailscale 的 IP 在设备重装后会变，
 * 域名不会（`Docs/runbooks/home_gpu_server.md` §2）。
 */
object ServerAddress {

    /** 默认的后端主机名。用户可以在连接页改，改完存进 DataStore。 */
    const val DEFAULT_HOST: String = "icewalnut-1060.tail22a711.ts.net"

    /** 网关监听的端口。 */
    const val DEFAULT_PORT: Int = 7869

    /** 用户第一次打开 App 时输入框里预填的值。 */
    val DEFAULT_INPUT: String = "$DEFAULT_HOST:$DEFAULT_PORT"

    /**
     * 把用户输入的东西规整成一个 Retrofit 能用的 base URL。
     *
     * 用户可能输入的形态很多：带不带 `http://`、带不带端口、带不带结尾斜杠。
     * 这里都补齐，**而不是要求用户输入得刚好对**。
     *
     * ⚠️ 只补 `http://` 不补 `https://` —— 网关没有 TLS，信任边界是整个 tailnet
     * （接口契约 §1）。用户真输了 `https://` 就按他写的来，让失败可见，
     * 而不是偷偷改成 http 然后让他以为自己连的是加密的。
     *
     * @return 结尾一定带 `/` 的 base URL；输入是空白时返回 null。
     */
    fun toBaseUrl(rawInput: String): String? {
        val trimmed = rawInput.trim()
        if (trimmed.isEmpty()) return null

        val withScheme = when {
            trimmed.startsWith("http://") || trimmed.startsWith("https://") -> trimmed
            else -> "http://$trimmed"
        }

        // 去掉结尾斜杠再统一补一个，避免出现 "//"
        val withoutTrailingSlash = withScheme.trimEnd('/')

        // 判断有没有端口：要看 scheme 后面那一段里有没有冒号。
        // 直接在整串上找冒号会命中 "http:" 里那个。
        val schemeEnd = withoutTrailingSlash.indexOf("://") + 3
        val afterScheme = withoutTrailingSlash.substring(schemeEnd)
        val hostPart = afterScheme.substringBefore('/')
        val hasPort = hostPart.contains(':')

        return if (hasPort) {
            "$withoutTrailingSlash/"
        } else {
            val path = afterScheme.substringAfter('/', missingDelimiterValue = "")
            val rebuilt = withoutTrailingSlash.substring(0, schemeEnd) + hostPart + ":" + DEFAULT_PORT
            if (path.isEmpty()) "$rebuilt/" else "$rebuilt/$path/"
        }
    }
}
