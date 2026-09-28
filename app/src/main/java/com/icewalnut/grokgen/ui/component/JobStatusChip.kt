package com.icewalnut.grokgen.ui.component

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.size
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.unit.dp
import com.icewalnut.grokgen.model.JobPhase
import com.icewalnut.grokgen.model.JobStatusView
import com.icewalnut.grokgen.ui.theme.GrokgenTheme

/**
 * 任务状态的呈现：**颜色、文字、图标三者一起给**（App 架构文档 §9.5.3，需求 §7.1.3）。
 *
 * ⚠️ 「排队中」与「已取消」在配色里是同一个灰（需求 §7.1.2）—— **光看颜色本来就分不开**，
 * 所以文字与图标不是锦上添花，是功能正确性。各页面只用这个组件，不自己拼。
 */
@Composable
fun JobStatusChip(view: JobStatusView, modifier: Modifier = Modifier) {
    val color = statusColor(view.phase)
    Row(
        modifier = modifier,
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        StatusGlyph(view.phase, color, MaterialTheme.typography.labelMedium)
        Text(view.label, style = MaterialTheme.typography.labelMedium, color = color)
    }
}

/**
 * 状态图标。生成中用一个转圈（它确实「正在发生」），其余用字形。
 *
 * ⚠️ 字形用 ✓ ✕ ! 这类普通符号，不用 emoji（需求 §7.1：不用 emoji 充当图标）。
 */
@Composable
fun StatusGlyph(phase: JobPhase, color: Color, style: TextStyle, spinnerSize: Int = 12) {
    when (phase) {
        JobPhase.Generating, JobPhase.Finishing -> CircularProgressIndicator(
            modifier = Modifier.size(spinnerSize.dp),
            strokeWidth = 1.5.dp,
            color = color,
        )
        else -> Text(glyphFor(phase), style = style, color = color)
    }
}

private fun glyphFor(phase: JobPhase): String = when (phase) {
    JobPhase.Queued -> "○"
    JobPhase.Done -> "✓"
    JobPhase.Failed -> "!"
    JobPhase.Cancelled -> "✕"
    JobPhase.Unknown -> "?"
    JobPhase.Generating, JobPhase.Finishing -> ""
}

/** 状态 → 颜色。取自主题的语义 token，**这里不写颜色字面量**（VS-41）。 */
@Composable
fun statusColor(phase: JobPhase): Color {
    val status = GrokgenTheme.status
    return when (phase) {
        JobPhase.Queued, JobPhase.Unknown -> status.queued
        JobPhase.Generating, JobPhase.Finishing -> status.running
        JobPhase.Done -> status.done
        JobPhase.Failed -> status.failed
        JobPhase.Cancelled -> status.cancelled
    }
}
