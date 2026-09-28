package com.icewalnut.grokgen.model

import com.icewalnut.grokgen.net.dto.NormalizedParamsDto
import java.util.Locale

/** 「网关实际使用的参数」卡片里的一行。 */
data class SummaryRow(val label: String, val value: String)

/**
 * 把网关回报的 `normalized` 变成界面上的五行。
 *
 * 契约 §2 要求九个字段都能看到，这里合成五行（与视觉稿「任务详情」画板一致）：
 * 分辨率 = width × height；时长 = 实际秒数 · 帧数；种子；
 * 采样 = sampler · steps · 档位；shift = 视频 / 音频。
 *
 * ⚠️ **格式化规则只写在这一处**（App 架构文档 §3.2），不散在 Composable 里。
 *
 * @param turbo 用户提交时选的档位。`normalized` 里没有 turbo 这个字段，
 *   但 sampler/steps/shift 就是那一档的值，并排显示用户才对得上号。
 *   **不知道时传 `null`**（例如从任务详情进来）：那一行只显示 sampler 与步数。
 *   ⚠️ **不要按步数去猜是不是 Turbo** —— 那等于在 App 里再抄一份网关的采样档位规则，
 *   网关改了档位 App 就会报错的名字。
 */
fun summarizeNormalized(normalized: NormalizedParamsDto, turbo: Boolean?): List<SummaryRow> = listOf(
    SummaryRow("分辨率", "${normalized.width} × ${normalized.height}"),
    SummaryRow(
        "时长",
        "${formatSeconds(normalized.actualDurationSeconds)} 秒 · ${normalized.lengthFrames} 帧",
    ),
    SummaryRow("种子", normalized.seed.toString()),
    SummaryRow(
        "采样",
        buildString {
            append("${normalized.sampler} · ${normalized.steps} 步")
            when (turbo) {
                true -> append(" · Turbo")
                false -> append(" · 标准")
                null -> Unit
            }
        },
    ),
    SummaryRow(
        "shift",
        "${formatShift(normalized.shiftVideo)} / ${formatShift(normalized.shiftAudio)}",
    ),
)

/**
 * 秒数保留两位小数：网关回报 5.166…时显示 5.17，与它 notices 里的写法一致。
 *
 * ⚠️ 固定 `Locale.ROOT` —— 用系统语言格式化，某些地区会把小数点写成逗号。
 */
internal fun formatSeconds(seconds: Double): String = String.format(Locale.ROOT, "%.2f", seconds)

/**
 * 时长按钮上的**请求**秒数：整数不带小数点（5 而不是 5.0），非整数保留两位。
 * 与 [formatSeconds] 分开，是因为按钮上显示的是用户选的档位，不是网关回报的实际值。
 */
fun formatSecondsForChip(seconds: Double): String =
    if (seconds % 1.0 == 0.0) seconds.toLong().toString() else formatSeconds(seconds)

/** shift 保留一位小数：6.0 / 3.0，与契约和视觉稿一致。 */
internal fun formatShift(shift: Double): String = String.format(Locale.ROOT, "%.1f", shift)
