# grokgen 网关架构 v0.1

**状态**：初稿 · 2026-09-22
**上游**：`Docs/requirement/grokgen_requirements_v0.1.md`
**环境事实**：`Docs/runbooks/home_gpu_server.md`

本文档定四件事：网关怎么分层、怎么构造 workflow、对 App 暴露什么 API、
以及两个状态机（任务、GPU）怎么配合。

本轮所有结论都来自对运行中的 ComfyUI 的实际查询与一次实跑，
每条的核对方式写在 §8。

---

## 1. 最重要的一个决定：绕开 Director，直接用原生 H3 节点

### 1.1 事实

查了运行中 ComfyUI 的 `/object_info`，两个节点的输入长这样：

**`MiniMaxH3Director`**（DaSiWa 的 UI 节点）：

```text
mode            COMBO  T2VA / I2VA / FL2VA / L2VA / REF2VA / Image Inpaint
prompt          STRING
width, height   INT
duration        INT
timeline_data   STRING   ← 默认值 {"version":1,"items":[],"prompt_blocks":[]}
builder_state   STRING
frame_rate      FLOAT
```

**`MiniMaxH3ImageToVideo`**（ComfyUI 自带的原生节点）：

```text
clip, vae       必填
prompt          STRING
width, height   INT
length          INT      帧数
first_frame     IMAGE    可选
last_frame      IMAGE    可选
→ 输出 positive(CONDITIONING), LATENT
```

### 1.2 决定与理由

**网关构造 workflow 时用原生 `MiniMaxH3ImageToVideo`，不用 Director。**

关键在 `timeline_data`：**Director 把用户加的图片、裁剪范围、排序、每段的 prompt
全塞在这一个字符串字段里**，那是它前端 JS 维护的内部状态。
网关要用 Director，就得反推这个 JSON 的私有结构——而它是自定义节点包的内部实现，
作者随时可以改，改了不会有任何编译错误，只会在运行时行为不对。

原生节点没有这个问题：`first_frame` / `last_frame` 是标准的 IMAGE 输入，
而且**它正好覆盖 v0.1 要的三种模式**：

| App 的模式 | 传什么 |
|---|---|
| T2VA | 两个 frame 都不传 |
| I2VA | 只传 `first_frame` |
| FL2VA | `first_frame` + `last_frame` |

⚠️ **代价要认**：Director 的两个便利功能得自己实现——
**对齐指令行**（§2.3）和**参数校验**。这两件事是确定的规则，写进网关是可控的；
反推一个会变的 JSON 结构不可控。

⚠️ **这个决定不影响你在浏览器里继续用 DaSiWa workflow。** 两条路并存：
人工在 ComfyUI 界面里用 Director，App 走网关的原生链路。

### 1.3 REF2VA 为什么必须留到后面

原生的 `MiniMaxH3ReferenceToVideo` 的参考素材输入是 `COMFY_AUTOGROW_V3` 类型
（可增长的动态输入组），构造起来比固定输入复杂得多。
加上需求文档 §8.3 说的模型本身也不对口——两个理由叠加，v0.1 不做。

---

## 2. workflow 怎么构造

### 2.1 视频链路的节点图

⚠️ **本节在 M1R2 被重写过。** 原来那张图是按 `/object_info` 逐个节点推的，
有三处与实际模板不符（见 §2.1.1）。现在这张按**两份真实模板**画：

* `Docs/knowledge/DasiwaMinimaxH3WorkflowsT2VA_cMMH3V23.json` ——
  **用户实际在用的那份**，sigma shift 与采样参数以它为准；
* ComfyUI 自带的 `video_minimax_h3_{i2v,t2v}.json` —— 官方模板，帧数公式取自它。

两份在采样链的结构上完全一致。

网关按下面这张图拼 API 格式的 JSON，提交到 `POST /prompt`：

```text
UNETLoader(unet_name, "default")
   │ MODEL
   ├─(turbo 时)─> LoraLoaderModelOnly(turbo_lora, 1.0)
   ▼
MiniMaxH3SigmaShift(model, shift_video, shift_audio)
   │ MODEL
   ├──> BasicGuider(model, conditioning) ───────────> GUIDER ─┐
   └──> BasicScheduler(model, "simple", steps, 1.0) ─> SIGMAS ─┤
KSamplerSelect(sampler_name) ────────────────────────> SAMPLER ─┤
RandomNoise(noise_seed) ─────────────────────────────> NOISE ───┤
                                                                ▼
CLIPLoader(clip, "minimax", "default") ─┐            SamplerCustomAdvanced
VAELoader(video_vae) ─┬──────────────────┤                   │ LATENT
LoadImage(首帧) ──────┼──> MiniMaxH3ImageToVideo             ├─> VAEDecode(video_vae) ──────> IMAGE ┐
LoadImage(尾帧) ──────┘     (clip, vae, prompt, width,       └─> VAEDecodeAudio(audio_vae) ─> AUDIO ┤
                             height, length,                                                        ▼
                             first_frame?, last_frame?)                       CreateVideo(images, fps=24, audio)
                             │ [0] CONDITIONING ──> BasicGuider                                     │ VIDEO
                             └ [1] LATENT ──────> SamplerCustomAdvanced                             ▼
                                                                               SaveVideo(prefix, "auto", "auto")
```

三个要点：

1. ⭐ **同一个 LATENT 同时喂 `VAEDecode` 和 `VAEDecodeAudio`。**
   H3 的 latent 是音画合一的，两个 VAE 各取所需。这是从模板的连线里读出来的。
2. ⭐ **`CLIPLoader` 必须给 `type="minimax"`。** 漏了它 CLIP 加载不起来，
   而这个值只看 `/object_info` 是看不出来该填什么的。
3. **模式的全部差别就是接几张图。** T2VA 不接、I2VA 接 `first_frame`、
   FL2VA 两个都接 —— 三种模式共用同一个 `MiniMaxH3ImageToVideo` 节点。
   官方的 t2v 与 i2v 模板用的就是同一个 subgraph，这证实了 §1.2 的决定。

⭐ 输出走原生 `CreateVideo` + `SaveVideo(format="auto")`，产出 **H.264 + AAC 的 MP4**。
不用 V23 里那个 `DaSiWa_EnhancedVideoCombine` —— 它默认 Auto 编码，
产出的就是之前发现的那些 AV1/WebM。

#### 2.1.1 原来那张图错在哪

| 原来写的 | 模板实际用的 | 后果 |
|---|---|---|
| `KSampler` | `SamplerCustomAdvanced` + `BasicGuider` + `BasicScheduler` + `KSamplerSelect` + `RandomNoise` | 结构整个不同 |
| 用 `ConditioningZeroOut` 造 negative | **`BasicGuider` 没有 negative 输入** | 那个 hack 是多余的 |
| `CLIPLoader` 只给文件名 | 还要 `type="minimax"` | 会加载失败 |

⚠️ `MiniMaxH3SigmaShift` **原来写对了，不要删**。
官方模板里确实没有它，但用户在用的 V23 有，Settings 面板在驱动它。

#### 2.1.2 采样参数分两套 profile，不能混用

取自 V23 模板 `Settings & Post-Processing` 那段注释：

| | 非 Turbo | **Turbo（用户实际在用）** |
|---|---|---|
| Sampler | `res_multistep` | **`euler`** |
| Steps | 25 | **8** |
| Shift Video | 10–12 | **6** |
| Shift Audio | 3–5 | **3** |

⚠️ **需求文档 §8.2 最初把两套混在了一起**（`res_multistep` + shift 11 + 8 步）——
8 步属于 Turbo 区间，而那两个值是非 Turbo 的。已在需求文档里更正。

落地在 `server/app/models/video_job.py` 的 `TURBO_PROFILE` / `FULL_PROFILE`。

### 2.2 时长换算：帧数是 17k+5 的格子

原生节点的 `length` 是**帧数**，不是秒数，而且它有个硬性的格子。
节点自己的 tooltip 写着：

> Frame count at 24 fps, snapped up to the model's 17k+5 grid
> (124 = ~5s; trained range is ~124-362, longer is untested)

也就是说合法帧数是 `5, 22, 39, 56, ..., 124, 141, ...`（间隔 17）。
**训练过的范围是 124–362 帧**，按 24 fps 换算就是 **约 5.2 秒到 15.1 秒**。

⇒ 网关要做两件事：

```python
def seconds_to_length(seconds: float, fps: float = 24.0) -> int:
    """把用户要的秒数换成模型接受的帧数（17k+5 格子，向上取）。"""
    frames = seconds * fps
    k = max(0, math.ceil((frames - 5) / 17))
    return int(17 * k + 5)
```

⚠️ **换算完要把实际时长回传给 App 并显示出来。**
用户填 5 秒，实际得到的是 124 帧 = 5.17 秒。
不解释的话，用户会以为是模型不准——**这是一个纯粹的取整，不是质量问题**。

⚠️ 超出 124–362 这个范围时网关**不拒绝**，但要标成「未训练范围」。
这符合需求里「宁可结果差也不要拒绝执行」的取向。

### 2.3 对齐指令行：Director 替你加的那一句，现在得自己加

给了首帧或尾帧时，**全局 prompt 的第一行必须是一条说明「第几张图对齐到第几秒」的句子**。
这是 H3 的 prompt 约定，不是 Director 的发明，所以绕开 Director 之后仍然要加。

只有首帧：

```text
For the target video, at 0.00 seconds into the target video, Picture 1 (from Shot 1) is fully referenced.

integrated_multimodal_description: ...
```

⚠️ **写法有两个版本，本项目按上面这个（不带括号）。**
Director 的**文档**里写的是 `<Picture 1> (from [Shot 1])`，带尖括号和方括号；
而用户在用的 V23 模板里，作者自己给的示例（MarkdownNote `#2695`）**不带括号**。

两种写法 ComfyUI 都不会报错，**差别只体现在生成质量上，没有便宜的验证方式** ——
属于「不会报错只会变差」那一类。选模板示例的理由是：那是作者的实际用法，
也是用户一直在用的。代码注释里记了另一种写法的存在。

首帧 + 尾帧：

```text
How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the {duration}.00-second mark of the target video.

integrated_multimodal_description: ...
```

`{duration}` 用**换算后的实际秒数**，不是用户填的那个数（5 秒 → `5.17`）。
分隔符是 em-dash `—`，不是普通连字符。

⚠️ **这是本轮最容易写错、且写错不会报错的一处**：
模型会把尾帧对到错误的时间点，只表现为结果变差。`VS-3` 专门钉这一条。

**这一段由网关拼装，App 不碰。** App 只传三段内容：画面描述、环境声、背景音乐。
网关负责拼成：

```text
<对齐指令行，仅在有图时>

integrated_multimodal_description: <画面描述>

overall_soundscape: <环境声，留空则整段省略>

non_diegetic_music: <背景音乐，留空则填 N/A>
```

⚠️ **空的 `non_diegetic_music` 要填 `N/A`，不是留空。**
这是 Director 的自动规则（V23 模板 Quick Start 注释里写明），
绕开 Director 之后必须自己复制。

⚠️ **这段拼装逻辑必须有单元测试**，而且测试要能在开发机上跑——
它是纯字符串处理，不需要 GPU，没有理由不测。

### 2.4 输出格式：不需要转码

**需求文档 §6 里「必须做转码」那个结论，本轮被推翻了。**

原来的判断基于：现有输出是 AV1 + Opus 的 WebM。
但那是 **DaSiWa workflow 选的** `DaSiWa_EnhancedVideoCombine` 节点决定的，
不是模型的属性。

ComfyUI 自带的 `CreateVideo` 有个 `codec` 参数，取值是 `none / auto / h264 / av1`；
`SaveVideo` 的 `format` 是 `auto / mp4 / mkv / webm`，
其 tooltip 明确写着「Auto uses MP4 for Auto/H.264 and WebM for AV1」。

**实跑验证过**（2026-09-22）：提交一个纯 CPU 的小 workflow
（`EmptyImage` 24 帧 + `EmptyAudio` → `CreateVideo(codec=h264)` → `SaveVideo(format=auto)`），
`ffprobe` 结果是：

```text
codec_name=h264   profile=High
codec_name=aac    profile=LC
format_name=mov,mp4,m4a,3gp,3g2,mj2
```

⇒ **ComfyUI 直接产出手机能播的 H.264 + AAC MP4，网关不需要 ffmpeg 转码。**

**保留 ffmpeg 作为兜底**，两个场景：
（1）用户要看的是之前用 DaSiWa workflow 生成的老 WebM 文件；
（2）将来某个 workflow 又输出了别的格式。
媒体库索引里记下每个文件的实际编码，只在不是 H.264 MP4 时才转，转完缓存。

---

## 3. 分层：ComfyUI 客户端必须是可替换的

```text
server/app/
├── main.py              FastAPI 装配
├── api/                 HTTP 路由，只做参数校验与序列化
│   ├── jobs.py
│   ├── library.py
│   ├── uploads.py
│   ├── gpu.py
│   └── events.py        WebSocket
├── core/
│   ├── jobs.py          任务状态机 + 串行队列
│   ├── gpu.py           GPU 状态机
│   └── config.py
├── comfy/
│   ├── client.py        ComfyClient 协议（Protocol/ABC）← 唯一的 GPU 边界
│   ├── http_client.py   真实实现，说 HTTP + WebSocket
│   ├── fake_client.py   测试替身
│   └── workflows/
│       ├── h3_video.py  §2 那张图的构造代码
│       └── sd_image.py
├── media/
│   ├── index.py         媒体库索引
│   └── probe.py         ffprobe、缩略图、按需转码
└── models/              pydantic 请求/响应模型
```

**唯一的设计硬约束**：所有对 ComfyUI 的调用都经过 `comfy/client.py` 里的那个接口。

理由在需求文档里已经写过，这里说它的可检验形式：

> **`core/`、`media/`、`api/` 里不允许出现任何 `http` 调用或 ComfyUI 的地址。**
> 检验方式：在开发机（没有 GPU、连不上服务器）上应当能跑通除
> `comfy/http_client.py` 之外的全部单元测试。**跑不通就说明分层破了。**

接口大致是：

```python
class ComfyClient(Protocol):
    async def submit(self, workflow: dict, client_id: str) -> str: ...      # -> prompt_id
    async def history(self, prompt_id: str) -> JobRecord: ...
    async def interrupt(self) -> None: ...
    async def free(self, unload_models: bool, free_memory: bool) -> None: ...
    async def system_stats(self) -> SystemStats: ...
    async def upload_image(self, data: bytes, name: str) -> str: ...
    def events(self, client_id: str) -> AsyncIterator[ComfyEvent]: ...      # WebSocket
```

### 3.1 ComfyUI 那侧的 API（实测存在）

从 `server.py` 的路由表读出来的，网关只用到这几个：

| 路由 | 用途 |
|---|---|
| `POST /prompt` | 提交 workflow，返回 `prompt_id`；**参数错误时返回结构化的 `node_errors`** |
| `GET /history/{prompt_id}` | 任务结果，含输出文件名与 subfolder |
| `GET /queue` | 队列状态 |
| `POST /interrupt` | 中断当前任务 |
| `POST /free` | **显存释放**，body `{"unload_models": true, "free_memory": true}` |
| `GET /system_stats` | **显存与版本信息** |
| `POST /upload/image` | 上传图片到 `input/` |
| `GET /ws` | 进度事件 |
| `GET /object_info` | 节点定义，网关启动时用它自检 |

⭐ **`POST /prompt` 的校验错误是结构化的**，形如：

```json
{"error": {"type": "prompt_outputs_failed_validation"},
 "node_errors": {"2": {"errors": [{"type": "required_input_missing",
                                   "details": "channels"}]}}}
```

⇒ **网关要把它原样翻译给 App**，不要压成一句「生成失败」。
这是排障时唯一有用的信息。

---

## 4. 任务状态机

```text
queued ──> preparing ──> submitted ──> running ──> postprocessing ──> done
   │           │             │            │              │
   └───────────┴─────────────┴────────────┴──────────────┴──> failed
   │
   └──> cancelled
```

| 状态 | 含义 | 谁来推进 |
|---|---|---|
| `queued` | 在网关的队列里等 | 网关 |
| `preparing` | 上传素材、拼 workflow、必要时切换 GPU 服务 | 网关 |
| `submitted` | 已提交给 ComfyUI，等它开始 | ComfyUI 的 ws 事件 |
| `running` | 正在跑，带 `stage` 与 `progress` | ComfyUI 的 ws 事件 |
| `postprocessing` | 建缩略图、写索引、必要时转码 | 网关 |
| `done` / `failed` / `cancelled` | 终态 | — |

`running` 的 `stage` 要能区分这几段，因为耗时量级差得很远：

```text
loading_model   分钟级，用户最需要知道的就是这一段
sampling        带百分比
decoding_video
decoding_audio
encoding
```

⚠️ **失败必须带原因**，至少分清：ComfyUI 校验失败（附 `node_errors`）、
显存不足、ComfyUI 进程不可达、超时。需求文档里说的「失败必须可见」，
落地就是这个 `failure_reason` 字段。

---

## 5. GPU 状态机

```text
idle ──> switching ──> h3_ready ──┐
  ▲                               │
  └──────── switching <───────────┘
                │
                └──> sd_ready
```

规则（需求 F7）：

1. **同一时间只允许一个重 GPU 任务。** 队列串行，不并发。
2. 切换前调 `POST /free {"unload_models": true, "free_memory": true}`。
3. **轮询 `GET /system_stats` 的 `vram_free` 等它回落**，不要固定 sleep。
4. 回落不到阈值就**重启 ComfyUI 进程**，重启也不行就把 GPU 标成 `failed` 报给 App。

⭐ **不需要 `nvidia-smi`**：ComfyUI 的 `/system_stats` 直接给显存数字。
实测返回（2026-09-22，空闲时）：

```text
name: cuda:0 NVIDIA GeForce RTX 4080 SUPER
vram_total: 17170956288   (约 16.0 GiB)
vram_free:  15647768576   (约 14.6 GiB)
```

⇒ 网关走 HTTP 就够，不用 ssh 到 shell 里去读，边界更干净。

⚠️ **SD 和 H3 都在同一个 ComfyUI 进程里**（需求 §4.2 的决定），
所以「切换」不是换进程，而是**换一套模型**：卸掉当前的，加载另一套。
`/free` 够用的时候不要重启进程——重启要多付一次几分钟的加载开销。

---

## 6. 对 App 的 API

**接口契约不在本文档里**，在 `Docs/contract/gateway_api_v0.1.md`。

那份文档放在仓库根目录的 `Docs/` 下，因为 **App 和网关都要读它**。
放进 `server/Docs/` 的话，App 那边会抄一份，然后两份慢慢对不上，
而且不会有任何机制报错。

本文档只负责网关内部怎么实现那份契约；契约本身的字段、状态取值、错误形状，
以那份文档为准。

⚠️ **`/stream` 的 Range 支持要自己确认一遍。** FastAPI/Starlette 的
`FileResponse` 支持 Range，但这是播放能不能拖进度条的唯一依赖，
**属于必须写测试的那一类**，不能靠"框架应该支持"。

### 6.1 网关与 ComfyUI 的目录关系

网关和 ComfyUI 在同一台机器、同一个用户（`icewalnut`）下，
所以**媒体库直接读文件系统，不经过 ComfyUI 的 `/view`**：

```text
~/workspace/ComfyUI/output/          生成结果，按日期分子目录
~/workspace/ComfyUI/input/           上传的素材
~/workspace/grokgen-media/thumbs/    缩略图，网关自己管
~/workspace/grokgen-media/index.db   媒体索引
```

⚠️ **缩略图和索引不要写进 ComfyUI 的目录**，否则 ComfyUI 的输出目录里会混进
不是它产出的东西，扫描逻辑会互相干扰。

---

## 7. 尚未决定的

1. **索引用 SQLite 还是 JSON 文件。** 倾向 SQLite（分页和按来源过滤都要查询），
   但媒体数量不大时 JSON 也够。
2. **进度是转发 ComfyUI 的 ws 还是让 App 轮询。** 倾向转发，
   但要处理 ComfyUI 重启导致 ws 断开的情况。
3. **缩略图在 `postprocessing` 阶段做，还是首次访问时懒生成。**
   倾向前者——反正那时文件刚写完，磁盘缓存还热。
4. ~~SD 的模型怎么归位到 ComfyUI~~ **已完成：目录级软链接，已验证 ComfyUI 跟随。**
   见 `server/Docs/implementation/M1_gateway_mainline.md` §5。
5. ~~`width`/`height` 有没有整除约束~~ **已核实：必须是 32 的倍数。**
   节点源码里声明了 `step=32`，latent 空间尺寸是 `height // 16, width // 16`。
   同时发现**首帧是拉伸、尾帧是居中裁剪**，两者处理方式不同。
   详见 `server/Docs/implementation/M1_gateway_mainline.md` §2.1。

---

## 8. 本文档的核对方式

2026-09-22 全部对运行中的 ComfyUI（`icewalnut-1060:8188`，版本 0.36.0）实测：

- 节点输入输出：`GET /object_info`，1278 个节点，逐个读
  `MiniMaxH3Director` / `MiniMaxH3DirectorGuide` / `MiniMaxH3ImageToVideo` /
  `MiniMaxH3ReferenceToVideo` / `MiniMaxH3SigmaShift` / `KSampler` /
  `CreateVideo` / `SaveVideo` 的 schema
- 帧数格子：服务器上 `grep length comfy_extras/nodes_minimax_h3.py`，读节点自己的 tooltip
- 路由表：服务器上 `grep 'routes.(get|post)' server.py`
- 显存读数：`GET /system_stats`
- **输出编码：实跑一个纯 CPU 的 workflow，`ffprobe` 验证产物是 h264 + aac + mp4，
  验完删掉了 `output/probe/`**

⚠️ 一个会咬人的环境问题：**开发机的 shell 里有 `http_proxy` / `all_proxy` 指向
`127.0.0.1:7890`，它会拦截发往 Tailscale 地址的请求**，表现是 502。
用 `curl --noproxy '*'`，或者把 tailnet 域名加进 `NO_PROXY`。
（网关自己跑在服务器上，不受这个影响；这只影响从开发机做验证。）
