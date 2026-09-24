package com.icewalnut.grokgen.ui.theme

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.Immutable
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp

/**
 * 任务的五种状态各自的颜色。
 *
 * ⚠️ **为什么不塞进 MaterialTheme 的标准配色槽位**：
 * Material 3 的配色角色只有 `primary` / `error` / `surface` 这些通用的，
 * **没有「排队中」和「已完成」**。硬塞的话会出现
 * `MaterialTheme.colorScheme.tertiary` 其实是「完成绿」这种只有作者知道的映射。
 *
 * 字段**按内容命名，不按颜色命名** —— 是 `done` 不是 `green`。
 * 将来「完成」改成别的颜色时，改的是值，不是所有引用它的地方。
 */
@Immutable
data class StatusColors(
    val queued: Color,
    val running: Color,
    val done: Color,
    val failed: Color,
    val cancelled: Color,
)

/**
 * 间距尺度：4dp 的倍数（需求 §7.1.4）。
 *
 * 屏幕左右边距固定 16dp。
 */
@Immutable
data class Spacing(
    val xs: Dp = 4.dp,
    val sm: Dp = 8.dp,
    val md: Dp = 12.dp,
    val lg: Dp = 16.dp,
    val xl: Dp = 24.dp,
    val xxl: Dp = 32.dp,
    /** 屏幕左右边距。 */
    val gutter: Dp = 16.dp,
)

private val LocalStatusColors = staticCompositionLocalOf<StatusColors> {
    error("StatusColors 没有被提供 —— 这段界面不在 GrokgenTheme 里面")
}

private val LocalSpacing = staticCompositionLocalOf { Spacing() }

/**
 * 取主题里的扩展 token。
 *
 * ⚠️ 没被 [GrokgenTheme] 包住时会**直接抛异常**，不是悄悄给个默认值 ——
 * 忘了套主题应该立刻看见，而不是得到一套看起来差不多但其实不对的颜色。
 */
object GrokgenTheme {
    val status: StatusColors
        @Composable get() = LocalStatusColors.current

    val spacing: Spacing
        @Composable get() = LocalSpacing.current
}

/**
 * ⚠️ **只有深色一套**（需求 §7.1.1）。
 *
 * 这个 App 的内容是生成出来的图和视频，深色背景最衬内容；
 * 而且它大概率是晚上用的。
 * 只有一套配色意味着**只需要验一遍**。
 *
 * 代价要认：户外强光下比浅色费劲。判断依据是这个 App 连的是家里的机器，基本在家里用。
 * 将来要加浅色时，token 结构已经在这里了，加的是一组值不是一套架构。
 *
 * @param darkTheme 参数保留是为了预览时能强制取值，**但默认忽略系统设置** ——
 *   系统调成浅色时这个 App 仍然是深色的，那是故意的。
 */
@Composable
fun GrokgenTheme(
    @Suppress("UNUSED_PARAMETER") darkTheme: Boolean = isSystemInDarkTheme(),
    content: @Composable () -> Unit,
) {
    val colorScheme = darkColorScheme(
        primary = GrokgenAccent,
        onPrimary = GrokgenOnAccent,
        background = GrokgenBackground,
        onBackground = GrokgenOnBackground,
        surface = GrokgenSurface,
        onSurface = GrokgenOnBackground,
        surfaceVariant = GrokgenSurfaceVariant,
        onSurfaceVariant = GrokgenOnSurfaceVariant,
        outline = GrokgenOutline,
        outlineVariant = GrokgenSurfaceVariant,
        error = GrokgenStatusFailed,
        onError = GrokgenOnAccent,
    )

    val statusColors = StatusColors(
        queued = GrokgenStatusQueued,
        running = GrokgenStatusRunning,
        done = GrokgenStatusDone,
        failed = GrokgenStatusFailed,
        cancelled = GrokgenStatusCancelled,
    )

    CompositionLocalProvider(
        LocalStatusColors provides statusColors,
        LocalSpacing provides Spacing(),
    ) {
        MaterialTheme(
            colorScheme = colorScheme,
            typography = GrokgenTypography,
            content = content,
        )
    }
}
