package com.icewalnut.grokgen.ui.connect

import androidx.compose.foundation.background
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
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextField
import androidx.compose.material3.TextFieldDefaults
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import com.icewalnut.grokgen.net.ConnectionOutcome
import com.icewalnut.grokgen.net.dto.HealthDto
import com.icewalnut.grokgen.ui.component.adviceFor
import com.icewalnut.grokgen.ui.theme.GrokgenTheme

/**
 * 连接页。需求 F1。
 *
 * 视觉按需求 §7.1：深色 + 内容优先 —— 输入控件低对比度退到背景里，
 * 强调色只出现在主操作与「在线」状态上。
 */
@Composable
fun ConnectScreen(
    state: ConnectUiState,
    onInputChange: (String) -> Unit,
    onConnect: () -> Unit,
    onOpenUpload: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val spacing = GrokgenTheme.spacing
    val colors = MaterialTheme.colorScheme

    Column(
        modifier = modifier
            .fillMaxSize()
            .background(colors.background)
            .systemBarsPadding()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = spacing.gutter),
    ) {
        Spacer(Modifier.height(spacing.xxl))

        Text(
            text = "连接后端",
            style = MaterialTheme.typography.headlineSmall,
            color = colors.onBackground,
        )

        Spacer(Modifier.height(spacing.sm))

        Text(
            text = "手机需要先连上 Tailscale，才访问得到家里那台机器。",
            style = MaterialTheme.typography.bodySmall,
            color = colors.onSurfaceVariant,
        )

        Spacer(Modifier.height(spacing.xl))

        Text(
            text = "网关地址",
            style = MaterialTheme.typography.bodySmall,
            color = colors.onSurfaceVariant,
        )
        Spacer(Modifier.height(spacing.xs))

        // 下划线式输入：只留一条底线，没有填充背景和边框（需求 §7.1.1）。
        TextField(
            value = state.input,
            onValueChange = onInputChange,
            singleLine = true,
            textStyle = MaterialTheme.typography.bodyLarge,
            keyboardOptions = KeyboardOptions(
                capitalization = KeyboardCapitalization.None,
                autoCorrectEnabled = false,
                keyboardType = KeyboardType.Uri,
                imeAction = ImeAction.Go,
            ),
            colors = TextFieldDefaults.colors(
                focusedContainerColor = Color.Transparent,
                unfocusedContainerColor = Color.Transparent,
                disabledContainerColor = Color.Transparent,
                focusedTextColor = colors.onBackground,
                unfocusedTextColor = colors.onBackground,
                cursorColor = colors.primary,
                focusedIndicatorColor = colors.primary,
                unfocusedIndicatorColor = colors.outline,
            ),
            modifier = Modifier.fillMaxWidth(),
        )

        if (state.inputError != null) {
            Spacer(Modifier.height(spacing.sm))
            Text(
                text = state.inputError,
                style = MaterialTheme.typography.bodySmall,
                color = GrokgenTheme.status.failed,
            )
        }

        Spacer(Modifier.height(spacing.xl))

        Button(
            onClick = onConnect,
            enabled = !state.checking,
            shape = RoundedCornerShape(12.dp),
            colors = ButtonDefaults.buttonColors(
                containerColor = colors.primary,
                contentColor = colors.onPrimary,
            ),
            modifier = Modifier
                .fillMaxWidth()
                .height(52.dp),
        ) {
            if (state.checking) {
                CircularProgressIndicator(
                    modifier = Modifier.size(20.dp),
                    strokeWidth = 2.dp,
                    color = colors.onPrimary,
                )
            } else {
                Text(text = "连接", style = MaterialTheme.typography.titleMedium)
            }
        }

        if (state.outcome != null) {
            Spacer(Modifier.height(spacing.xl))
            OutcomeCard(state.outcome)
        }

        // ⚠️ 只有网关**和 ComfyUI 都在线**时才让进上传页。
        //    上传是经 ComfyUI 落盘的 —— ComfyUI 挂着时传必然 502，
        //    **提前拦住比让用户白传十几 MB 强**。
        if (state.outcome is ConnectionOutcome.Healthy) {
            Spacer(Modifier.height(spacing.lg))
            Button(
                onClick = onOpenUpload,
                shape = RoundedCornerShape(12.dp),
                colors = ButtonDefaults.buttonColors(
                    containerColor = colors.surface,
                    contentColor = colors.onBackground,
                ),
                modifier = Modifier.fillMaxWidth().height(52.dp),
            ) {
                Text("选一张图上传 →", style = MaterialTheme.typography.titleMedium)
            }
        } else if (state.outcome is ConnectionOutcome.ComfyDown) {
            Spacer(Modifier.height(spacing.lg))
            Text(
                text = "ComfyUI 没起来，现在传图会失败 —— 图是经它落盘的。",
                style = MaterialTheme.typography.bodySmall,
                color = colors.onSurfaceVariant,
            )
        }

        Spacer(Modifier.height(spacing.xxl))
    }
}

@Composable
private fun OutcomeCard(outcome: ConnectionOutcome) {
    val spacing = GrokgenTheme.spacing
    val colors = MaterialTheme.colorScheme
    val status = GrokgenTheme.status

    // ⭐ 文案抽到了 ui/component/ConnectionAdvice.kt，**和上传页共用同一份** ——
    //    「手机没连上 Tailscale」在两个页面是同一件事，说法就该一样。
    val advice = adviceFor(outcome)
    val dotColor = if (outcome is ConnectionOutcome.Healthy) status.done else status.failed

    Column(
        modifier = Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(16.dp))
            .background(colors.surface)
            .padding(spacing.lg),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            // ⚠️ 圆点只是辅助，**文字才是主要的区分手段**（需求 §7.1.3）：
            //    「排队中」和「已取消」用的是同一个灰，光看颜色本来就分不开。
            Box(
                modifier = Modifier
                    .size(8.dp)
                    .clip(RoundedCornerShape(4.dp))
                    .background(dotColor),
            )
            Spacer(Modifier.size(spacing.sm))
            Text(
                text = advice.title,
                style = MaterialTheme.typography.titleMedium,
                color = colors.onBackground,
            )
        }

        if (advice.detail != null) {
            Spacer(Modifier.height(spacing.md))
            Text(
                text = advice.detail,
                style = MaterialTheme.typography.bodySmall,
                color = colors.onSurfaceVariant,
            )
        }

        val health = when (outcome) {
            is ConnectionOutcome.Healthy -> outcome.health
            is ConnectionOutcome.ComfyDown -> outcome.health
            else -> null
        }

        if (health != null) {
            Spacer(Modifier.height(spacing.lg))
            HorizontalDivider(color = colors.outlineVariant)
            HealthDetails(health)
        }
    }
}

@Composable
private fun HealthDetails(health: HealthDto) {
    val spacing = GrokgenTheme.spacing
    val colors = MaterialTheme.colorScheme

    Column(modifier = Modifier.fillMaxWidth()) {
        DetailRow("网关版本", health.gateway.version)

        val gpu = health.comfy.gpu
        if (gpu != null) {
            DetailRow("显卡", gpu)
        }

        val total = health.comfy.vramTotalBytes
        val free = health.comfy.vramFreeBytes
        if (total != null && free != null) {
            // ⭐ 显存读数是「它确实跑在那台服务器上」的证据，不是装饰 ——
            //    和 M1R1 用 vram_total 证明部署成功是同一个手法。
            DetailRow("显存", "${formatGib(total - free)} / ${formatGib(total)} 已用")
        }

        // ⚠️ ffprobe 缺失要单独说，不能混在「在线」里一起显示成正常。
        //    没有它，网关读不出上传图片的尺寸，画布会**静默**退回默认值，
        //    表现为首帧图被拉伸变形 —— 哪里都不报错。
        if (!health.gateway.ffprobe) {
            Spacer(Modifier.height(spacing.md))
            Text(
                text = "服务器上没有 ffprobe：上传的图读不出尺寸，画面比例会按默认值走，" +
                    "首帧可能被拉伸变形。",
                style = MaterialTheme.typography.bodySmall,
                color = GrokgenTheme.status.failed,
            )
        }
    }
}

@Composable
private fun DetailRow(label: String, value: String) {
    val spacing = GrokgenTheme.spacing
    val colors = MaterialTheme.colorScheme

    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = spacing.md),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.Top,
    ) {
        Text(
            text = label,
            style = MaterialTheme.typography.bodySmall,
            color = colors.onSurfaceVariant,
        )
        Spacer(Modifier.size(spacing.lg))
        Text(
            text = value,
            style = MaterialTheme.typography.bodySmall,
            color = colors.onBackground,
        )
    }
}

/** 字节数转成 GiB，保留一位小数。 */
private fun formatGib(bytes: Long): String {
    val gib = bytes.toDouble() / (1024.0 * 1024.0 * 1024.0)
    return String.format("%.1f GB", gib)
}
