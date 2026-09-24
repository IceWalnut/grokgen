package com.icewalnut.grokgen

import com.icewalnut.grokgen.net.ConnectionOutcome
import com.icewalnut.grokgen.net.ServerAddress
import com.icewalnut.grokgen.net.classifyHealth
import com.icewalnut.grokgen.net.classifyNetworkFailure
import com.icewalnut.grokgen.net.dto.ComfyDto
import com.icewalnut.grokgen.net.dto.GatewayDto
import com.icewalnut.grokgen.net.dto.HealthDto
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.IOException
import java.net.ConnectException
import java.net.NoRouteToHostException
import java.net.SocketTimeoutException
import java.net.UnknownHostException

/**
 * 连接结果分类（需求 F1）与地址规整。
 *
 * ⚠️ **这里测的是「异常/响应体 → 结果」这个映射写得对不对，
 * 不是「那个映射本身是不是符合现实」。** 后者要在真机上分别制造四种情况来验，
 * 编号 VS-31 —— 实测与代码里那套推断不符时**改代码，不改判据**。
 */
class ConnectionOutcomeTest {

    // ---- 四种结果 ----

    @Test
    fun `解析不出主机名意味着手机没连上 Tailscale`() {
        assertEquals(
            ConnectionOutcome.TailscaleDown,
            classifyNetworkFailure(UnknownHostException("icewalnut-1060")),
        )
    }

    @Test
    fun `连接被明确拒绝意味着主机在但网关没起来`() {
        // ⚠️ 这一档在当前这台服务器上**观察不到**：WSL 用镜像网络，
        //    关闭端口上的连接是被丢弃而不是被拒绝。M2R1 实测过。
        //    所以这条测的是映射写得对，不是这个分支真的会发生。
        assertEquals(
            ConnectionOutcome.ConnectionRefused,
            classifyNetworkFailure(ConnectException("Connection refused")),
        )
    }

    @Test
    fun `超时与无路由都归到「没有应答」这一档`() {
        // ⚠️ M2R1 实测：网关进程杀掉、wsl --shutdown，两种情况表现完全一样，
        //    都是连接超时。所以这一档底下有三种原因而 App 分不开，文案要把三种都说出来。
        assertEquals(
            ConnectionOutcome.NoAnswer,
            classifyNetworkFailure(SocketTimeoutException("timeout")),
        )
        assertEquals(
            ConnectionOutcome.NoAnswer,
            classifyNetworkFailure(NoRouteToHostException("no route")),
        )
    }

    @Test
    fun `HTTP 200 但 comfy 不可达，要报成 ComfyUI 没起来而不是一切正常`() {
        // ⭐ 这是这一组测试里最重要的一条。
        //    /v1/health 即使 ComfyUI 挂了也返回 200 —— 那是网关故意的设计。
        //    所以「请求成功」不等于「一切正常」，必须再看一眼响应体。
        val health = HealthDto(
            gateway = GatewayDto(status = "ok", version = "b0f7ffb", ffprobe = true),
            comfy = ComfyDto(reachable = false, error = "127.0.0.1:8188/system_stats 不可达"),
        )

        val outcome = classifyHealth(health)

        assertTrue("应当是 ComfyDown，实际是 $outcome", outcome is ConnectionOutcome.ComfyDown)
        assertEquals(
            "127.0.0.1:8188/system_stats 不可达",
            (outcome as ConnectionOutcome.ComfyDown).reason,
        )
    }

    @Test
    fun `comfy 可达时才算一切正常`() {
        val health = HealthDto(
            gateway = GatewayDto(status = "ok", version = "b0f7ffb", ffprobe = true),
            comfy = ComfyDto(
                reachable = true,
                gpu = "cuda:0 NVIDIA GeForce RTX 4080 SUPER : cudaMallocAsync",
                vramTotalBytes = 17170956288,
                vramFreeBytes = 1009926600,
            ),
        )

        assertTrue(classifyHealth(health) is ConnectionOutcome.Healthy)
    }

    @Test
    fun `不可达时 gpu 与显存三个字段是缺失的，不是零`() {
        // ⚠️ 契约上那三个键在不可达时**根本不存在**（不是 null 也不是 0）。
        //    这条钉的是「Kotlin 侧必须先判 reachable 再取」这个前提。
        val comfy = ComfyDto(reachable = false, error = "boom")
        assertNull(comfy.gpu)
        assertNull(comfy.vramTotalBytes)
        assertNull(comfy.vramFreeBytes)
    }

    @Test
    fun `包在 IOException 里的原因要能被拆出来`() {
        val wrapped = IOException("unexpected end of stream", UnknownHostException("host"))
        assertEquals(ConnectionOutcome.TailscaleDown, classifyNetworkFailure(wrapped))
    }

    @Test
    fun `分不出来的异常要如实归到 Unexpected，不许猜一个`() {
        // ⚠️ 没见过的失败要看得见。把它归进上面任何一种，都会让用户照着错的方向去排查。
        val outcome = classifyNetworkFailure(IllegalStateException("something else"))
        assertTrue("应当是 Unexpected，实际是 $outcome", outcome is ConnectionOutcome.Unexpected)
    }

    // ---- 地址规整 ----

    @Test
    fun `地址规整：补 scheme、补端口、补结尾斜杠`() {
        val expected = "http://icewalnut-1060.tail22a711.ts.net:7869/"

        assertEquals(expected, ServerAddress.toBaseUrl("icewalnut-1060.tail22a711.ts.net"))
        assertEquals(expected, ServerAddress.toBaseUrl("icewalnut-1060.tail22a711.ts.net:7869"))
        assertEquals(expected, ServerAddress.toBaseUrl("http://icewalnut-1060.tail22a711.ts.net:7869"))
        assertEquals(expected, ServerAddress.toBaseUrl("http://icewalnut-1060.tail22a711.ts.net:7869/"))
        assertEquals(expected, ServerAddress.toBaseUrl("  icewalnut-1060.tail22a711.ts.net  "))
    }

    @Test
    fun `用户自己写了 https 就按他写的来，不偷偷改成 http`() {
        // 网关没有 TLS。但偷偷把 https 改成 http，会让用户以为自己连的是加密的 ——
        // 让失败可见比悄悄「修好」它更重要。
        assertEquals(
            "https://example.ts.net:7869/",
            ServerAddress.toBaseUrl("https://example.ts.net"),
        )
    }

    @Test
    fun `空白输入返回 null，连都不用连`() {
        assertNull(ServerAddress.toBaseUrl(""))
        assertNull(ServerAddress.toBaseUrl("   "))
    }
}
