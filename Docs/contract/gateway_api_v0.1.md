# 网关 API 契约 v0.1

**状态**：草案 · 2026-09-22 · **M1 跑通后按实际实现回填**

这份文档是 **App 和网关之间唯一的约定**。它放在根目录的 `Docs/` 下而不是
`server/Docs/` 或 `app/Docs/` 下，理由只有一条：

> **两端都要读它。** 放进任何一端的目录，另一端就会抄一份，
> 然后两份慢慢对不上，而且没有任何机制会报错。

改这份文档意味着两端都要改。**先改这里，再改两端的代码。**

前缀 `/v1`。除上传与媒体流外全部是 JSON。

---

## 1. 认证与网络

v0.1 **没有认证**。Tailnet 成员即可访问，整个 tailnet 就是信任边界。

网关监听地址、端口与 Tailscale 域名见 `Docs/runbooks/home_gpu_server.md`。
⚠️ **App 里不要写死 IP**，用 MagicDNS 域名——设备重装后 IP 会变，域名不会。

---

## 2. 任务

### `POST /v1/jobs`

提交一个生成任务。

```json
{
  "type": "h3_video",
  "mode": "I2VA",
  "prompt": {
    "description": "画面描述",
    "soundscape": "环境声，可为空",
    "music": "背景音乐，可为空"
  },
  "first_frame_asset_id": "grokgen/img_20260922_ab12.png",
  "last_frame_asset_id": null,
  "width": null,
  "height": null,
  "duration_seconds": 5,
  "turbo": true,
  "steps": null,
  "seed": null
}
```

`mode` 取 `T2VA` / `I2VA` / `FL2VA`。`seed` 为 `null` 时网关随机取一个并回报。

`type` 目前只接受 `"h3_video"`，其他值返回 400。它是 M4 接入 SD 之后的类型判别位，
现在就留着，免得那时改一次请求体形状。

⭐ **`turbo` 决定的是一整套采样参数，不是一个开关。** 网关有两套 profile：
`turbo=true` 是 8 步 + `euler` + shift 6.0/3.0，`turbo=false` 是 25 步 +
`res_multistep` + shift 11.0/3.0。

⚠️ **两套不能混着用** —— 8 步属于 Turbo 区间，而 `res_multistep` + shift 11 是
非 Turbo 那一套的值。所以网关**不提供逐项覆盖采样参数的入口**：
`turbo` 选定 profile，`steps` 留空则取该 profile 的默认值。
实际用的全套参数在响应的 `normalized` 里回报。

⭐ **`width` / `height` 是可选的**，三种情形：

| 情形 | 网关怎么做 |
|---|---|
| 都不给 + 有首帧图 | **按首帧图的宽高比推画布**，贴到 32 的倍数 |
| 都不给 + 没有首帧图（T2VA） | 用默认 `736x416` |
| 给了 | 用用户给的；**宽高比与首帧图不符时在 `notices` 里说明会被拉伸** |

⚠️ **默认应当是「不给」。** 首帧图被拉伸变形是这条链路最容易出的问题 ——
原生节点对首帧用的是 `crop="disabled"`（直接拉伸），不是裁剪。
让画布去适配图片，而不是把图片拉去适配画布。

响应：

```json
{
  "job_id": "job_20260922_0001",
  "state": "queued",
  "normalized": {
    "width": 736,
    "height": 416,
    "length_frames": 124,
    "actual_duration_seconds": 5.17,
    "seed": 849302114,
    "sampler": "euler",
    "steps": 8,
    "shift_video": 6.0,
    "shift_audio": 3.0
  },
  "notices": [
    "时长已调整：请求 5.00 秒，实际 124 帧 = 5.17 秒（模型只接受 17k+5 的帧数）",
    "画布按首帧图的比例推算为 736x416（首帧图 1472x832），避免拉伸变形"
  ]
}
```

⚠️ **`normalized` 不是可选的装饰，App 必须显示它。** 三个值会和用户填的不一样：

- **`actual_duration_seconds`** —— 模型只接受 `17k+5` 的帧数，用户填 5 秒实际得到
  124 帧 = 5.17 秒。不显示的话用户会以为模型不准。
- **`width` / `height`** —— 必须是 32 的倍数，不是的话网关向上取整；
  留空且有首帧图时是**按图片比例推算**出来的，和用户心里的默认值可能完全不同。
- **`seed`** —— 用户没填时网关生成，回报出来才能复现。

后四个 `sampler` / `steps` / `shift_video` / `shift_audio` 是 `turbo` 选定的那套
profile 的实际取值。App 不必显著展示，但**要能在详情里看到** ——
两套 profile 混用会明显影响画质，而那种错不会报错。

⚠️ **`notices` 是给用户看的中文提示，不是日志。** 它说的是「你要的和你会得到的
有哪些差别」，网关生成的就是上面那样的完整中文句子，App 直接显示即可，
不要按前缀去解析它们。

### `GET /v1/jobs/{job_id}`

```json
{
  "job_id": "job_20260922_0001",
  "state": "running",
  "stage": "sampling",
  "progress": 0.42,
  "created_at": "2026-09-22T16:40:00+08:00",
  "normalized": { "...": "同提交时" },
  "outputs": [],
  "failure_reason": null
}
```

`state`：`queued` / `preparing` / `submitted` / `running` / `postprocessing` /
`done` / `failed` / `cancelled`

`stage`（仅 `running` 时有意义）：`loading_model` / `sampling` /
`decoding_video` / `decoding_audio` / `encoding`

⚠️ **`loading_model` 必须单独显示。** 它是分钟级的固定开销，跟这次生成多少内容无关。
App 上把它和 `sampling` 混成一个「生成中」，用户会以为卡死了。

`progress` 只在 `sampling` 阶段有百分比，其余阶段为 `null`。

⚠️ **M1 阶段 `stage` 与 `progress` 恒为 `null`。** 它们的数据来自 ComfyUI 的
WebSocket 事件，而 M1 只做轮询（执行文档 §1）。网关靠 ComfyUI 的队列接口只能
区分到 `submitted`（已提交、还没轮到）与 `running`（正在跑），再细分不出来。
**App 不要把 `stage` 为空当成异常**，等事件流（§6）落地后它才会有值。

完成后 `outputs`：

```json
[{ "kind": "video", "item_id": "itm_20260922_0001",
   "duration_seconds": 5.17, "width": 736, "height": 416,
   "codec": "h264", "container": "mp4", "size_bytes": 1921093 }]
```

⚠️ **M1 阶段 `outputs` 只填得出 `kind` 和文件位置**（`filename` + `subfolder`，
它们来自 ComfyUI 的 `/history`）。`item_id` / `duration_seconds` / `codec` /
`size_bytes` 要读产物文件才知道，那是 M3 媒体库索引的事。
上面这个形状是**目标形状**，不是 M1 的实际返回。

失败时 `failure_reason`：

```json
{
  "kind": "comfy_validation",
  "message": "workflow 校验失败",
  "detail": { "2": { "errors": [{ "type": "required_input_missing",
                                   "details": "channels" }] } }
}
```

`kind` 取 `comfy_validation` / `out_of_memory` / `comfy_unreachable` /
`timeout` / `internal`。

⚠️ **`detail` 要原样带上 ComfyUI 的 `node_errors`，不要压成一句话。**
排障时它是唯一有用的信息。App 可以折叠显示，但不能丢。

### `GET /v1/jobs`

列表，按 `created_at` **倒序**（最新的在前）。

```text
GET /v1/jobs?state=running&limit=20
```

`state` 可选，给了就只返回该状态的任务。`limit` 可选，默认 50，上限 200。

```json
{ "items": [ { "job_id": "...", "state": "...", "...": "同 GET /v1/jobs/{job_id}" } ] }
```

⚠️ **外面套一层 `items`，不要直接返回数组。** 以后要加总数或分页游标时，
裸数组没有地方放，而改形状意味着两端一起改。

每个元素与 `GET /v1/jobs/{job_id}` **形状完全相同**，不做精简版 ——
队列页要显示的东西（状态、进度、失败原因）详情页也要显示，
分成两种形状只会让 App 写两套解析。

### `POST /v1/jobs/{job_id}/cancel`

取消一个任务。返回取消后的任务对象，形状同 `GET /v1/jobs/{job_id}`。

| 任务当前状态 | 结果 |
|---|---|
| `queued` / `preparing` | 直接变 `cancelled`，不会被送去 ComfyUI |
| `submitted` / `running` | 网关请求 ComfyUI 中断，任务变 `cancelled` |
| `done` / `failed` / `cancelled` | **409**，已经是终态了 |

任务不存在返回 404。

⚠️ **取消运行中的任务有一个网关必须处理的细节。** ComfyUI 的中断接口
**没有参数，中断的是它当前正在跑的那一个**，不是按任务 id 取消。
而这台服务器上 ComfyUI 自己的网页界面也在用。
所以网关在中断前会先查 ComfyUI 正在跑的是不是这个任务，
**不是的话就只把任务标成取消，不发中断请求** —— 否则会打断用户手工提交的生成。

### `GET /v1/jobs/{job_id}/video`

取回任务产出的视频文件。响应体是视频本身，不是 JSON。

```text
GET /v1/jobs/job_20260923_0001/video
→ 200 OK
  Content-Type: video/mp4
  Accept-Ranges: bytes
  Content-Length: 516656
```

⭐ **支持 Range 请求，这是手机上能拖进度条的唯一依赖。**

```text
GET /v1/jobs/job_20260923_0001/video
Range: bytes=0-1023
→ 206 Partial Content
  Content-Range: bytes 0-1023/516656
```

⚠️ **App 要看 `Accept-Ranges` 再决定要不要让用户拖。** 响应里没有这个头时
播放器通常会退化成「必须下完才能播」。

| 情况 | 返回 |
|---|---|
| 正常 | 200（或带 `Range` 时 206） |
| 任务不存在 | 404 |
| 任务还没到 `done` | **409**，`detail` 里带当前状态 |
| `done` 但没有产物，或产物文件不在磁盘上 | 404 |

⚠️ **「还没好」返回 409 而不是 404。** 两者对 App 是完全不同的意思：
404 是「这个东西不存在，别再问了」，409 是「再等等，还在生成」。
混成一个，App 就没法决定要不要继续轮询。

⚠️ **M1 阶段一个任务只有一个视频产物**，所以这个路径不带产物 id。
M5 的多参考、批量生成会产出多个，那时要改成 `/outputs/{item_id}` ——
**改的时候这个路径要保留并重定向**，否则已经装在手机上的 App 会全部失效。

---

## 3. 上传

### `POST /v1/uploads/image`

`multipart/form-data`，字段名 `file`。

```json
{ "asset_id": "grokgen/img_20260922_ab12.png",
  "width": 768, "height": 768, "size_bytes": 8589 }
```

⚠️ **上传与提交分离。** 生成任务里只带 `asset_id`，不带大文件——
任务提交要快，上传可以慢。

⚠️ **`asset_id` 是一个路径，不是一个不透明的 id。**
它就是网关在 workflow 里填给 `LoadImage` 的那个值（相对 ComfyUI `input/` 的路径），
`grokgen/` 这一层是为了和用户自己放在 `input/` 里的文件分开 ——
ComfyUI 界面的 LoadImage 下拉框只列顶层文件，所以手机传上去的图不会把它塞满。

**为什么不另造一层 id ↔ 路径的映射**：M1 没有持久化，
多一层映射就多一处网关重启后会丢的状态。App 只管把这个字符串原样传回来。

⚠️ **`width` / `height` 一定要用，别丢。** 不给 `POST /v1/jobs` 指定尺寸时，
网关就是按这两个数推画布的（见下）。

---

## 4. 媒体库（M3 实现，先定形状）

```text
GET /v1/library/items?kind=video|image&source=&page=&page_size=
GET /v1/library/items/{item_id}
GET /v1/library/items/{item_id}/thumb
GET /v1/library/items/{item_id}/stream      ← 必须支持 Range
GET /v1/library/items/{item_id}/download
```

⚠️ **`/stream` 必须支持 HTTP Range 请求**（返回 `206` 与正确的 `Content-Range`）。
这是手机上能不能拖进度条的唯一依赖。

---

## 5. GPU 与服务状态（M4 实现，先定形状）

```text
GET  /v1/gpu
POST /v1/gpu/free
```

```json
{ "mode": "h3_ready",
  "vram_total_bytes": 17170956288,
  "vram_free_bytes": 15647768576,
  "loaded_service": "minimax_h3",
  "switching_eta_seconds": null }
```

`mode`：`idle` / `h3_ready` / `sd_ready` / `switching` / `busy` / `failed`

⚠️ **`switching` 时要给 `switching_eta_seconds`。** 切换要卸载再加载，
是分钟级的，App 必须把它显示成「正在切换」而不是普通排队。

---

## 6. 事件流（M2 或 M3，先定形状）

```text
WS /v1/events
```

推送任务状态变化与 GPU 状态变化。**断线时 App 退回轮询 `GET /v1/jobs/{id}`**——
ComfyUI 重启会导致网关的上游 ws 断开，这不算异常。

---

## 7. 这份契约怎么改

1. 先改本文档
2. 再改网关（`server/`），补测试
3. 再改 App（`app/`）
4. 三步在**同一个 commit** 里 —— 契约和两端的实现不允许分开提交

理由见 `Docs/requirement/grokgen_requirements_v0.1.md` §10.3：
前后端同仓库的唯一目的就是让这种改动能一次做完。
