package com.icewalnut.grokgen.ui.component

import com.icewalnut.grokgen.net.ConnectionOutcome

/**
 * 一条结果对用户的解释：标题 + 该做什么。
 *
 * @param title 一行短句。
 * @param detail 处置建议；没有则 `null`。
 */
data class Advice(val title: String, val detail: String?)

/**
 * 连接失败那几档的说法。
 *
 * ⭐ **连接页和上传页共用这一份。**
 * 「手机没连上 Tailscale」在上传时和在连接时是同一件事，说法就应当一样 ——
 * 抄成两份，改一处漏一处，两页会开始说不同的话。
 */
fun adviceFor(outcome: ConnectionOutcome): Advice = when (outcome) {
    is ConnectionOutcome.Healthy -> Advice("网关在线", null)

    is ConnectionOutcome.ComfyDown -> Advice(
        "网关在线，但 ComfyUI 没起来",
        outcome.reason ?: "网关没有给出原因。去服务器上看看 ComfyUI 的进程还在不在。",
    )

    ConnectionOutcome.TailscaleDown -> Advice(
        "手机没连上 Tailscale",
        "解析不出这个地址。打开 Tailscale App 确认已经登录并连上，再试一次。",
    )

    // ⚠️ 三种可能都要说出来，**不能挑一个说**。
    //    M2R1 实测：杀网关进程和 `wsl --shutdown` 在手机这边完全分不开 ——
    //    这台服务器的 WSL 用镜像网络，关闭端口上的连接是被丢弃而不是被拒绝。
    //    第一版挑了一个说，结果把用户指向了错误的方向。
    ConnectionOutcome.NoAnswer -> Advice(
        "服务器没有应答",
        "地址解析得出来，但连不上，有三种可能：机器关机了、" +
            "WSL 发行版被系统回收了、或者网关进程没起来。" +
            "这三种在手机这边分不开 —— 到那台机器上看一眼最快。",
    )

    ConnectionOutcome.ConnectionRefused -> Advice(
        "服务器在线，但网关没启动",
        "端口上没人监听。到服务器上把网关进程起起来。",
    )

    is ConnectionOutcome.HttpError -> Advice(
        "网关回了 HTTP ${outcome.code}",
        outcome.body ?: "没有响应体。",
    )

    is ConnectionOutcome.Unexpected -> Advice(
        "没见过的失败",
        outcome.throwable.toString(),
    )
}
