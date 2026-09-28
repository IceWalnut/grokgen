package com.icewalnut.grokgen.model

import java.time.Duration
import java.time.Instant
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.time.format.DateTimeParseException

/**
 * 解析网关的 `created_at`（ISO 8601 带时区，例如 `2026-09-28T05:58:08.419404+00:00`）。
 *
 * @return 解析失败时 `null` —— 这是展示用的时间，解析不了就不显示，**不让整张卡片崩掉**。
 */
fun parseCreatedAt(createdAt: String): Instant? =
    try {
        OffsetDateTime.parse(createdAt).toInstant()
    } catch (e: DateTimeParseException) {
        null
    }

/**
 * 未结束的任务：「已用 N 秒」/「已用 M 分 S 秒」。
 *
 * ⚠️ 从 `created_at` 算起，**包括排队的时间** —— 这是用户从点下提交到现在等了多久，
 * 不是 GPU 跑了多久。手机与服务器的时钟若有偏差，这个数会偏；小于 0 时按 0 显示。
 */
fun formatElapsed(createdAt: Instant, now: Instant): String {
    val seconds = Duration.between(createdAt, now).seconds.coerceAtLeast(0)
    return if (seconds < 60) "已用 $seconds 秒" else "已用 ${seconds / 60} 分 ${seconds % 60} 秒"
}

/**
 * 已结束的任务：「刚刚」/「N 分钟前」/「N 小时前」/ 更早则显示日期时间。
 */
fun formatRelative(createdAt: Instant, now: Instant, zone: ZoneId = ZoneId.systemDefault()): String {
    val seconds = Duration.between(createdAt, now).seconds.coerceAtLeast(0)
    return when {
        seconds < 60 -> "刚刚"
        seconds < 3_600 -> "${seconds / 60} 分钟前"
        seconds < 86_400 -> "${seconds / 3_600} 小时前"
        else -> DATE_TIME.format(createdAt.atZone(zone))
    }
}

private val DATE_TIME: DateTimeFormatter = DateTimeFormatter.ofPattern("M月d日 HH:mm")
