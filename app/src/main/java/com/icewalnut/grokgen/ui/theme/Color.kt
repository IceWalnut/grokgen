package com.icewalnut.grokgen.ui.theme

import androidx.compose.ui.graphics.Color

/**
 * 全 App 的颜色 token。
 *
 * ⭐ **这是唯一允许出现颜色字面量的地方**（`ui/theme/` 包）。
 * 这条约束由 `SourceConstraintsTest` 强制，编号 VS-41。
 * 它守的是「换配色时改一处就够」—— 颜色一旦散进各个页面，
 * 换配色就变成一次全仓库搜索替换，而那种替换会顺手改到不该改的地方**且不报错**。
 *
 * 方向、取值理由、对比度都在需求文档 §7.1.2，**这里不复述**。
 * 那一节里每个值的对比度都是按 WCAG 相对亮度公式算过的，不是估的。
 *
 * ⚠️ **只有深色一套，没有 light/dark 分支**（需求 §7.1.1）。
 * 这省掉的不只是一组值，还有「每个页面要在两套配色下各看一遍」这件事。
 */

// ---- 底层与容器 ----

/** 页面底色。用近黑而不是纯黑：纯黑在 OLED 上滚动容易拖影，配白字对比也过硬（21:1）。 */
val GrokgenBackground = Color(0xFF0E0F11)

/** 卡片、输入区的面。 */
val GrokgenSurface = Color(0xFF16181B)

/** 次级容器，比卡面再亮一档。 */
val GrokgenSurfaceVariant = Color(0xFF1F2226)

/** 分隔线。不承载信息，所以不要求达到对比度判定线。 */
val GrokgenOutline = Color(0xFF2E3339)

// ---- 文字 ----

/** 正文。在底色上 15.91:1。 */
val GrokgenOnBackground = Color(0xFFE8EAED)

/** 次级文字与标签。在底色上 7.26:1。 */
val GrokgenOnSurfaceVariant = Color(0xFF9AA0A6)

/** 禁用态。在底色上 3.88:1 —— 达到 UI 组件的 3:1 线，但**不该用来放正文**。 */
val GrokgenDisabled = Color(0xFF6B7177)

// ---- 强调色 ----

/**
 * 唯一的强调色，只出现在主操作与「运行中」状态上。
 *
 * 选冷色是有理由的：生成出来的图和视频色相是任意的，
 * **冷色强调在暖色内容旁边仍然分得清**，暖色强调会和内容抢。
 */
val GrokgenAccent = Color(0xFF4CC2FF)

/** 主按钮上的文字色。在强调色上 7.78:1。 */
val GrokgenOnAccent = Color(0xFF06263A)

// ---- 任务状态 ----
//
// ⚠️ 这几个**不放进 MaterialTheme 的标准配色槽位**，理由见 Theme.kt 的 StatusColors。
// ⚠️ 「排队中」和「已取消」故意是同一个灰 —— 所以状态**不许只靠颜色区分**，
//    必须同时有文字或图标（需求 §7.1.3）。

/** 排队中。在底色上 6.02:1。 */
val GrokgenStatusQueued = Color(0xFF8A9199)

/** 生成中。与强调色是同一个值：强调色本身就是「正在发生」的颜色。 */
val GrokgenStatusRunning = GrokgenAccent

/** 完成。在底色上 10.72:1。 */
val GrokgenStatusDone = Color(0xFF5BD98A)

/** 失败。在底色上 6.91:1。 */
val GrokgenStatusFailed = Color(0xFFFF6B6B)

/** 已取消。与「排队中」同色，见上面那条警告。 */
val GrokgenStatusCancelled = GrokgenStatusQueued
