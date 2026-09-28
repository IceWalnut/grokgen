package com.icewalnut.grokgen.net

import kotlinx.serialization.json.Json

/**
 * 与网关收发 JSON 用的**唯一**一个 [Json] 实例。
 *
 * ⚠️ **线上与测试必须共用它。** `SubmitRequestGoldenTest`（VS-34）拿它序列化请求体
 * 再与 golden 文件比对 —— 测试要是自己 new 一个 `Json`，测试是绿的，
 * 线上发出去的却可能是另一个形状，而网关那边 pydantic 会把多出来的字段**静默丢掉**。
 *
 * ⚠️ `ignoreUnknownKeys = true`：网关将来往响应里加字段，不该让 App 崩。
 * 实际上它已经在用了：提交响应的 `normalized` 里比契约多一个 `notices`。
 *
 * ⚠️ **不要**顺手加 `coerceInputValues` 之类的宽容开关 ——
 * 那会把「字段类型不对」这种真错误也吞掉，而契约对不上正是要看见的东西。
 *
 * ⚠️ **也不要改 `encodeDefaults` / `explicitNulls`。** 请求体里 `width` / `height` /
 * `steps` / `seed` 留空时必须写成 `null` 而不是被省略（契约 §2）。
 * 现在靠的是两条默认行为的组合：
 * - `explicitNulls` 默认 `true` ⇒ **没有默认值**的可空属性会写出 `null`；
 * - `encodeDefaults` 默认 `false` ⇒ **有默认值**且等于默认值的属性会被**省略**。
 *
 * ⇒ 请求数据类（如 `JobSubmitRequestDto`）的属性**一律不给默认值**。
 * 这两处任何一处被改，golden 测试会报出缺了哪些键。
 */
internal val GatewayJson: Json = Json {
    ignoreUnknownKeys = true
}
