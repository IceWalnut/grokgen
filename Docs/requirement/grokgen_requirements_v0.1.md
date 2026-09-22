# grokgen v0.1 需求文档

**状态**：初稿 · 2026-09-22
**来源**：`Docs/knowledge/chat_dialogue.json`（2026-09-21～22 与 Codex 在服务器上的对话记录）中用户提出的需求，
加上 2026-09-22 在两台机器上实测到的环境事实。
**环境事实不在本文档展开**，看 `Docs/runbooks/home_gpu_server.md`。

---

## 1. 一句话目标

做一个 **Android App，当作家里那台 GPU 机器的远程生成控制台**：
在手机上提交 MiniMax H3 视频生成任务和 Stable Diffusion 生图任务，看进度，
浏览和播放机器上已经生成的图片与视频。手机和机器之间走 Tailscale 私有网络。

## 2. 为什么要做

现在的用法是：坐到电脑前，打开浏览器里的 ComfyUI 节点图，手动改参数，排队，等结果，
在 Windows 的文件夹里翻输出。

三个具体不便：

1. **必须在电脑前**。任务动辄十几分钟，人却被绑在机器旁边。
2. **ComfyUI 的节点图不适合手机浏览器**。节点图是为鼠标和大屏做的，手机上点不准、看不全。
3. **输出结果散在服务器的目录里**，没有缩略图、没有分类、手机上看不了。

## 3. 范围

### 3.1 v0.1 做什么

- Android 客户端，通过 Tailscale 访问后端
- 一个**新写的后端网关**，跑在服务器的 WSL 里，是 App 唯一的入口
- MiniMax H3 视频生成：T2VA（纯文本）、I2VA（给首帧图）、FL2VA（给首帧+尾帧）
- Stable Diffusion 生图：文生图，可选图生图
- 任务队列：当前任务、等待队列、取消、重跑、复制参数
- 媒体库：图片网格、视频网格、缩略图、预览、在线播放、下载到手机
- 图片上传：相册选图、拍照、多图
- 服务与显存管理：查看当前载入的是哪个模型服务，手动或自动切换

### 3.2 v0.1 明确不做

| 不做什么 | 为什么 |
|---|---|
| 手机上的 ComfyUI 节点图编辑器 | 节点图是桌面交互，搬到手机上只会更难用。App 要像一个任务表单，不像一个 IDE |
| REF2VA（多参考图生成） | 当前主模型 `h3ErosMax_beta5_fp8` 更适合 T2V/I2V/FL2VA；真正的 REF2VA 需要另外下载对应模型。详见 §8.3 |
| LoRA 自由挑选 | 先把主链路跑通。v0.1 只用固定的 Turbo LoRA |
| 多用户、账号体系 | 只有用户一个人用，整个 Tailnet 就是信任边界 |
| 把生成结果自动同步到手机 | 视频文件大，默认在线看，用户点「保存」才下载。详见 F5 |
| 公网访问 | 只走 Tailscale。见 §7 |

## 4. 架构决定：App 不直接连 ComfyUI

```text
Android App
   │  Tailscale 私网（MagicDNS 名，不是 IP）
   ▼
后端网关（新写，跑在 WSL 里）
   ├─→ ComfyUI   127.0.0.1:8188   ← MiniMax H3 与 SD 都走这里（见 §4.2）
   ├─→ 输出目录 / 媒体库索引 / ffmpeg 转码
   └─→ GPU 显存与进程管理
```

### 4.1 网关到底做什么

网关不是「转发一下请求」的薄代理。**App 少不了的那些东西，ComfyUI 一个都不提供**，
这些活必须有人干，而干活的地方只能在服务器上：

| 网关的职责 | 为什么 ComfyUI 干不了 |
|---|---|
| **把表单变成 workflow** —— App 传的是「模式 + prompt + 图 + 时长」，网关把它填进 workflow JSON 再提交 | ComfyUI 的 API 只收完整的节点图。让手机去拼一张几十个节点的 JSON，等于把整个 workflow 的知识搬进 App，换个节点就得发版 |
| **显存与服务切换** —— 维护 GPU 状态机，切换前卸载、轮询显存、必要时重启进程 | ComfyUI 不知道自己该在什么时候让位。这是跨服务的调度，见 F7 |
| **任务队列与去重** —— 保证同一时间只有一个重 GPU 任务 | ComfyUI 的队列只管它自己收到的任务，不管显存够不够 |
| **媒体库索引** —— 扫描输出目录，建缩略图、分页、按来源分类、记录来源任务 | ComfyUI 的 history 只有任务记录，没有可浏览的媒体库 |
| **转码** —— 把 AV1/WebM 转成手机能播的 H.264 MP4（§6） | ComfyUI 的职责到出文件为止 |
| **按 Range 分段传视频** —— 让手机能拖进度条 | ComfyUI 的静态文件服务不是为流媒体设计的 |
| **收敛外部入口** —— 只有网关对 Tailscale 暴露，ComfyUI 收回到 `127.0.0.1` | — |

一句话：**网关是 App 和 ComfyUI 之间的翻译层加调度层**。
它存在的最大价值是——以后换 workflow、换模型、换自定义节点，**Android 端不用发版**。

### 4.2 SD 也走 ComfyUI（已定）

**不用 SD WebUI 的 API，在 ComfyUI 里跑 SD workflow。**

理由：只需要管一个进程。SD WebUI 是另一个 venv、另一个端口、另一套 API 和另一份显存占用；
两个进程都常驻，16 GB 显存的切换逻辑要处理「两个都在、都得卸」的组合。
走 ComfyUI 之后，切换退化成「同一个进程里换一套 workflow 和一批模型」，
ComfyUI 自己的模型缓存和 offload 机制就能覆盖大部分情况，网关只在它不够用时才介入重启。

⚠️ 代价要认：**SD 的模型得放进 ComfyUI 的 `models/checkpoints/`**。
**已于 2026-09-22 用目录级软链接解决并验证**，做法与验证见
`server/Docs/implementation/M1_gateway_mainline.md` §5。

`~/stable-diffusion/stable-diffusion-webui/` 保留不动，当作备用与对照，只是不进 v0.1 的链路。

## 5. 功能需求

### F1 连接与服务状态

- App 首次启动时配置后端地址，默认填 MagicDNS 名（形如 `icewalnut-1060.tail22a711.ts.net`），**不要写死 IP**
- 服务页显示：
  - 后端是否可达
  - GPU 型号、总显存、当前已用显存
  - 当前载入的是哪个服务（MiniMax H3 / SD / 空闲）
  - 手动按钮：启动 MiniMax、启动 SD、释放显存、查看日志摘要
- 连不上时要明确区分三种情况并分别提示：**手机没连 Tailscale** / **服务器离线** / **网关进程没起来**。
  不要统一显示成「网络错误」

### F2 MiniMax H3 视频生成页

- 模式选择：T2VA（不给图）/ I2VA（一张首帧图）/ FL2VA（首帧 + 尾帧）
  - 实现上 I2VA 和 FL2VA 走同一条路由，区别是给几张图。UI 上仍按三种模式呈现，因为用户心里是三件事
- Prompt 区按现有 Director 的三段结构：
  - `integrated_multimodal_description` —— 画面描述
  - `overall_soundscape` —— 环境声
  - `non_diegetic_music` —— 背景音乐
  - UI 上用三个带说明的输入框，不要直接把这三个英文键名甩给用户
- 首帧图 / 尾帧图上传槽
- 参数：分辨率、时长、steps、seed、比例
- 高级参数（sampler、scheduler、shift）默认折叠，默认值见 §8.2
- 提交按钮
- 实时状态，至少要能区分这几段，因为它们的耗时量级完全不同：
  排队 → **加载模型**（分钟级）→ 采样 → VAE 解码 → 编码视频

⚠️ **对齐指令行不要让用户手写。** Director 要求给了首/尾帧时，全局 prompt 的第一行必须是一条
描述「第几张图对应第几秒」的对齐句。这由网关或 Director 自动生成。

### F3 Stable Diffusion 生图页

- Prompt / Negative prompt
- 尺寸、steps、CFG、seed、sampler
- 模型选择
- 出图数量
- 图生图：上传参考图（可选）
- LoRA：v0.1 不做，或只做一个简单下拉

### F4 队列页

- 当前正在跑的任务及进度
- 等待队列
- 取消任务
- 重跑
- 复制参数到新任务
- 日志摘要（不是整个 ComfyUI 日志，是这个任务相关的那几行）

### F5 媒体库页

- 图片网格 / 视频网格
- 按来源过滤：SD 输出、MiniMax 输出、上传的素材、收藏
- 图片：列表用缩略图 → 点开中等尺寸预览 → 需要时看原图
- 视频：列表用封面帧 → 点开播放
- 下载到手机（显式动作）
- 分享到其他 App

**默认在线看服务器上的文件，不自动下载。** 理由：MiniMax 输出的视频很大，
全量同步到手机不划算；Tailscale 内网带宽够用；缩略图和分页由服务器做，手机只拿它要看的那一份。

后端需要维护索引，至少包含：文件路径、类型、生成时间、来源任务、缩略图路径、时长/尺寸。

### F6 上传

- 相册选图、拍照、多图
- 上传前裁剪、压缩、限制尺寸
- **上传与生成分离**：先上传拿到一个 asset id，生成任务里只带 asset id，不带大文件

```json
{ "asset_id": "img_20260922_xxx", "path": "input/mobile/img_20260922_xxx.png" }
```

对 MiniMax H3 来说，首帧图的落地位置就是 ComfyUI 的 `input/` 目录，
再由网关把它塞进 workflow。

### F7 显存与服务切换（**这一项是整个项目最核心的约束**）

GPU 是 RTX 4080 SUPER，**16376 MiB 显存**（实测）。MiniMax H3 这一套里，
主模型 14 GB、text encoder 15 GB —— 单是这两个就已经超过显存总量，
靠 ComfyUI 分批加载和 offload 才跑得起来。**再让 SD 同时常驻是不可能的。**

因此网关必须维护一个显式的 GPU 状态机：

```text
idle
minimax_h3_loaded
sd_loaded
switching_to_minimax
switching_to_sd
busy
```

规则：

1. **同一时间只允许一个重 GPU 任务在跑**，由网关强制，不靠调用方自觉
2. 切换前先停掉当前服务：先尝试 ComfyUI 的 unload，不干净就直接杀进程
3. **轮询显存，等它降到安全阈值再启动另一个**，不要固定 sleep
   （架构文档后来定了用 ComfyUI 的 `/system_stats` 读显存，不用 `nvidia-smi`）
4. App 上显示「正在切换模型服务」并给出预计耗时

⚠️ **对 16 GB 显存这个规模，重启服务往往比复杂的热切换更可靠。**
进程可以都开着，但任务开始前主动 unload；unload 不干净就重启那个进程。

⚠️ **模型加载是一笔与生成量无关的固定开销**，每起一次进程付一次。
所以：**不要为每个任务重启服务**，同一个服务下的连续任务要合并着跑。

## 6. 播放与传输

- 播放器用 Android 原生 **Media3 / ExoPlayer**
- 后端提供普通 HTTP 视频流，**必须支持 `Range` 请求**，否则进度条拖不动
- 对外统一成 **H.264 + AAC + MP4**，由后端转码

> ⚠️ **本节的结论已被 2026-09-22 的后续实测推翻。**
> 下面「转码必须做」的判断成立于「只能用现有 DaSiWa workflow」这个前提；
> 架构文档定了网关自己拼 workflow 之后，**ComfyUI 原生就能直接输出 H.264 MP4，
> 不需要转码**。见 `server/Docs/architecture/gateway_architecture_v0.1.md` §2.4。
> 本节保留，因为它记录了老输出文件的真实格式——那些文件仍然需要兜底转码。

### 6.1 实测：现有 workflow 的输出不是 MP4

2026-09-22 用 `ffprobe` 查了服务器上两批输出，**两条链路格式完全不同**：

| 来源 | 容器 | 视频编码 | 音频编码 |
|---|---|---|---|
| **DaSiWa workflow**（当前在用）`output/video/2026-09-22/*_audio.webm` | WebM | **AV1** | **Opus** |
| 官方 H3 模板（9 月 19 日跑的）`MiniMax_H3_0000*.mp4` | MP4 | H.264 High | AAC |

⇒ **你现在实际在用的那条链路输出的是 AV1 + Opus 的 WebM。**

AV1 在 Android 上的处境：Android 10 以后要求支持 AV1 解码，但**硬件解码要看具体芯片**，
中低端和老机器多半是软解——软解 AV1 很吃电、容易掉帧。Opus 在 WebM 里兼容性也不如 AAC。
**所以不能指望「手机可能也能播」，转码这一层是必需的，不是优化项。**

### 6.2 转码成本实测：可以忽略

在服务器上把一段 7.3 秒、512×800、3.15 Mbps 的 AV1/WebM 转成 H.264 + AAC 的 MP4：

```bash
ffmpeg -i in.webm -c:v libx264 -preset veryfast -crf 20 -pix_fmt yuv420p \
       -c:a aac -b:a 128k -movflags +faststart out.mp4
```

**实际耗时 0.38 秒**，输出 1.9 MB。相比之下一次视频生成是分钟级的。
⇒ **转码放在生成任务结束后同步做即可**，不需要队列、不需要懒加载、不值得做缓存策略。

两个要点：

1. **`-movflags +faststart` 不能省。** 它把索引挪到文件开头，
   否则手机要先下完整个文件才能起播。
2. **用 CPU 的 `libx264`，不要用 `h264_nvenc`。** 服务器上 NVENC 可用，
   但这台机器的瓶颈是显存而不是 CPU——转码占 0.38 秒 CPU 完全无所谓，
   而去碰 GPU 反而可能和正在跑的生成任务抢资源。

**原始文件保留**，转码产物另存。将来手机端硬解 AV1 普及了，可以直接切回原文件。

### 6.3 接口形状

接口契约**已单独成文**：`Docs/contract/gateway_api_v0.1.md`。
那里是 App 与网关之间唯一的约定，本文档不重复列举 endpoint。

## 7. 非功能需求

| 项 | 要求 |
|---|---|
| 网络 | 只经 Tailscale。**不做公网端口映射、不做内网穿透** |
| 认证 | v0.1 不做账号。Tailnet 成员即可访问。网关**只监听 Tailscale 网卡或 `0.0.0.0`**，不要顺手开到公网 |
| 任务持久性 | 任务跑在服务器上，App 退后台、切网、重启都不影响任务；重连后能看到进度 |
| 失败可见 | 模型切换失败、显存没释放、ComfyUI 进程死掉，都要作为明确状态返回。**不允许用超时或空结果掩盖** |
| 后端存活 | 网关是常驻服务，它活着的前提是 WSL 发行版没被回收 —— 那个坑见 runbook §4 |
| 真机验证 | 播放、上传、相册这三块只能在真机上验证 |

## 8. 已知事实与约束（来自实测和已有部署）

### 8.1 后端跑在哪

ComfyUI 装在服务器 WSL 的 `~/workspace/ComfyUI`，启动脚本和日志在 `~/workspace/minimax_h3`，
启动后监听 `0.0.0.0:8188`（2026-09-22 实测在跑，占用约 13.5 GB 显存）。
起停命令见 runbook §5.1。

SD WebUI 在 `~/stable-diffusion/stable-diffusion-webui`（独立 venv），
**v0.1 不接它** —— SD 走 ComfyUI，理由见 §4.2。

生成结果落在 `~/workspace/ComfyUI/output/video/YYYY-MM-DD/` 下，按日期分目录。
媒体库索引要扫这里。

### 8.2 MiniMax H3 的推荐参数（作为 App 的默认值）

这组值是上一轮对着实际部署核对出来的，**App 的默认参数应当照这个填**：

```text
sampler_name: res_multistep     # 不是 euler；这个 workflow 的文档推荐 res_multistep
scheduler:    simple
steps:        8–12 起步，质量不够再往 16–25 加
shift_video:  11 或 12          # 官方节点默认 12
shift_audio:  3                 # 就是默认值
```

首次测试的安全分辨率与时长：`768x432`，3 秒。

> ⚠️ **`768x432` 只对官方模板那条路有效。**
> 网关直接调原生节点时，`width` / `height` 必须是 **32 的倍数**，
> 而 `432 / 32 = 13.5`。走网关时用 `736x416`。
> 依据与说明见 `server/Docs/implementation/M1_gateway_mainline.md` §2.1。

两个超分开关（`RTX Upscaler & Refiner`、`Latent Upscaler`）**默认都关**，
而且**不要同时开** —— 两个都很吃显存和时间。想要质量就只开 Latent，想要速度就只开 RTX。

`MiniMax H3 Cache` 默认关：它能加速，但可能影响运动连贯性、音频和参考图一致性。

### 8.3 REF2VA 为什么排除在 v0.1 之外

当前主模型是 `h3ErosMax_beta5_fp8.safetensors`，它适合 T2V/I2V/FL2VA。
真正的 reference-to-video 需要对应的 Ref2VA 模型；拿 FL2VA 模型顶替可能报错或结果不对。
要支持 REF2VA，得先下载对应模型 —— 那是后续版本的事。

### 8.4 一个已经踩过的坑要在网关里防住

Video VAE 和 Audio VAE 接反过，报错是 `MiniMax H3 VAE MISMATCH`。
网关构造 workflow 时这两个槽位写死正确的文件名，不要做成可配置项。

### 8.5 客户端环境

开发机上 Android SDK、JDK 17、adb 都已就绪（具体路径见 `AGENTS.md` §4）。
**手机需要装 Tailscale 并登录同一个账号** —— 这是 App 能用的前提。

## 9. 里程碑与验收

| 里程碑 | 内容 | 验收标准 |
|---|---|---|
| **M1 后端主链路** | 网关能提交一个 MiniMax H3 I2VA 任务、报告状态、取回视频 | 用 `curl` 从开发机提交一次，拿到能播的 mp4 |
| **M2 Android 原型** | 连接、上传图片、填 prompt、提交、播放结果 | 在真机上完整走一遍 I2VA，不碰电脑 |
| **M3 媒体库** | 扫描输出目录，缩略图，网格，在线播放 | 手机上能翻到历史输出并播放，首屏不超过明显卡顿 |
| **M4 接入 SD** | SD 生图 + 服务切换 + 显存释放 | 在 App 里从 MiniMax 切到 SD 再切回来，两次都能出结果，中途显存确实降下去过 |
| **M5 高级功能** | LoRA、REF2VA、多参考、音频上传、收藏、批量下载 | 逐项定义 |

## 10. 已定与待定

### 10.1 已定（2026-09-22）

| 问题 | 结论 | 依据 |
|---|---|---|
| SD 走哪条路 | **在 ComfyUI 里跑 SD workflow**，不用 SD WebUI 的 API | §4.2。少管一个进程，显存切换简单得多 |
| 输出是什么编码 | **AV1 + Opus 的 WebM**（当前 DaSiWa 链路），必须转码 | §6.1，`ffprobe` 实测 |
| 转码放哪一步 | 生成结束后同步转成 H.264 + AAC MP4，CPU 编码，原文件保留 | §6.2，实测 0.38 秒 |
| 网关用什么写 | **FastAPI** | 见 §10.2 |
| 网关代码放哪 | **前后端同一个仓库 `IceWalnut/grokgen`（private）**，服务器上 `git pull` 部署 | 见 §10.3 |

### 10.2 网关语言：FastAPI（已定）

**性能不是考虑因素** —— 只有一个用户，两边都远远够用。真正的区别在别处：

**FastAPI（Python）的好处**，也是主要理由：
网关最容易出错的活是**构造 ComfyUI 的 workflow JSON**，尤其是 DaSiWa Director 那套
模式路由、对齐指令行、VAE 槽位的语义。用 Python 就可以直接读、甚至直接 import
ComfyUI 和自定义节点的代码来确认一个字段到底叫什么、取值范围是什么，
也能复用 ComfyUI 社区现成的 API 调用示例。**换成 Go，这部分只能靠人肉对着 JSON 猜。**
另外 ComfyUI 的进度是 WebSocket 事件，Python 这边有现成的客户端写法。

**Go 的好处**：编出来是单个二进制，没有 venv 漂移的问题，常驻更省心；
`net/http` 的 `ServeContent` 自带 Range 支持，不用自己处理分段。

**为什么还是选 FastAPI**：Go 的两个好处都是部署期的便利，而部署只在一台固定机器上做一次；
FastAPI 的好处是开发期的——**它直接降低把 workflow 拼错的概率，而这正是这个项目的主要风险。**
Range 支持用 Starlette 也能做，只是要自己确认一遍，不是障碍。

⚠️ 一个要注意的点：**网关不要和 ComfyUI 共用同一个 venv**。
ComfyUI 的依赖很重且会随更新变动，网关单独建一个 venv，避免互相牵连。

### 10.3 代码放哪、怎么同步（已定）

**前后端放同一个仓库**：`git@github.com:IceWalnut/grokgen.git`（private）。

```text
grokgen/
├── app/                      Android 客户端
├── server/                   FastAPI 网关
├── scripts/deploy_server.sh  部署脚本
└── Docs/
```

**为什么不拆成两个仓库**：App 和网关之间有一份共同的 API 契约，
改一个接口必然要同时改两边。放一个仓库里，**一次改动就是一个 commit**，
不会出现「App 已经发了、网关还没部署」这种对不上的状态。
只有一个人开发、两边一起演进、文档也只有一份——拆开没有任何收益。
将来真要拆（比如网关开源），按目录切出去也不难。

**同步方式：服务器自己 `git pull`。**

```text
开发机（唯一真相源）──push──> GitHub ──pull──> 服务器 ~/workspace/grokgen
```

服务器上那份是 `origin/main` 的**只读检出**，
⚠️ **不要在服务器上改代码** —— 部署脚本用 `git reset --hard origin/main`，改了就没了。
要调试就改开发机、提交、推送、重新部署。

**为什么不直接在服务器上写**，两个理由：

1. **改动要可复审。** 本地文件的每次修改都会渲染成 diff，而且有 git 历史；
   如果源码只在服务器上，每次改动都变成一条 ssh 上的字符串替换命令，
   只有执行的人看得见改了什么。
2. **没有 GPU 也能开发大半。** 网关里只有「调用 ComfyUI」这一小块依赖 GPU。
   把 ComfyUI 客户端放在一个接口后面，其余逻辑（任务状态机、媒体索引、
   Range 分段、转码调用）都能在开发机上跑单元测试。
   ⇒ **这不只是部署方便，它是一条设计约束**：ComfyUI 的调用必须是可替换的，
   否则本地一个测试都跑不了。

部署：`scripts/deploy_server.sh`。它做四件事——
服务器上 `git pull`、装 `server/requirements.txt`、重启 uvicorn、
**确认端口真的在监听**（只看进程起没起来不算验证）。
本地有未 push 的 commit 时脚本会直接拒绝部署，避免部署出一个跟本地不一样的版本。

**venv**：`server/.venv`，由部署脚本创建，**与 ComfyUI 的 venv 分开**。
它在 `.gitignore` 里，不进版本库。

**常驻方式**：服务器上的 WSL 里 systemd 是跑着的（PID 1 是 systemd），
但 `sudo` 需要密码、用户 linger 没开，装不了系统服务。
所以**第一版沿用 ComfyUI 已经在用的那个办法**：`setsid nohup ... &` 脱离 SSH 会话。
想升级成 systemd 服务（自动重启、开机自起）需要一次交互式的 sudo 或
`loginctl enable-linger`，那是后面的事，不挡 M1。

⚠️ 别忘了 runbook §4 那个坑：**网关活着的前提是 WSL 发行版没被回收。**

⚠️ **服务器首次要 bootstrap 一次**：在那台机器上 clone 仓库。
仓库是 private，所以**那台机器需要一把能访问 GitHub 的 SSH key**——
目前还没确认它有没有，见 §10.4。

### 10.4 仍待定

1. **进度怎么推给 App？** 转发 ComfyUI 的 WebSocket 事件，还是让 App 轮询。
   影响 App 端的实时性与耗电。
2. **任务与媒体的元数据存哪？** SQLite 还是 JSON 索引文件。
3. **缩略图什么时候生成？** 生成任务结束时顺手做（和转码同一步），还是首次访问时懒生成。
4. ~~SD 的模型怎么归位到 ComfyUI~~ **已完成（2026-09-22）**：目录级软链接，
   已验证 ComfyUI 跟随。见 `server/Docs/implementation/M1_gateway_mainline.md` §5。
5. ~~服务器有没有能访问 GitHub 的 SSH key~~ **已完成**：key 已加，
   服务器上 clone 成功，`git pull` 部署链路实跑通过。
