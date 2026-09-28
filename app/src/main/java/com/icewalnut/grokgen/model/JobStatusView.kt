package com.icewalnut.grokgen.model

/**
 * 任务状态在 App 上的呈现分组（App 架构文档 §6）。
 *
 * 契约有 8 个 `state`，不原样显示给用户。
 */
enum class JobPhase {
    Queued,
    Generating,
    Finishing,
    Done,
    Failed,
    Cancelled,

    /**
     * 认不出的 `state`。
     *
     * ⚠️ 网关将来加一个新状态时，App 不应该崩、也不应该猜。
     * 原样显示那个字符串，并且**不当成终态** —— 宁可多问几次，也不要把一个还在跑的任务当成结束了。
     */
    Unknown,
}

/**
 * 一个任务在界面上要显示的状态。
 *
 * @param label 状态行的主文字，例如「生成中 · 采样 42%」。
 * @param explanation 详情页状态卡下面那句解释；没有则 `null`。
 * @param progressFraction 进度条的值 0..1；`null` 表示不确定进度（显示滚动条或不显示）。
 * @param canCancel 这个状态下能不能点取消。
 */
data class JobStatusView(
    val phase: JobPhase,
    val label: String,
    val explanation: String?,
    val progressFraction: Float?,
    val canCancel: Boolean,
) {
    val isTerminal: Boolean get() = phase == JobPhase.Done || phase == JobPhase.Failed || phase == JobPhase.Cancelled
}

/**
 * 契约的 `state` 是不是终态。
 *
 * ⚠️ **白名单**：只有这三个是终态。认不出的状态一律不是 —— 理由见 [JobPhase.Unknown]。
 * 这和网关那边 `JobRecord.finished` 用白名单是同一个道理（ContextPack §4.01：
 * ComfyUI 的 `completed` 字段曾被当成「结束了」，结果被中断的任务永远等不到终态）。
 */
fun isTerminalState(state: String): Boolean = state in TERMINAL_STATES

private val TERMINAL_STATES = setOf("done", "failed", "cancelled")

/**
 * 把契约的 `state` / `stage` / `progress` 变成界面上的状态。
 *
 * ⚠️ **格式化规则只写在这一处**（App 架构文档 §3.2）。
 *
 * ⚠️ **`stage` 与 `progress` 可以为 `null`**（刚开始、网关与 ComfyUI 的实时连接中断、
 * 或网关认不出当前节点时），这时显示不带百分比的「生成中」—— **为空不是异常**。
 *
 * @param progress 0..1。越界时夹到范围内，不信任网关一定给出合法值。
 */
fun jobStatusView(state: String, stage: String?, progress: Double?): JobStatusView = when (state) {
    "queued", "preparing" -> JobStatusView(
        phase = JobPhase.Queued,
        label = "排队中",
        explanation = "同一时间只运行一个任务，前面的跑完才轮到它。",
        progressFraction = null,
        canCancel = true,
    )

    "submitted", "running" -> generatingView(stage, progress)

    // ⚠️ 收尾中不可取消：契约规定这时取消返回 409。按钮直接不给，免得用户点了看到「已经结束了」一头雾水。
    "postprocessing" -> JobStatusView(
        phase = JobPhase.Finishing,
        label = "收尾中",
        explanation = "视频已经生成，正在整理产物，马上就好。",
        progressFraction = null,
        canCancel = false,
    )

    "done" -> JobStatusView(JobPhase.Done, "完成", null, null, canCancel = false)
    "failed" -> JobStatusView(JobPhase.Failed, "失败", null, null, canCancel = false)
    "cancelled" -> JobStatusView(JobPhase.Cancelled, "已取消", null, null, canCancel = false)

    else -> JobStatusView(
        phase = JobPhase.Unknown,
        label = "未知状态：$state",
        explanation = "网关报了一个 App 不认识的状态。App 会继续查询，但不会猜它的意思。",
        progressFraction = null,
        canCancel = false,
    )
}

private fun generatingView(stage: String?, progress: Double?): JobStatusView {
    // ⚠️ loading_model 必须单独显示（需求 F2）：它的耗时与生成多少内容无关，
    //    和「采样中」混成一个「生成中」，用户会以为卡住了。
    val (label, explanation) = when (stage) {
        "loading_model" -> "正在加载模型" to "这一段的耗时与要生成多少内容无关，通常要等十几到几十秒，之后才开始采样。"
        "sampling" -> {
            val percent = progress?.let { (it.coerceIn(0.0, 1.0) * 100).toInt() }
            (if (percent != null) "采样中 $percent%" else "采样中") to null
        }
        "decoding_video", "decoding_audio" -> "解码中" to null
        "encoding" -> "编码中" to null
        // stage 为空或认不出：只说「生成中」，不编造细分阶段。
        else -> "生成中" to "服务器正在生成。暂时拿不到细分阶段与百分比（刚开始，或网关与 ComfyUI 的实时连接中断了），任务本身不受影响。"
    }
    val fraction = if (stage == "sampling") progress?.coerceIn(0.0, 1.0)?.toFloat() else null
    return JobStatusView(
        phase = JobPhase.Generating,
        label = label,
        explanation = explanation,
        progressFraction = fraction,
        canCancel = true,
    )
}
