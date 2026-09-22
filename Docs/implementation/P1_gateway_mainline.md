# P1 执行文档：网关主链路

**状态**：待执行 · 2026-09-22
**上游**：`Docs/architecture/gateway_architecture_v0.1.md`
**目标**：需求文档 §9 的 P1 —— 网关能提交一个 MiniMax H3 任务、报告状态、取回视频。

---

## 1. 退出标准

P1 完成的判据只有一条，必须能实际做到：

> 在开发机上执行一条 `curl`，提交一个带首帧图的 I2VA 任务，
> 轮询到任务完成，下载回一个**能在手机上播放的 H.264 MP4**。

⚠️ 「接口返回 200」不算完成。**判据是拿到一个能播的文件。**

P1 **不做**：Android 客户端、媒体库、SD、显存切换、WebSocket 进度推送。
这些分别在 P2–P4。P1 的进度用轮询就够。

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

**P1 用 `736x416`**：两个都是 32 的倍数，而且这是服务器上已有成品
`MiniMax_H3_00002_.mp4` 的真实分辨率，**是已知能跑出来的尺寸**。

**首帧和尾帧的缩放方式不一样。** 读节点源码：

```python
img = _resize(first_frame[:1], width, height, "disabled")   # 首帧：直接拉伸
img = _resize(last_frame[:1],  width, height, "center")     # 尾帧：居中裁剪
```

⇒ **首帧图如果宽高比和目标不一致，会被拉伸变形**，不是裁剪。
网关应当在上传或提交时按目标宽高比处理好，或者根据图片的宽高比反推 `width`/`height`。
**P1 先不处理，但要在返回里带一个提示字段**，免得 P2 做 App 时忘了这件事。

---

## 3. 步骤

每一步都写了**怎么验证**。没有验证方式的步骤不算完成。

### S1 骨架跑起来，并把部署链路走通一次

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

**验证**：

1. 开发机本地 `uvicorn app.main:app --port 7869`，`curl localhost:7869/v1/health` 有响应
2. 提交推送后跑 `scripts/deploy_server.sh`
3. `curl --noproxy '*' http://icewalnut-1060.tail22a711.ts.net:7869/v1/health` 有响应

⚠️ **第 2 步是部署脚本的首次实跑。** 脚本的后半段（装依赖、起 uvicorn、
确认端口监听）到现在为止**从未被验证过**，S1 就是它的验收。
脚本报错先修脚本，别绕过去手工起服务。

### S2 ComfyUI 客户端接口

按架构文档 §3 写 `comfy/client.py`（Protocol）、`http_client.py`、`fake_client.py`。

P1 只需要这几个方法：`submit`、`history`、`system_stats`、`upload_image`。
`interrupt` / `free` / `events` 先留空实现（抛 `NotImplementedError`），P3/P4 再填。

**验证**：

- 针对 `fake_client` 的单元测试在**开发机**上全绿
- 一个标了 `@pytest.mark.integration` 的测试打真实 ComfyUI，读 `/system_stats`，
  断言 `vram_total > 0`；这个测试默认跳过，只在需要时手动跑

### S3 workflow 构造器（**这一步最需要测试**）

`comfy/workflows/h3_video.py`，纯函数：输入一个请求对象，输出 workflow dict。
**不碰网络，不读文件。**

要实现的三件事：

1. **节点图拼装** —— 架构文档 §2.1 那张图
2. **帧数换算** —— `seconds_to_length()`，17k+5 格子，架构文档 §2.2
3. **prompt 拼装** —— 对齐指令行 + 三段结构，架构文档 §2.3

**验证**（全部在开发机上跑，不需要 GPU）：

| 测什么 | 断言 |
|---|---|
| 帧数换算边界 | `1s→22`、`5s→124`、`5.17s→124`、`5.2s→141`、`15s→362` |
| T2VA | 生成的图里 `MiniMaxH3ImageToVideo` **没有** `first_frame` / `last_frame` |
| I2VA | 有 `first_frame`，没有 `last_frame`，prompt 第一行是单帧对齐句 |
| FL2VA | 两个 frame 都有，prompt 第一行是双帧对齐句，且句中秒数是**换算后**的实际时长 |
| 三段 prompt | 空的段落不应留下孤零零的 `overall_soundscape:` 空标签 |
| 分辨率 | 非 32 倍数的输入被**向上取整到 32 的倍数**，并在响应里报告调整后的值 |

⚠️ **对齐指令行的秒数必须用换算后的时长。** 用户填 5 秒、实际 124 帧 = 5.17 秒，
对齐句里要写 `5.17`，不是 `5.00`。写错了模型会把尾帧对到错误的时间点。

### S4 打通一次真实生成（T2VA，最小代价）

不带图，纯文本，`736x416`，`length=124`（约 5.17 秒），`steps=8`，
配 Turbo LoRA（`minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors`）。

**先手工跑，再写进代码**：用 S3 生成的 workflow JSON，
直接 `curl -X POST /prompt` 提交到 ComfyUI，看它认不认。

**验证**：

1. `/prompt` 返回 `prompt_id` 且 `node_errors` 为空
   —— 不为空说明图拼错了，报错里有具体是哪个节点的哪个字段
2. 轮询 `/history/{prompt_id}` 直到 `status_str == "success"`
3. 到服务器上 `ffprobe` 产物，断言 `h264` + `aac` + `mp4`
4. **实际看一眼这个视频**，确认不是黑屏或噪声

⚠️ **第 4 步不能省。** 前三步全过但输出是一片黑，说明采样参数或 VAE 接线有问题，
而这三步都查不出来。

⚠️ **这一步会真的占用 GPU 几分钟**，模型加载是分钟级的固定开销（runbook §6.6）。
跑之前先 `curl /queue` 确认没有别的任务在跑。

### S5 上传 + I2VA

`POST /v1/uploads/image`：接 multipart，转存到 ComfyUI 的 `input/`，返回 `asset_id`。

两条路可选：
- 调 ComfyUI 的 `POST /upload/image`（走 HTTP，边界干净）
- 直接写文件系统（同机同用户，更快）

**P1 用 ComfyUI 的 `/upload/image`** —— 让所有 ComfyUI 交互都收在
`comfy/http_client.py` 一处，符合架构文档 §3 的分层约束。
以后嫌慢再换成直接写盘，那时只改 `http_client.py` 一个文件。

**验证**：上传一张图 → 提交 I2VA → 产物的第一帧**肉眼可辨认是那张图**。
`ffprobe` 只能证明文件格式对，证明不了首帧生效了。

### S6 任务状态机与串行队列

`core/jobs.py`：架构文档 §4 的状态机，加一个**容量为 1 的串行执行**。

P1 的简化：状态存在内存里，网关重启就丢。持久化放到 P3 和媒体库索引一起做。
**但状态机的形状现在就要定对**，后面只是换存储。

**验证**：

- 连续提交 3 个任务，断言**任何时刻只有一个处于 `submitted`/`running`**
- 用 `fake_client` 模拟 ComfyUI 返回校验错误，断言任务进入 `failed`
  且 `failure_reason` 里**带着 `node_errors` 的原文**

### S7 对外的 API

```text
POST /v1/jobs            提交
GET  /v1/jobs/{id}       状态
GET  /v1/jobs            列表
GET  /v1/jobs/{id}/video 取回产物，支持 Range
```

P1 先不做完整媒体库，`/jobs/{id}/video` 直接按 `/history` 里记录的
文件名去输出目录取。

**验证**：

- `curl -r 0-1023` 请求前 1KB，断言返回 `206 Partial Content`
  且 `Content-Range` 头正确
- ⚠️ **Range 必须真测。** 它是手机上能不能拖进度条的唯一依赖，
  不能因为「框架应该支持」就跳过

### S8 端到端验收

一条命令走完：上传图 → 提交 I2VA → 轮询到完成 → 下载 mp4 → 本地 `ffprobe`。

写成 `scripts/smoke_p1.sh`，放进仓库。**以后每次改网关都跑一遍这个。**

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

⚠️ SD 本身的接入是 P4，这里只是把模型准备好。
P4 开始前还要确认一件事：这 4 个 checkpoint 里 `v1-5-pruned-emaonly` 是 SD 1.5，
其余三个看文件大小（6.5–6.7 GB）像是 SDXL 系。
**两代的 workflow 不一样**，P4 要么只支持一代，要么按模型类型分别拼图。

---

## 6. 风险与未知

| 风险 | 影响 | 处置 |
|---|---|---|
| **首次真实生成可能显存不足** | S4 卡住 | 先用最小配置：`736x416`、`length=124`、`steps=8`。不够再降 |
| **Turbo LoRA 可能与主模型不兼容** | 报 shape mismatch | 现成的退路：关掉 LoRA，`steps` 提到 16–25。需求 §8.2 记过这个 |
| **纯 T2VA 没有对齐指令行，prompt 结构可能不同** | S4 出黑屏或乱结果 | S4 就是为了先验证这条最简单的路。出问题先对照 Director 生成的 prompt |
| **网关起来后 ComfyUI 仍监听 0.0.0.0** | 两个入口并存 | P1 不改。等 P2 App 打通后再把 ComfyUI 收回 `127.0.0.1` |
| **状态只在内存里，网关一重启任务就丢** | 重启期间的任务查不到 | P1 接受。P3 做持久化 |

---

## 7. 步骤与验收对照表

| 步骤 | 完成的判据 |
|---|---|
| S1 | 从开发机 curl 到部署在服务器上的 `/v1/health`，且是 `deploy_server.sh` 部署的 |
| S2 | 开发机上单元测试全绿（不连服务器） |
| S3 | 开发机上 workflow 构造的全部用例通过，含三种模式与帧数边界 |
| S4 | 拿到一个 h264+aac+mp4，**且肉眼确认画面正常** |
| S5 | I2VA 产物的首帧**肉眼可辨认是上传的那张图** |
| S6 | 三个任务串行执行；模拟失败时 `failure_reason` 带 `node_errors` 原文 |
| S7 | `curl -r 0-1023` 返回 206 且 `Content-Range` 正确 |
| S8 | `scripts/smoke_p1.sh` 一条命令跑通全程 |
