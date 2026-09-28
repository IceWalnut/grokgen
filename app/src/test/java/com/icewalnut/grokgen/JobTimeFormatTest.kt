package com.icewalnut.grokgen

import com.icewalnut.grokgen.model.formatElapsed
import com.icewalnut.grokgen.model.formatRelative
import com.icewalnut.grokgen.model.parseCreatedAt
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import java.time.Instant
import java.time.ZoneId

class JobTimeFormatTest {

    private val created = parseCreatedAt("2026-09-28T05:58:08.419404+00:00")!!

    @Test
    fun `能解析网关真实返回的 created_at`() {
        // 这是 M2R3 真机任务 job_20260928_0001 的原值：微秒精度 + 显式时区。
        assertEquals(Instant.parse("2026-09-28T05:58:08.419404Z"), created)
    }

    @Test
    fun `解析不了时返回 null 不抛`() {
        assertNull(parseCreatedAt("not a time"))
    }

    @Test
    fun `已用时间`() {
        assertEquals("已用 32 秒", formatElapsed(created, created.plusSeconds(32)))
        assertEquals("已用 1 分 35 秒", formatElapsed(created, created.plusSeconds(95)))
        // 手机时钟比服务器慢时不显示负数。
        assertEquals("已用 0 秒", formatElapsed(created, created.minusSeconds(5)))
    }

    @Test
    fun `相对时间`() {
        val zone = ZoneId.of("Asia/Shanghai")
        assertEquals("刚刚", formatRelative(created, created.plusSeconds(10), zone))
        assertEquals("12 分钟前", formatRelative(created, created.plusSeconds(12 * 60), zone))
        assertEquals("2 小时前", formatRelative(created, created.plusSeconds(2 * 3600 + 5), zone))
        // 05:58 UTC = 13:58 北京时间
        assertEquals("9月28日 13:58", formatRelative(created, created.plusSeconds(2 * 86_400), zone))
    }
}
