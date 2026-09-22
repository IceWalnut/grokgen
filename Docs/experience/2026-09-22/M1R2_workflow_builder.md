# M1R2 总结：workflow 构造器

日期：2026-09-22
执行文档：`server/Docs/implementation/M1_gateway_mainline.md` §3 M1R2

**要解决的问题**：workflow 拼得对不对，能在开发机上回答。
**结论**：解决了。VS-1〜VS-4 通过，47 条测试全绿，不占 GPU。

---

## 1. 本轮最重要的事：找到了用户实际在用的模板

上一版计划把 ComfyUI 自带的官方模板当成权威参考。**那是错的** ——
用户实际用的是 `DasiwaMinimaxH3WorkflowsT2VA_cMMH3V23.json`，
它**不在 ComfyUI 目录里**，只存在于 Windows 的 `E:\Downloads\minimax-h3\`
（手动拖进前端加载的）。仓库自带的只有 V13 / V16，84 KB，与 V23 的 md5 都不同。

已经过 WSL 的 `/mnt/e/` 取出，放进 `Docs/knowledge/`（173 KB），并写了来源说明。
放仓库里的好处是**服务器那份检出会自动拿到它**，等于同时落进 WSL。

它纠正了三件事，每件都会直接影响生成结果：

### 1.1 采样参数分两套 profile，需求文档原来混了

V23 的 `Settings & Post-Processing` 注释写得很清楚：

| | 非 Turbo | Turbo |
|---|---|---|
| Sampler | `res_multistep` | `euler` |
| Steps | 25 | 4–8 |
| Shift Video | 10–12 | 6–8 |

需求文档 §8.2 原来写的是「`res_multistep` + shift 11 + steps 8–12」——
**8 步属于 Turbo 区间，而那两个值是非 Turbo 那一套的**。
这组混合值来自更早一次对着截图的分析，当时没有「两套 profile」的概念。

**用户实际在用的是 Turbo**：面板存的是 `euler / simple / 8 / shift 6 / 3`。
需求 §8.2 与架构 §2.1.2 都已更正，代码里落成 `TURBO_PROFILE` / `FULL_PROFILE`。

### 1.2 对齐指令行的写法与 Director 文档不一致

V23 里有两个现成示例（MarkdownNote `#2695` 单帧、`#2696` 首尾帧），
**不带尖括号也不带方括号**：

```text
For the target video, at 0.00 seconds into the target video, Picture 1 (from Shot 1) is fully referenced.
```

而 Director 的**文档**写的是 `<Picture 1> (from [Shot 1])`。

⚠️ **这个分歧没有便宜的验证方式**：两种写法 ComfyUI 都不报错，
差别只在生成质量上。本轮按 V23 示例实现（作者自己的用法，也是用户一直在用的），
代码注释里记了另一种写法的存在。**这条要留到能听/能看结果时才有可能判定。**

### 1.3 空的 `non_diegetic_music` 要填 `N/A`

Director 的自动规则（V23 Quick Start 注释）。绕开 Director 之后要自己复制。

---

## 2. 架构文档 §2.1 的节点图本轮重写

原图是按 `/object_info` 逐个节点推的，有三处与两份真实模板都不符：

| 原来写的 | 模板实际用的 | 后果 |
|---|---|---|
| `KSampler` | `SamplerCustomAdvanced` + `BasicGuider` + `BasicScheduler` + `KSamplerSelect` + `RandomNoise` | 结构整个不同 |
| 用 `ConditioningZeroOut` 造 negative | **`BasicGuider` 没有 negative 输入** | 那个 hack 是多余的 |
| `CLIPLoader` 只给文件名 | 还要 `type="minimax"` | **会加载失败** |

⚠️ `MiniMaxH3SigmaShift` **原来写对了**。官方模板里没有它，一度让我准备删掉；
但用户在用的 V23 有，Settings 面板在驱动它，所以保留。

---

## 3. 验证了什么

### Level 2 自动化测试（开发机，不占 GPU）

```bash
server/.venv/bin/python -m pytest -m "not integration" -q
# 47 passed
```

| VS | 判据 |
|---|---|
| VS-1 | 点位 `1s→39` / `5s→124` / `5.17s→124` / `5.2s→141` / `15s→362`，**外加不变量 `n % 17 == 5`** |
| VS-2 | T2VA 无 frame 键；I2VA 只有 `first_frame`；FL2VA 两张图接**两个不同**的 `LoadImage` |
| VS-3 | 对齐行确实出现，FL2VA 含 `5.17-second` 且**不含** `5.00-second`；空 music 变 `N/A` |
| VS-4 | `768x432 → 768x448` 且有 notice；已对齐时**不产生** notice |

另外钉了几条不在原判据里但值得守的：turbo 关掉时**图里根本没有 LoRA 节点**
（不是把强度设成 0）、图里不出现 `ConditioningZeroOut` / `KSampler`、
`CLIPLoader` 的 `type` 是 `minimax`、两个 VAE 解码器吃同一个 latent。

### golden 回归

`server/tests/golden/h3_{t2va,i2va,fl2va}.json` 三份，**无自动覆盖开关**。
写入前逐份核对过连线与取值。

### 判据自检：三条植入缺陷都被抓住

按「一条从未失败过的判据不是判据」：

| 植入的缺陷 | 谁报错 |
|---|---|
| 对齐行秒数写死成 `5.00` | `test_fl2va_alignment_line_uses_the_converted_duration` + golden `h3_fl2va` |
| `FRAME_GRID_STEP` 17 改成 16 | 5 条点位 + 多条不变量断言 |
| T2VA 也接上首帧 | `test_t2va_graph_has_no_frame_inputs` + golden `h3_t2va` |

**每一条都是单元测试与 golden 同时报错**，验证完全部恢复，47 条仍全绿。

---

## 4. 哪些验证失败过 / 改过判据

**`Docs/Validation.md` 里 VS-1 原写的点位 `1s→22` 是错的。**
实际是 39 —— `round(1 × 24) = 24` 已经越过 22 那一格，向上贴到 39。
写测试时才发现。**这是判据写错，不是实现不对**，已在表里注明原值错在哪。

---

## 5. 没验证什么

- **ComfyUI 认不认这张图，完全没验。** 本轮一次都没提交给 ComfyUI，不占 GPU。
  「`/prompt` 返回 `node_errors` 为空」是 VS-5，属于 M1R3。
- **生成结果好不好，无从验。** 对齐指令行那两种写法的差别、shift 6 对不对、
  8 步够不够 —— 这些只有真跑出来看画面才知道。
- **`LoadImage` 引用的文件名是否真的存在于 ComfyUI 的 `input/` 下**，没验。
  本模块是纯函数，不读文件系统。上传要到 M1R4。
- **`filename_prefix` 写死成 `video/grokgen`**，没有按日期分目录，
  也没验证 ComfyUI 会把它落在哪。媒体库（M3）要用到时再定。
- **超长时长的行为**只验了「会警告不会拒绝」，没验模型在 362 帧以上实际会怎样。

## 6. 遗留风险

| 风险 | 说明 |
|---|---|
| 对齐指令行两种写法 | 没有便宜的验证方式，两种都不报错。M1R3 出画面后也未必能判 |
| UI 格式转 API 格式可能漏字段 | golden 挡不住「模板里有而我根本没想到」的节点。M1R3 的 `node_errors` 是第一道真检查 |
| turbo LoRA 与 `h3ErosMax_beta5_fp8` 可能 shape mismatch | M1R3 才会暴露。退路：`turbo=False` → `res_multistep` / 25 步 / shift 11 |
| `notices` 是中文硬编码在网关里 | 将来 App 要做多语言时得挪位置。现在不值得抽象 |

## 7. 下一轮

**M1R3：ComfyUI 客户端 + 第一次真实生成。** 先手工把本轮生成的 workflow
`curl` 给 `/prompt` 看它认不认，再写进代码。对应 VS-5〜VS-7。

⚠️ 跑之前先 `curl /queue` 确认 ComfyUI 空闲；模型加载是分钟级固定开销。
