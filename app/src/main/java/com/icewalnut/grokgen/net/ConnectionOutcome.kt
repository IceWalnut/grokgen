package com.icewalnut.grokgen.net

import com.icewalnut.grokgen.net.dto.HealthDto
import java.io.IOException
import java.net.ConnectException
import java.net.NoRouteToHostException
import java.net.SocketTimeoutException
import java.net.UnknownHostException

/**
 * 连一次网关之后，到底发生了什么。
 *
 * 需求 F1 要求把「连不上」拆成互不相同的几种情况分别提示，
 * 不许统一显示成「网络错误」—— 因为**用户要做的事完全不同**。
 *
 * ⭐ **判据分两层，这是这个文件最容易被写错的地方**：
 * 前三种失败靠**传输层异常**判断，而 [ComfyDown] 靠**响应体**判断 ——
 * `GET /v1/health` 即使 ComfyUI 挂了**也返回 200**。
 * 那是网关故意的设计（`server/app/main.py` 的注释写明了）：
 * 做成 502 会让「网关没起来」和「ComfyUI 没起来」在 App 看来一模一样。
 *
 * ⇒ **「请求成功」不等于「一切正常」。**
 */
sealed interface ConnectionOutcome {

    /** 网关在线，ComfyUI 也在线。 */
    data class Healthy(val health: HealthDto) : ConnectionOutcome

    /** 网关在线，但它报告 ComfyUI 不可达。[reason] 是网关给的原文。 */
    data class ComfyDown(val health: HealthDto, val reason: String?) : ConnectionOutcome

    /** MagicDNS 名解析不了 ⇒ 手机上的 Tailscale 隧道没通。 */
    data object TailscaleDown : ConnectionOutcome

    /**
     * 地址解析得出来，但没有任何应答。
     *
     * ⚠️ **这一档底下其实有三种原因，而 App 分不开它们**（M2R1 实测）：
     * 机器关机了、WSL 发行版被回收了、网关进程没起来 —— 三种都只表现为连接超时。
     *
     * 原因是服务器的 WSL 用镜像网络，**关闭端口上的连接是被丢弃而不是被拒绝**，
     * 所以拿不到 `ConnectException`，只能等到超时。实测三种情况逐个制造过：
     * 网关进程杀掉、`wsl --shutdown`，表现完全一样。
     *
     * ⇒ 文案要把三种可能都说出来，**不要挑一个说** —— 挑错了会把用户指向错误的方向。
     */
    data object NoAnswer : ConnectionOutcome

    /**
     * 连接被明确拒绝 ⇒ 主机在，但端口上没人监听。
     *
     * ⚠️ **在当前这台服务器上观察不到这一档**（见 [NoAnswer]）。
     * 保留它是因为换个网络环境（比如把网关搬到不用镜像网络的机器上）它就会出现，
     * 而那时它给出的信息比 [NoAnswer] 精确。
     * **但它至今没有被真实触发过** —— 按 `Docs/Validation.md` §3 规则 ③，
     * 这不算验证过。
     */
    data object ConnectionRefused : ConnectionOutcome

    /** 网关回了个非 2xx。原样带上状态码与响应体，不要压成一句话。 */
    data class HttpError(val code: Int, val body: String?) : ConnectionOutcome

    /** 上面都不是。**不要把它伪装成别的** —— 没见过的失败要看得见。 */
    data class Unexpected(val throwable: Throwable) : ConnectionOutcome
}

/**
 * 把一个异常翻译成 [ConnectionOutcome]。
 *
 * ⚠️ **这是个纯函数，不碰网络** —— 所以它能在开发机上直接跑单元测试，不需要设备。
 *
 * ⚠️ **下面这套映射是推断，不是实测结果。** VS-31 要在真机上分别制造四种情况来验它。
 * 最可能不准的是 [ConnectionOutcome.ServerUnreachable] 与 [ConnectionOutcome.GatewayDown]
 * 这两条：网关跑在 WSL 里，而 tailnet 节点是 Windows 主机 ——
 * WSL 停掉时到 7869 的连接**是被拒绝还是超时，没有测过**。
 * 实测与这里不符时**改这里，不要改判据**（`Docs/Validation.md` §4.1）。
 */
fun classifyNetworkFailure(throwable: Throwable): ConnectionOutcome = when (throwable) {
    // 解析不出主机名。MagicDNS 的域名只有在 Tailscale 隧道建立时才解析得出来，
    // 所以解析失败 ⇒ 隧道没通。装了没登录、登录了又断开，都落在这一档 ——
    // 对用户来说这几种情况要做的事是同一个，合成一档是对的。
    is UnknownHostException -> ConnectionOutcome.TailscaleDown

    // 连接被明确拒绝：有人在那个地址上回了 RST，说明主机活着，只是端口没人听。
    // ⚠️ **当前这台服务器上走不到这一档**，见 ConnectionRefused 的说明。
    is ConnectException -> ConnectionOutcome.ConnectionRefused

    // 压根没有应答。⚠️ 三种原因都落在这里，App 分不开它们（见 NoAnswer）。
    is SocketTimeoutException, is NoRouteToHostException -> ConnectionOutcome.NoAnswer

    // OkHttp 会把一些底层问题包成 IOException。拆开看一层里面的原因，
    // 拆不出来就如实归到 Unexpected —— **不要猜**。
    is IOException -> throwable.cause?.let { cause ->
        if (cause === throwable) null else classifyNetworkFailure(cause)
    } ?: ConnectionOutcome.Unexpected(throwable)

    else -> ConnectionOutcome.Unexpected(throwable)
}

/**
 * 把一个成功拿到的响应体翻译成 [ConnectionOutcome]。
 *
 * ⚠️ 这一步不能省。HTTP 200 只说明网关活着。
 */
fun classifyHealth(health: HealthDto): ConnectionOutcome =
    if (health.comfy.reachable) {
        ConnectionOutcome.Healthy(health)
    } else {
        ConnectionOutcome.ComfyDown(health, health.comfy.error)
    }
