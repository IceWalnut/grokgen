package com.icewalnut.grokgen.ui.theme

import androidx.compose.material3.Typography
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp

/**
 * 字号层级。
 *
 * ⚠️ **用系统字体，不打包自定义字体**（需求 §7.1.4）。
 * 界面里有中文，一套覆盖中文的字体文件是 10 MB 级的，
 * 为一个自用 App 付这个体积不值。
 *
 * 只定义实际用得到的几档，不把 Material 3 的全套 15 个角色都填一遍 ——
 * 填了但没人用的角色，将来改起来只会让人不确定它有没有被引用。
 */
val GrokgenTypography = Typography(
    // 页面标题，如「新建视频」「队列」
    headlineSmall = TextStyle(
        fontFamily = FontFamily.Default,
        fontWeight = FontWeight.SemiBold,
        fontSize = 20.sp,
        lineHeight = 26.sp,
    ),
    // 强调性的状态大字，如「正在加载模型」
    titleLarge = TextStyle(
        fontFamily = FontFamily.Default,
        fontWeight = FontWeight.SemiBold,
        fontSize = 22.sp,
        lineHeight = 28.sp,
    ),
    // 区块小标题
    titleMedium = TextStyle(
        fontFamily = FontFamily.Default,
        fontWeight = FontWeight.SemiBold,
        fontSize = 16.sp,
        lineHeight = 22.sp,
    ),
    // 正文，输入框里的文字
    bodyLarge = TextStyle(
        fontFamily = FontFamily.Default,
        fontWeight = FontWeight.Normal,
        fontSize = 15.sp,
        lineHeight = 23.sp,
    ),
    // 列表里的主要文字、参数表的值
    bodyMedium = TextStyle(
        fontFamily = FontFamily.Default,
        fontWeight = FontWeight.Normal,
        fontSize = 14.sp,
        lineHeight = 21.sp,
    ),
    // 标签、说明、notices
    bodySmall = TextStyle(
        fontFamily = FontFamily.Default,
        fontWeight = FontWeight.Normal,
        fontSize = 13.sp,
        lineHeight = 21.sp,
    ),
    // 时间、次要计数，最小的一档
    labelSmall = TextStyle(
        fontFamily = FontFamily.Default,
        fontWeight = FontWeight.Normal,
        fontSize = 12.sp,
        lineHeight = 16.sp,
    ),
)
