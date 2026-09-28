package com.icewalnut.grokgen.ui.queue

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.systemBarsPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import com.icewalnut.grokgen.model.JobPhase
import com.icewalnut.grokgen.model.formatElapsed
import com.icewalnut.grokgen.model.formatRelative
import com.icewalnut.grokgen.model.jobStatusView
import com.icewalnut.grokgen.model.parseCreatedAt
import com.icewalnut.grokgen.model.summarizeNormalized
import com.icewalnut.grokgen.net.CancelOutcome
import com.icewalnut.grokgen.net.dto.JobDto
import com.icewalnut.grokgen.ui.component.StatusGlyph
import com.icewalnut.grokgen.ui.component.adviceFor
import com.icewalnut.grokgen.ui.component.statusColor
import com.icewalnut.grokgen.ui.theme.GrokgenTheme

/**
 * 任务详情。需求 F2 / F4，视觉稿见 App 架构文档 §9.5.0 的「任务详情 · 加载模型」画板。
 *
 * 显示状态、网关实际使用的参数、notices、失败原因，以及取消。
 * **播放不在这一轮**（M2R5）—— 完成时只放一个占位。
 */
@Composable
fun JobDetailScreen(
    jobId: String,
    state: JobDetailUiState,
    onCancelClicked: () -> Unit,
    onDismissCancelConfirm: () -> Unit,
    onBack: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val spacing = GrokgenTheme.spacing
    val colors = MaterialTheme.colorScheme
    val job = state.job

    Column(
        modifier = modifier
            .fillMaxSize()
            .background(colors.background)
            .systemBarsPadding()
            .verticalScroll(rememberScrollState()),
    ) {
        Row(
            modifier = Modifier.padding(start = spacing.sm, top = spacing.sm, end = spacing.gutter),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Box(
                modifier = Modifier.size(44.dp).clip(RoundedCornerShape(12.dp)).clickable(onClick = onBack),
                contentAlignment = Alignment.Center,
            ) {
                Text("←", style = MaterialTheme.typography.titleLarge, color = colors.onBackground)
            }
            Text("任务详情", style = MaterialTheme.typography.titleMedium, color = colors.onBackground)
        }

        Column(modifier = Modifier.padding(horizontal = spacing.gutter)) {
            state.trouble?.let {
                Spacer(Modifier.height(spacing.sm))
                TroubleBanner(it)
            }

            if (state.gone) {
                Spacer(Modifier.height(spacing.sm))
                Notice(
                    "网关里已经没有这个任务了。最常见的原因是网关重启过 —— 任务只存在网关内存里，" +
                        "重启会清空。如果它当时还在跑，产物可能已经在服务器的输出目录里，但这里查不到了。",
                )
            }

            Spacer(Modifier.height(spacing.sm))
            if (job == null) {
                if (!state.gone && state.trouble == null) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        CircularProgressIndicator(Modifier.size(16.dp), strokeWidth = 2.dp, color = colors.primary)
                        Spacer(Modifier.size(spacing.sm))
                        Text("正在读取 $jobId…", style = MaterialTheme.typography.bodySmall, color = colors.onSurfaceVariant)
                    }
                }
            } else {
                StatusCard(job, state.cancelRequested)
                job.prompt.description.takeIf { it.isNotBlank() }?.let {
                    Spacer(Modifier.height(spacing.lg))
                    Text(it, style = MaterialTheme.typography.bodyMedium, color = colors.onBackground)
                }
                ParametersCard(job)
                job.failureReason?.let { FailureCard(it.kind, it.message, it.detail?.toString()) }
                if (job.state == "done") {
                    Spacer(Modifier.height(spacing.lg))
                    Notice("视频已生成。App 里播放要等下一版（M2R5）；现在可以用网关的 /v1/jobs/${job.jobId}/video 取回。")
                }
            }

            val cancel = state.cancel
            if (cancel is CancelPhase.Finished) {
                Spacer(Modifier.height(spacing.lg))
                CancelResult(cancel.outcome, stillCancelling = state.cancelRequested)
            }

            val view = job?.let { jobStatusView(it.state, it.stage, it.progress) }
            if (view != null && view.canCancel && !state.cancelRequested) {
                Spacer(Modifier.height(spacing.xl))
                CancelButton(
                    phase = cancel,
                    alreadyRunning = view.phase == JobPhase.Generating,
                    onClick = onCancelClicked,
                    onDismiss = onDismissCancelConfirm,
                )
            }
        }
        Spacer(Modifier.height(spacing.xxl))
    }
}

@Composable
private fun StatusCard(job: JobDto, cancelRequested: Boolean) {
    val colors = MaterialTheme.colorScheme
    val spacing = GrokgenTheme.spacing
    val base = jobStatusView(job.state, job.stage, job.progress)
    // 网关已经接受取消、但任务还没到终态：如实说「正在取消」，而不是继续显示「生成中」。
    val view = if (cancelRequested && !base.isTerminal) {
        base.copy(label = "正在取消…", explanation = "已经请服务器中断这个任务，等它停下来。")
    } else {
        base
    }
    val color = statusColor(view.phase)
    val createdAt = parseCreatedAt(job.createdAt)
    val now by rememberTickingNow(active = !view.isTerminal)

    Column(
        modifier = Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(16.dp))
            .background(colors.surface)
            .padding(20.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(9.dp)) {
            StatusGlyph(view.phase, color, MaterialTheme.typography.titleLarge, spinnerSize = 16)
            Text(view.label, style = MaterialTheme.typography.titleLarge, color = color)
        }
        view.explanation?.let {
            Spacer(Modifier.height(spacing.sm))
            Text(it, style = MaterialTheme.typography.bodySmall, color = colors.onSurfaceVariant)
        }
        if (view.phase == JobPhase.Generating || view.phase == JobPhase.Queued) {
            Spacer(Modifier.height(spacing.lg))
            val fraction = view.progressFraction
            if (fraction != null) {
                LinearProgressIndicator(
                    progress = { fraction },
                    color = colors.primary,
                    trackColor = colors.outline,
                    modifier = Modifier.fillMaxWidth().height(3.dp),
                )
            } else if (view.phase == JobPhase.Generating) {
                LinearProgressIndicator(color = colors.primary, trackColor = colors.outline, modifier = Modifier.fillMaxWidth().height(3.dp))
            }
        }
        Spacer(Modifier.height(spacing.md))
        Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
            Text(
                when {
                    createdAt == null -> ""
                    view.isTerminal -> "提交于 ${formatRelative(createdAt, now)}"
                    else -> formatElapsed(createdAt, now)
                },
                style = MaterialTheme.typography.labelSmall,
                color = colors.onSurfaceVariant,
            )
            Text(job.jobId, style = MaterialTheme.typography.labelSmall, color = colors.onSurfaceVariant)
        }
    }
}

@Composable
private fun ParametersCard(job: JobDto) {
    val colors = MaterialTheme.colorScheme
    val spacing = GrokgenTheme.spacing
    Spacer(Modifier.height(spacing.xl))
    Text("网关实际使用的参数", style = MaterialTheme.typography.bodySmall, color = colors.onSurfaceVariant)
    Spacer(Modifier.height(spacing.sm))
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(16.dp))
            .background(colors.surface)
            .padding(horizontal = spacing.lg, vertical = spacing.xs),
    ) {
        // turbo 传 null：从详情进来拿不到用户当时选的档位，**不按步数去猜**。
        val rows = summarizeNormalized(job.normalized, turbo = null)
        rows.forEachIndexed { index, row ->
            Row(
                modifier = Modifier.fillMaxWidth().padding(vertical = spacing.md),
                horizontalArrangement = Arrangement.SpaceBetween,
            ) {
                Text(row.label, style = MaterialTheme.typography.bodySmall, color = colors.onSurfaceVariant)
                Spacer(Modifier.size(spacing.lg))
                Text(row.value, style = MaterialTheme.typography.bodySmall, color = colors.onBackground, textAlign = TextAlign.End)
            }
            if (index != rows.lastIndex) HorizontalDivider(color = colors.surfaceVariant)
        }
    }
    // ⚠️ notices 原文显示，不按前缀解析（契约 §2）。
    job.notices.forEach {
        Spacer(Modifier.height(spacing.sm))
        Notice(it)
    }
}

/**
 * 失败原因。`message` 直接显示；`detail` 是 ComfyUI 的原始信息，**契约要求不能丢**，
 * 默认折叠，点开看原文。
 */
@Composable
private fun FailureCard(kind: String, message: String, detail: String?) {
    val colors = MaterialTheme.colorScheme
    val spacing = GrokgenTheme.spacing
    var expanded by rememberSaveable { mutableStateOf(false) }
    Spacer(Modifier.height(spacing.lg))
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(16.dp))
            .background(colors.surface)
            .padding(spacing.lg),
    ) {
        Text("失败原因", style = MaterialTheme.typography.bodySmall, color = colors.onSurfaceVariant)
        Spacer(Modifier.height(spacing.xs))
        Text(message, style = MaterialTheme.typography.bodyMedium, color = GrokgenTheme.status.failed)
        Text("类别：$kind", style = MaterialTheme.typography.labelSmall, color = colors.onSurfaceVariant)
        if (detail != null && detail != "null") {
            Spacer(Modifier.height(spacing.sm))
            Text(
                if (expanded) "收起原始信息 ▴" else "展开原始信息 ▾",
                style = MaterialTheme.typography.labelMedium,
                color = colors.primary,
                modifier = Modifier.clickable { expanded = !expanded }.padding(vertical = spacing.xs),
            )
            if (expanded) {
                Text(detail, style = MaterialTheme.typography.labelSmall, color = colors.onBackground)
            }
        }
    }
}

@Composable
private fun CancelButton(phase: CancelPhase, alreadyRunning: Boolean, onClick: () -> Unit, onDismiss: () -> Unit) {
    val colors = MaterialTheme.colorScheme
    val spacing = GrokgenTheme.spacing
    val failed = GrokgenTheme.status.failed
    val confirming = phase == CancelPhase.Confirming
    val sending = phase == CancelPhase.Sending

    Box(
        modifier = Modifier
            .fillMaxWidth()
            .height(52.dp)
            .clip(RoundedCornerShape(12.dp))
            .background(if (confirming) colors.surfaceVariant else colors.background)
            .border(1.dp, if (confirming) failed else colors.outline, RoundedCornerShape(12.dp))
            .clickable(enabled = !sending, onClick = onClick),
        contentAlignment = Alignment.Center,
    ) {
        if (sending) {
            CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp, color = failed)
        } else {
            Text(
                if (confirming) "再点一次确认取消" else "取消任务",
                style = MaterialTheme.typography.titleMedium,
                color = failed,
            )
        }
    }
    if (confirming) {
        Spacer(Modifier.height(spacing.sm))
        Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
            // 排队中的任务还没占过 GPU，说「时间不会退回」不对（M2R4 真机上看到的）。
            Text(
                if (alreadyRunning) "已经跑掉的时间不会退回。" else "它还没开始跑，取消不浪费什么。",
                style = MaterialTheme.typography.labelSmall,
                color = colors.onSurfaceVariant,
            )
            Text(
                "不取消了",
                style = MaterialTheme.typography.labelMedium,
                color = colors.primary,
                modifier = Modifier.clickable(onClick = onDismiss),
            )
        }
    }
}

/**
 * 取消的结果。每一档都说清楚发生了什么、任务现在怎样。
 *
 * ⚠️ 409 **不是失败**：用户想让它停，它已经停了（或正在收尾马上就好）。
 */
@Composable
private fun CancelResult(outcome: CancelOutcome, stillCancelling: Boolean) {
    val text = when (outcome) {
        is CancelOutcome.Accepted ->
            if (stillCancelling) "服务器已经接受取消，正在等它停下来。" else "已取消。"
        is CancelOutcome.AlreadyFinished -> "已经结束了，不需要取消。"
        CancelOutcome.NotFound -> "网关里已经没有这个任务了（多半是网关重启过）。"
        is CancelOutcome.ComfyUnreachable ->
            "取消没有发出去：网关连不上 ComfyUI。任务可能还在跑。${outcome.detail.orEmpty()}"
        is CancelOutcome.Unreachable ->
            "取消没有发出去：${adviceFor(outcome.reason).title}。任务照常在跑，可以稍后再试。"
        is CancelOutcome.HttpError -> "取消失败：网关回了 HTTP ${outcome.code}。${outcome.detail.orEmpty()}"
    }
    Notice(text)
}

@Composable
private fun Notice(text: String) {
    val colors = MaterialTheme.colorScheme
    val spacing = GrokgenTheme.spacing
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(12.dp))
            .background(colors.surfaceVariant)
            .padding(spacing.md),
        horizontalArrangement = Arrangement.spacedBy(spacing.sm),
    ) {
        Text("ⓘ", style = MaterialTheme.typography.bodySmall, color = colors.onSurfaceVariant)
        Text(text, style = MaterialTheme.typography.bodySmall, color = colors.onBackground)
    }
}
