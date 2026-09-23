# M1 执行文档：网关主链路

**状态**：待执行 · 2026-09-22
**上游**：`server/Docs/architecture/gateway_architecture_v0.1.md`
**目标**：需求文档 §9 的 M1 —— 网关能提交一个 MiniMax H3 任务、报告状态、取回视频。

---

## 1. 退出标准

M1 完成的判据只有一条，必须能实际做到：

> 在开发机上执行一条 `curl`，提交一个带首帧图的 I2VA 任务，
> 轮询到任务完成，下载回一个**能在手机上播放的 H.264 MP4**。

⚠️ 「接口返回 200」不算完成。**判据是拿到一个能播的文件。**

M1 **不做**：Android 客户端、媒体库、SD、显存切换、WebSocket 进度推送。
这些分别在 M2–M4。M1 的进度用轮询就够。

---

## 2. 前置事实（都已实测确认）

开工前不需要再验证的事：

| 事实 | 在哪验证过 |
|---|---|
| 服务器能 `git clone` 本仓库，`git pull` 部署链路通 | 已实跑 |
| ComfyUI 0.36.0 在 `0.0.0.0:8188`，从开发机经 Tailscale 可达 | 已实跑 |
| 原生 `MiniMaxH3ImageToVideo` 的输入签名 | `/object_info` |
| `CreateVideo(codec=h264)` + `SaveVideo` 产出 h264+aac+mp4 | 已实跑并 ffprobe 验证 |
| `/prompt`、`/history`、`/system_stats`、`/upload/image`、`/free` 都存在 | 读 `server.py` 路由表 |
| 服务器有 Python 3.10.12、venv 可用 | 已实跑 |
| 开发机也是 Python 3.10.12 | 已确认 |

⚠️ **开发机的 `http_proxy` 会拦截 Tailscale 请求（502）**，
所有从开发机发起的验证都要带 `--noproxy '*'`。见 runbook §6.4。

### 2.1 本轮新确认的两个约束

**分辨率必须是 32 的倍数。** 节点源码里 `width` / `height` 都声明了 `step=32`，
latent 的空间尺寸是 `height // 16, width // 16`。

⚠️ **需求文档 §8.2 里建议的 `768x432` 是无效值** —— `432 / 32 = 13.5`。
那个值来自官方模板的中文说明，不适用于这里直接调原生节点的路径。

**M1 用 `736x416`**：两个都是 32 的倍数，而且这是服务器上已有成品
`MiniMax_H3_00002_.mp4` 的真实分辨率，**是已知能跑出来的尺寸**。

**首帧和尾帧的缩放方式不一样。** 读节点源码：

```python
img = _resize(first_frame[:1], width, height, "disabled")   # 首帧：直接拉伸
img = _resize(last_frame[:1],  width, height, "center")     # 尾帧：居中裁剪
```

⇒ **首帧图如果宽高比和目标不一致，会被拉伸变形**，不是裁剪。
网关应当在上传或提交时按目标宽高比处理好，或者根据图片的宽高比反推 `width`/`height`。
**M1 先不处理，但要在返回里带一个提示字段**，免得 M2 做 App 时忘了这件事。

---

## 3. Round 划分

命名按项目约定：**M = Milestone，R = Round**，本文档是 M1，其中的轮次记作
`M1R1`、`M1R2`……

⚠️ **一个 round 不是一个步骤，也不是一条聊天消息。**
按 `AGENTS.md` §1.1 的定义，round 是**为解决一个明确问题所做的一次完整努力**：
分析 → 改代码 → 构建与测试 → 再分析，不通过就迭代。
所以下面每个 round 都有「要解决的问题」和「解决的判据」，
内部包含多少次改-测循环不限。

每个 round 结束时按 `AGENTS.md` §1.2 写总结到
`Docs/experience/YYYY-MM-DD/`，文件名带上轮次编号，例如 `M1R3_...md`。
总结里必须包含 `Docs/Validation.md` §5 要求的四项，**其中「没验证什么」不是可选项**。

| Round | 要解决的问题 | 解决的判据 | VS |
|---|---|---|---|
| M1R1 | 网关能被部署到服务器并访问到 | 从开发机 curl 到 `/v1/health` | VS-12 |
| M1R2 | workflow 拼得对不对 | 构造器的全部用例在开发机上通过 | VS-1〜4, VS-13 |
| M1R3 | ComfyUI 认不认这张图，能不能出视频 | 拿到一个画面正常的 mp4 | VS-5, VS-6, VS-7 |
| M1R4 | 首帧图能不能真的生效 | 产物首帧肉眼可辨认是上传的图 | VS-8 |
| M1R5 | 并发提交会不会互相踩 | 三个任务严格串行，失败原因带得回来 | VS-9, VS-10, VS-17, VS-18 |
| M1R6 | 手机能不能拖进度条 | Range 请求返回 206 且 `Content-Range` 正确 | VS-11 |
| M1R7 | 整条链路是否可重复验证 | 一条命令跑完全程 | 全部复跑 |

VS 编号的定义见 `Docs/Validation.md` §3，层级定义见其 §2。

---

### M1R1 — 网关骨架，以及部署链路的首次实跑

**问题**：现在没有任何服务端代码，部署脚本的执行部分也从未被验证过。

建 `server/`：

```text
server/
├── requirements.txt
├── app/
│   ├── __init__.py
│   ├── main.py            FastAPI 实例 + /v1/health
│   ├── core/config.py     配置（见 §4）
│   └── comfy/__init__.py
└── tests/
```

`requirements.txt` 先只放必需的：

```text
fastapi
uvicorn[standard]
httpx
websockets
pydantic
pydantic-settings
pytest
pytest-asyncio
```

`/v1/health` 返回网关自己的状态 + ComfyUI 是否可达。

**判据**：

1. 开发机本地 `uvicorn app.main:app --port 7869`，`curl localhost:7869/v1/health` 有响应
2. 提交推送后跑 `scripts/deploy_server.sh`
3. `curl --noproxy '*' http://icewalnut-1060.tail22a711.ts.net:7869/v1/health` 有响应

⚠️ **第 2 步是部署脚本的首次实跑。** 脚本的后半段（装依赖、起 uvicorn、
确认端口监听）到现在为止**从未被验证过**，本轮就是它的验收。
脚本报错先修脚本，**别绕过去手工起服务** —— 手工起来一次，这个脚本就永远是坏的。

---

### M1R2 — workflow 构造器（**M1 里最该写测试的一轮**）

**问题**：拼错 workflow 是这个项目的主要风险，而拼错之后 ComfyUI 的报错
未必指向真正的原因。这一轮要把「拼得对不对」变成开发机上就能回答的问题。

`comfy/workflows/h3_video.py`，纯函数：输入一个请求对象，输出 workflow dict。
**不碰网络，不读文件。**

要实现的三件事：

1. **节点图拼装** —— 架构文档 §2.1 那张图
2. **帧数换算** —— `seconds_to_length()`，17k+5 格子，架构文档 §2.2
3. **prompt 拼装** —— 对齐指令行 + 三段结构，架构文档 §2.3

**判据**（全部在开发机上跑，不需要 GPU，也不需要连服务器）：

| 测什么 | 断言 |
|---|---|
| 帧数换算边界 | `1s→22`、`5s→124`、`5.17s→124`、`5.2s→141`、`15s→362` |
| T2VA | 生成的图里 `MiniMaxH3ImageToVideo` **没有** `first_frame` / `last_frame` |
| I2VA | 有 `first_frame`，没有 `last_frame`，prompt 第一行是单帧对齐句 |
| FL2VA | 两个 frame 都有，prompt 第一行是双帧对齐句，且句中秒数是**换算后**的实际时长 |
| 三段 prompt | 空的段落不应留下孤零零的 `overall_soundscape:` 空标签 |
| 分辨率 | 非 32 倍数的输入被**向上取整到 32 的倍数**，并在响应里报告调整后的值 |

⚠️ **对齐指令行的秒数必须用换算后的时长。** 用户填 5 秒、实际 124 帧 = 5.17 秒，
对齐句里要写 `5.17`，不是 `5.00`。写错了模型会把尾帧对到错误的时间点，
而这种错**不会报错**，只会让结果变差——所以只能靠测试守住。

---

### M1R3 — ComfyUI 客户端，以及第一次真实生成

**问题**：上一轮证明的是「我们自己认为图是对的」，这一轮要回答
「ComfyUI 认不认，跑出来的是不是正常画面」。

先写客户端：按架构文档 §3 写 `comfy/client.py`（Protocol）、
`http_client.py`、`fake_client.py`。M1 只需要
`submit` / `history` / `system_stats` / `upload_image`；
`interrupt` / `free` / `events` 先抛 `NotImplementedError`，M3、M4 再填。

再跑第一次生成：**T2VA，不带图，代价最小**。
`736x416`、`length=124`（约 5.17 秒）、`steps=8`，
配 Turbo LoRA（`minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors`）。

**先手工提交一次，再写进代码**：把 M1R2 生成的 workflow JSON
直接 `curl -X POST /prompt` 丢给 ComfyUI，看它认不认。

**判据**：

1. 针对 `fake_client` 的单元测试在开发机上全绿
2. `/prompt` 返回 `prompt_id` 且 `node_errors` 为空
   —— 不为空说明图拼错了，报错里有具体是哪个节点的哪个字段
3. 轮询 `/history/{prompt_id}` 直到 `status_str == "success"`
4. 服务器上 `ffprobe` 产物，断言 `h264` + `aac` + `mp4`
5. **实际看一眼这个视频**，确认不是黑屏或噪声

⚠️ **第 5 步不能省。** 前四步全过但输出是一片黑，说明采样参数或 VAE 接线有问题，
而前四步一个都查不出来。

⚠️ **这一轮会真的占用 GPU 几分钟**，模型加载是分钟级的固定开销（runbook §6.6）。
跑之前先 `curl /queue` 确认没有别的任务在跑。

---

### M1R4 — 上传，以及首帧真的生效

**问题**：I2VA 的价值全在首帧上。文件格式对、任务成功，都不能证明首帧生效了。

`POST /v1/uploads/image`：接 multipart，转存到 ComfyUI 的 `input/`，返回 `asset_id`。

两条路可选：调 ComfyUI 的 `POST /upload/image`，或直接写文件系统（同机同用户）。
**M1 用 ComfyUI 的 `/upload/image`** —— 让所有 ComfyUI 交互都收在
`comfy/http_client.py` 一处，符合架构文档 §3 的分层约束。
以后嫌慢再换成直接写盘，那时只改 `http_client.py` 一个文件。

**判据**：上传一张图 → 提交 I2VA → 产物的第一帧**肉眼可辨认是那张图**。

⚠️ 这一条只能靠看。`ffprobe` 能证明文件格式对，证明不了首帧生效了；
**首帧参数传错时，任务会正常成功，只是变成了一个 T2VA**。

---

### M1R5 — 任务状态机与串行队列

**问题**：显存只够一个重任务。两个任务同时提交时，必须有人拦住第二个。

`core/jobs.py`：架构文档 §4 的状态机，加一个**容量为 1 的串行执行**。

M1 的简化：状态存在内存里，网关重启就丢。持久化放到 M3 和媒体库索引一起做。
**但状态机的形状现在就要定对**，后面只是换存储。

⭐ **范围调整（本轮执行时决定）**：任务的四个对外路由
（`POST /v1/jobs`、`GET /v1/jobs/{id}`、`GET /v1/jobs`、`POST /v1/jobs/{id}/cancel`）
**从 M1R6 提前到本轮**，因为状态机没有入口就只能靠单元测试看，
而契约里那几个字段的对齐本来就要跟着状态机一起定。
M1R6 相应缩成「取回产物 + Range」。

⚠️ **归一化只能做一次。** `normalize()` 在用户没填 seed 时会随机生成一个，
而 `build_workflow()` 内部会调 `normalize()`。
如果提交时算一次回给 App、执行时再算一次喂给 ComfyUI，
**两个 seed 不一样，而且不会报错** —— 用户拿回报的 seed 复现不出那个视频。
所以探测首帧尺寸与建图都在 `POST /v1/jobs` 的请求路径里做完，
结果存在 Job 上，worker 只做预检与提交。

**判据**：

- 连续提交 3 个任务，断言**任何时刻只有一个处于 `submitted`/`running`**
- 用 `fake_client` 模拟 ComfyUI 返回校验错误，断言任务进入 `failed`
  且 `failure_reason` 里**带着 `node_errors` 的原文**
- 取消的三种情形各自的终态正确（VS-17）
- 回报的 `normalized.seed` 与 workflow 里实际用的 seed 相同（VS-18）

---

### M1R6 — 取回产物与 Range

**问题**：确认视频能被分段取回。

⚠️ **提交、状态、列表、取消四个路由已经在 M1R5 做掉了**，本轮只剩：

```text
GET  /v1/jobs/{id}/video 取回产物，支持 Range
```

M1 先不做完整媒体库，`/jobs/{id}/video` 直接按 `/history` 里记录的
文件名去输出目录取。

**判据**：

- `curl -r 0-1023` 请求前 1KB，断言返回 `206 Partial Content`
  且 `Content-Range` 头正确

⚠️ **Range 必须真测。** 它是手机上能不能拖进度条的唯一依赖，
不能因为「框架应该支持」就跳过。

---

### M1R7 — 端到端冒烟

**问题**：前面每轮各自验过，但没有一条可重复的全程验证。

一条命令走完：上传图 → 提交 I2VA → 轮询到完成 → 下载 mp4 → 本地 `ffprobe`。

写成 `scripts/smoke_m1.sh`，放进仓库。**以后每次改网关都跑一遍这个。**

---

## 4. 配置

`server/app/core/config.py`，用环境变量覆盖，默认值写在代码里：

```python
COMFY_BASE_URL   = "http://127.0.0.1:8188"     # 网关与 ComfyUI 同机
COMFY_OUTPUT_DIR = "~/workspace/ComfyUI/output"
GATEWAY_PORT     = 7869

H3_UNET       = "h3ErosMax_beta5_fp8.safetensors"
H3_CLIP       = "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
H3_VIDEO_VAE  = "minimax_h3_video_vae_fp16.safetensors"
H3_AUDIO_VAE  = "minimax_h3_audio_vae_fp32.safetensors"
H3_TURBO_LORA = "minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors"
```

⚠️ **video VAE 和 audio VAE 写死，不做成可配置项。** 需求文档 §8.4：
这两个接反过，错误信息是 `MiniMax H3 VAE MISMATCH`。能配置就能配错。

⚠️ **启动时用 `/object_info` 自检一遍这些文件名存在**，
对不上就在启动日志里明确报出来。模型文件被改名或移动时，
让它在启动时就失败，比等到第一次生成跑了五分钟才报错要好。

---

## 5. SD 模型归位（已完成）

需求文档 §10.4 的遗留项，本轮已处理。**方式是目录级软链接**：

```bash
SD=~/stable-diffusion/stable-diffusion-webui/models
CU=~/workspace/ComfyUI/models
ln -sfn $SD/Stable-diffusion $CU/checkpoints/sdwebui
ln -sfn $SD/Lora             $CU/loras/sdwebui
ln -sfn $SD/LyCORIS          $CU/loras/sdwebui-lycoris
```

**为什么链目录而不是链每个文件**：一条链接搞定，以后往 SD WebUI 里加模型
会自动出现在 ComfyUI 里，不会漂。

**已验证 ComfyUI 确实跟随软链接**（这是会安静失败的那类事，所以实测了）：
`GET /object_info/CheckpointLoaderSimple` 返回

```text
sdwebui/oneObsession_v22.safetensors
sdwebui/pornmasterPro_noobV15VAE.safetensors
sdwebui/unholyDesireMixSinister_v40.safetensors
sdwebui/v1-5-pruned-emaonly.safetensors
```

LoRA 从 4 个增加到 39 个。**不需要重启 ComfyUI**，它会重新扫描目录。

⚠️ SD 本身的接入是 M4，这里只是把模型准备好。
M4 开始前还要确认一件事：这 4 个 checkpoint 里 `v1-5-pruned-emaonly` 是 SD 1.5，
其余三个看文件大小（6.5–6.7 GB）像是 SDXL 系。
**两代的 workflow 不一样**，M4 要么只支持一代，要么按模型类型分别拼图。

---

## 6. 风险与未知

| 风险 | 影响 | 处置 |
|---|---|---|
| **首次真实生成可能显存不足** | S4 卡住 | 先用最小配置：`736x416`、`length=124`、`steps=8`。不够再降 |
| **Turbo LoRA 可能与主模型不兼容** | 报 shape mismatch | 现成的退路：关掉 LoRA，`steps` 提到 16–25。需求 §8.2 记过这个 |
| **纯 T2VA 没有对齐指令行，prompt 结构可能不同** | S4 出黑屏或乱结果 | S4 就是为了先验证这条最简单的路。出问题先对照 Director 生成的 prompt |
| **网关起来后 ComfyUI 仍监听 0.0.0.0** | 两个入口并存 | M1 不改。等 M2 App 打通后再把 ComfyUI 收回 `127.0.0.1` |
| **状态只在内存里，网关一重启任务就丢** | 重启期间的任务查不到 | M1 接受。M3 做持久化 |

---

## 7. 每轮收尾

判据见 §3 的表，不在这里重复一遍。每个 round 结束时：

1. 写总结到 `Docs/experience/YYYY-MM-DD/M1R<n>_*.md`，
   内容按 `Docs/Validation.md` §5 的四项 —— **「没验证什么」不是可选项**
2. 更新 `Docs/Validation.md` §3 里那几行的状态
3. 同步 `Docs/TODO.md` 与 `Docs/ContextPack.md`
4. 本轮未受影响的文档，**显式确认它仍然准确**再决定不改
