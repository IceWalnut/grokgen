package com.icewalnut.grokgen.ui.queue

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.systemBarsPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.icewalnut.grokgen.model.JobPhase
import com.icewalnut.grokgen.model.formatElapsed
import com.icewalnut.grokgen.model.formatRelative
import com.icewalnut.grokgen.model.formatSeconds
import com.icewalnut.grokgen.model.isTerminalState
import com.icewalnut.grokgen.model.jobStatusView
import com.icewalnut.grokgen.model.parseCreatedAt
import com.icewalnut.grokgen.net.JobQueryOutcome
import com.icewalnut.grokgen.net.dto.JobDto
import com.icewalnut.grokgen.ui.component.JobStatusChip
import com.icewalnut.grokgen.ui.component.StatusGlyph
import com.icewalnut.grokgen.ui.component.adviceFor
import com.icewalnut.grokgen.ui.component.statusColor
import com.icewalnut.grokgen.ui.theme.GrokgenTheme
import kotlinx.coroutines.delay
import java.time.Instant

/**
 * 队列页。需求 F4，视觉稿见 App 架构文档 §9.5.0 的「队列」画板。
 *
 * M2 做到：任务列表、当前状态、进入详情。**重跑、复制参数、日志摘要不做**（App 架构文档 §4）。
 * 左侧是状态图标块而不是缩略图 —— 缩略图要等 M3 的媒体库接口。
 */
@Composable
fun QueueScreen(
    state: QueueUiState,
    onOpenJob: (String) -> Unit,
    onNewJob: () -> Unit,
    onRefresh: () -> Unit,
    onBack: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val spacing = GrokgenTheme.spacing
    val colors = MaterialTheme.colorScheme
    val jobs = state.jobs
    val now by rememberTickingNow(active = jobs?.any { !isTerminalState(it.state) } == true)

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
            HeaderButton("←", onBack)
            Text(
                "队列",
                style = MaterialTheme.typography.headlineSmall,
                color = colors.onBackground,
                modifier = Modifier.weight(1f),
            )
            HeaderButton("刷新", onRefresh)
            Spacer(Modifier.size(spacing.sm))
            HeaderButton("新建", onNewJob)
        }

        state.trouble?.let {
            Spacer(Modifier.height(spacing.sm))
            TroubleBanner(it, Modifier.padding(horizontal = spacing.gutter))
        }

        Spacer(Modifier.height(spacing.md))
        Column(
            modifier = Modifier.padding(horizontal = spacing.gutter),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            when {
                jobs == null && state.trouble == null -> LoadingRow()
                jobs == null -> Unit // 还没成功拿到过；横条已经说了原因
                jobs.isEmpty() -> EmptyHint()
                else -> jobs.forEach { job -> JobCard(job, now, onClick = { onOpenJob(job.jobId) }) }
            }

            if (jobs != null && state.total > jobs.size) {
                Text(
                    "只显示最近 ${jobs.size} 条，共 ${state.total} 条",
                    style = MaterialTheme.typography.labelSmall,
                    color = colors.onSurfaceVariant,
                )
            }
        }
        Spacer(Modifier.height(spacing.xxl))
    }
}

@Composable
private fun JobCard(job: JobDto, now: Instant, onClick: () -> Unit) {
    val colors = MaterialTheme.colorScheme
    val spacing = GrokgenTheme.spacing
    val view = jobStatusView(job.state, job.stage, job.progress)
    val createdAt = parseCreatedAt(job.createdAt)

    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(16.dp))
            .background(colors.surface)
            .clickable(onClick = onClick)
            .padding(spacing.md),
        horizontalArrangement = Arrangement.spacedBy(spacing.md),
    ) {
        Box(
            modifier = Modifier
                .size(72.dp)
                .clip(RoundedCornerShape(12.dp))
                .background(colors.surfaceVariant),
            contentAlignment = Alignment.Center,
        ) {
            StatusGlyph(view.phase, statusColor(view.phase), MaterialTheme.typography.titleLarge, spinnerSize = 22)
        }

        Column(
            modifier = Modifier.weight(1f).heightIn(min = 72.dp),
            verticalArrangement = Arrangement.spacedBy(spacing.sm, Alignment.CenterVertically),
        ) {
            Text(
                // prompt 是用户原文，理论上不会空（提交时校验过）；兜底显示模式，不显示空行。
                job.prompt.description.ifBlank { job.mode },
                style = MaterialTheme.typography.bodyMedium,
                color = colors.onBackground,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(spacing.sm)) {
                JobStatusChip(view.copy(label = cardLabel(view.label, view.phase, job)))
                if (createdAt != null) {
                    Text(
                        if (view.isTerminal) formatRelative(createdAt, now) else formatElapsed(createdAt, now),
                        style = MaterialTheme.typography.labelSmall,
                        color = colors.onSurfaceVariant,
                    )
                }
            }
            if (view.phase == JobPhase.Generating) {
                val fraction = view.progressFraction
                if (fraction != null) {
                    LinearProgressIndicator(
                        progress = { fraction },
                        color = colors.primary,
                        trackColor = colors.outline,
                        modifier = Modifier.fillMaxWidth().height(3.dp),
                    )
                } else {
                    // 没有百分比（不在采样、或 stage 为空）：不确定进度条，表示「在动，但不知道到哪了」。
                    LinearProgressIndicator(
                        color = colors.primary,
                        trackColor = colors.outline,
                        modifier = Modifier.fillMaxWidth().height(3.dp),
                    )
                }
            }
            job.failureReason?.let {
                Text(
                    it.message,
                    style = MaterialTheme.typography.labelSmall,
                    color = colors.onSurfaceVariant,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
            }
        }
    }
}

/** 完成的卡片带上实际时长，和视觉稿一致：「完成 · 5.17 秒」。 */
private fun cardLabel(label: String, phase: JobPhase, job: JobDto): String =
    if (phase == JobPhase.Done) "$label · ${formatSeconds(job.normalized.actualDurationSeconds)} 秒" else label

@Composable
private fun HeaderButton(text: String, onClick: () -> Unit) {
    val colors = MaterialTheme.colorScheme
    val spacing = GrokgenTheme.spacing
    Box(
        modifier = Modifier
            .heightIn(min = 44.dp)
            .clip(RoundedCornerShape(12.dp))
            .background(if (text == "←") colors.background else colors.surface)
            .clickable(onClick = onClick)
            .padding(horizontal = spacing.md),
        contentAlignment = Alignment.Center,
    ) {
        Text(
            text,
            style = if (text == "←") MaterialTheme.typography.titleLarge else MaterialTheme.typography.bodyMedium,
            color = colors.onBackground,
        )
    }
}

/**
 * 「连接中断，重试中」。
 *
 * ⚠️ **这不是任务失败**（App 架构文档 §7）：任务在服务器上照常跑，只是这一次没问到。
 * 所以用「排队中」的灰，不用失败的红；列表保留上一次拿到的内容。
 */
@Composable
fun TroubleBanner(trouble: JobQueryOutcome<Nothing>, modifier: Modifier = Modifier) {
    val colors = MaterialTheme.colorScheme
    val spacing = GrokgenTheme.spacing
    val (title, detail) = when (trouble) {
        is JobQueryOutcome.Unreachable -> "连接中断，重试中" to
            "任务在服务器上照常跑，不受影响。${adviceFor(trouble.reason).title}。"
        is JobQueryOutcome.HttpError -> "网关回了 HTTP ${trouble.code}，重试中" to trouble.detail
        JobQueryOutcome.NotFound -> "网关里没有这个任务" to null
        is JobQueryOutcome.Found -> "" to null // 不会走到：trouble 只存非成功的结果
    }
    Row(
        modifier = modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(12.dp))
            .background(colors.surfaceVariant)
            .padding(spacing.md),
        horizontalArrangement = Arrangement.spacedBy(spacing.sm),
    ) {
        CircularProgressIndicator(
            modifier = Modifier.size(14.dp).padding(top = 2.dp),
            strokeWidth = 1.5.dp,
            color = GrokgenTheme.status.queued,
        )
        Column {
            Text(title, style = MaterialTheme.typography.bodySmall, color = colors.onBackground)
            if (detail != null) {
                Text(detail, style = MaterialTheme.typography.labelSmall, color = colors.onSurfaceVariant)
            }
        }
    }
}

@Composable
private fun LoadingRow() {
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(GrokgenTheme.spacing.sm)) {
        CircularProgressIndicator(modifier = Modifier.size(16.dp), strokeWidth = 2.dp, color = MaterialTheme.colorScheme.primary)
        Text("正在读取队列…", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

@Composable
private fun EmptyHint() {
    Text(
        "还没有任务。\n⚠️ 网关重启会清空任务列表（任务只存在网关内存里），之前的任务可能因此不见了。",
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
}

/**
 * 「现在」，在 [active] 时每秒更新一次 —— 让「已用 N 秒」走起来。
 *
 * ⚠️ 这只是本地的时钟刷新，**不发请求**；请求的节奏由轮询器决定。
 * 没有未结束的任务时不刷新，免得页面空转。
 */
@Composable
fun rememberTickingNow(active: Boolean): androidx.compose.runtime.State<Instant> {
    val now = remember { mutableStateOf(Instant.now()) }
    LaunchedEffect(active) {
        now.value = Instant.now()
        while (active) {
            delay(1_000)
            now.value = Instant.now()
        }
    }
    return now
}
