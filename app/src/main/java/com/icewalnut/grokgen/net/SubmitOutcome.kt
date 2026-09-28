package com.icewalnut.grokgen.net

import com.icewalnut.grokgen.net.dto.JobSubmittedDto
import java.net.SocketTimeoutException

/**
 * 提交一个生成任务之后到底发生了什么。
 *
 * 与 [UploadOutcome] 同一个形式。
 *
 * ⭐ **这里比上传多一件要小心的事：提交是有副作用的。** 上传失败了重传一次，
 * 最多多一张图；提交重复了，就是**多一个任务、多付一次 GPU**（一次 5 秒视频约 75 秒）。
 * 所以「确定没提交」和「不确定提交了没有」必须分成两档，见 [MaybeSubmitted]。
 */
sealed interface SubmitOutcome {

    /** 成功。网关已经登记，正常是 `queued`。 */
    data class Submitted(val job: JobSubmittedDto) : SubmitOutcome

    /**
     * 连接阶段就失败了 ⇒ **请求肯定没有到达网关**，可以放心重新提交。
     *
     * 复用连接页那套分档与文案 —— 「手机没连 Tailscale」在哪一页都是同一件事。
     */
    data class Unreachable(val reason: ConnectionOutcome) : SubmitOutcome

    /**
     * 超时了，**不知道网关收没收到**。
     *
     * ⚠️ 不能说「提交失败」：请求可能已经到了网关、任务已经登记，
     * 只是响应没回来。那样说，用户会再点一次 —— 两个任务、两次 GPU。
     * ⇒ 文案要引导用户**先去确认队列**。App **不自动重试**。
     */
    data object MaybeSubmitted : SubmitOutcome

    /** 400：网关读不出首帧图的尺寸（比如那张图在服务器上被删了）。 */
    data class FirstFrameUnreadable(val detail: String?) : SubmitOutcome

    /**
     * 422：请求形状不对。**这是 App 的 bug，不是用户填错了** ——
     * 表单能提交的东西都已经在 App 端校验过。VS-34 的 golden 测试就是为了不走到这里。
     */
    data class MalformedRequest(val detail: String?) : SubmitOutcome

    /** 503：网关正在关停，不再收新任务。 */
    data class GatewayClosing(val detail: String?) : SubmitOutcome

    /** 其他非 2xx。原样带上状态码与响应体，不要压成一句话。 */
    data class HttpError(val code: Int, val detail: String?) : SubmitOutcome

    /** 没见过的失败。**不要伪装成别的。** */
    data class Unexpected(val throwable: Throwable) : SubmitOutcome
}

/**
 * 把提交接口的非 2xx 状态码翻译成 [SubmitOutcome]。
 *
 * ⚠️ **纯函数**，所以能在开发机上直接测。
 * 状态码的含义来自网关实现（`server/app/api/jobs.py` 的 `submit_job` 与
 * `server/app/models/video_job.py` 的校验器），**还没有对着真实网关逐个触发过**。
 */
fun classifySubmitStatus(code: Int, errorBody: String?): SubmitOutcome {
    val detail = parseFastApiDetail(errorBody)
    return when (code) {
        400 -> SubmitOutcome.FirstFrameUnreadable(detail)
        422 -> SubmitOutcome.MalformedRequest(detail)
        503 -> SubmitOutcome.GatewayClosing(detail)
        else -> SubmitOutcome.HttpError(code, detail)
    }
}

/**
 * 把提交时的异常翻译成 [SubmitOutcome]。
 *
 * ⚠️ **与 [classifyNetworkFailure] 唯一的不同：超时一律归到 [SubmitOutcome.MaybeSubmitted]。**
 *
 * OkHttp 的连接超时和读超时抛的都是 [SocketTimeoutException]，
 * 要分开只能看异常消息里的文字（`"connect timed out"` 对 `"timeout"`）——
 * 那是实现细节，换个 OkHttp 版本就可能变。而这里分错的代价不对称：
 * - 把「没连上」说成「不确定」⇒ 用户多看一眼队列；
 * - 把「已登记但响应丢了」说成「没连上」⇒ **用户重复提交，多付一次 GPU**。
 *
 * ⇒ 取保守的一侧。不引入 OkHttp 的 `EventListener` 去精确区分：
 * 为了一个只影响文案的差别，不值得加那层复杂度。
 *
 * ⚠️ OkHttp 有时把超时包在别的 `IOException` 里，所以沿 `cause` 链找一遍。
 */
fun classifySubmitFailure(throwable: Throwable): SubmitOutcome {
    if (throwable.causeChain().any { it is SocketTimeoutException }) {
        return SubmitOutcome.MaybeSubmitted
    }
    return when (val reason = classifyNetworkFailure(throwable)) {
        is ConnectionOutcome.Unexpected -> SubmitOutcome.Unexpected(reason.throwable)
        else -> SubmitOutcome.Unreachable(reason)
    }
}

/** 自身加上整条 cause 链。防自引用成环。 */
private fun Throwable.causeChain(): Sequence<Throwable> =
    generateSequence(this) { current -> current.cause?.takeIf { it !== current } }
