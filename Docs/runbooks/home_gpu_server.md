# Runbook：家用 GPU 服务器 `icewalnut`（经 Tailscale）

**这份文档回答一件事：从这台 Ubuntu 开发机，怎么用上家里那台带 4080 SUPER 的 Windows 机器。**

本文档面向**任何一个需要用这台机器的项目**。数字与命令标注了实测日期；
引用前先跑一遍 §3 末尾的冒烟测试。

---

## 1. 两台机器是什么关系

| | 开发机 | 服务器 |
|---|---|---|
| 主机名 | `zzqin-ubuntu` | `IceWalnut-1060` |
| 系统 | Ubuntu（原生 Linux） | Windows，里面跑一个 WSL2 Ubuntu 22.04 |
| 角色 | 写代码、跑构建与测试、连手机 | **只提供 GPU 算力**：ComfyUI / MiniMax H3、Stable Diffusion、TTS |
| GPU | 无 | RTX 4080 SUPER / 16376 MiB 显存 |

对 grokgen 来说这条分工是硬的：**Android 客户端在开发机上写和构建，后端网关在服务器上跑。**
开发机上没有 GPU，也没有 `ffmpeg`，所以任何要出音视频文件的验证都得在服务器上做。

**为什么走 Tailscale 而不是公网端口映射**：那台机器在用户家里，对公网完全不可见；
Tailscale 组的是一张只有用户自己设备能进的私有网络，流量加密，且家宽 IP 变了也不用配 DDNS。
把一台家用机器的端口开到互联网上，收益为零。

⭐ **手机也要加入同一个 Tailnet** —— grokgen 的 App 就是靠这个访问后端的，
没有别的入口。

---

## 2. 网络地址

Tailscale 网络（tailnet）后缀是 `tail22a711.ts.net`，当前三台设备（2026-09-22 实测）：

| 设备 | Tailscale IP | 系统 | 状态 |
|---|---|---|---|
| `zzqin-ubuntu`（本机） | `100.122.71.73` | Linux | — |
| `icewalnut-1060`（服务器） | **`100.64.3.34`** | Windows | 在线 |
| `garena-mac` | `100.119.94.24` | macOS | 离线 |

**MagicDNS 可用**：`icewalnut-1060.tail22a711.ts.net` 能解析到 `100.64.3.34`。
⭐ **代码和配置里优先写这个域名而不是 IP** —— Tailscale IP 在重装设备后会变，域名不会。

查看当前状态：

```bash
tailscale status            # 谁在线、是直连还是走中继
tailscale ip -4             # 本机的 Tailscale IP
```

这两条命令本身就是第一道排障：**如果 `tailscale status` 里 `icewalnut-1060` 显示 offline，
那台机器要么关机了、要么断网了，后面所有步骤都不用试。**

---

## 3. 两个 SSH 入口，别搞混

那台机器上有**两个独立的 SSH 服务**，用端口区分：

| 别名 | 目标 | 端口 | 登录后的 shell | 用途 |
|---|---|---|---|---|
| `icewalnut` | **Windows 本体** | 22 | **PowerShell** | 管 Windows 上的东西（计划任务、文件） |
| `icewalnut-wsl` | 里面的 **WSL Ubuntu 22.04** | 2222 | bash | **GPU 工作都在这里** |

两个别名指向**同一个 IP** —— WSL 配的是镜像网络（mirrored networking），
所以它跟 Windows 本体共用一个 Tailscale 地址。

`~/.ssh/config` 里现有的配置（已经在用，新项目不用改）：

```sshconfig
Host icewalnut
    HostName 100.64.3.34
    User admin
    IdentityFile ~/.ssh/id_ed25519_icewalnut
    IdentitiesOnly yes
    ServerAliveInterval 30

Host icewalnut-wsl
    HostName 100.64.3.34
    Port 2222
    User icewalnut
    IdentityFile ~/.ssh/id_ed25519_icewalnut
    IdentitiesOnly yes
    ServerAliveInterval 30
```

**两个入口用同一把私钥** `~/.ssh/id_ed25519_icewalnut`（权限 600），**免密码**。

冒烟测试（三条都应当秒回）：

```bash
tailscale status | grep icewalnut
ssh icewalnut-wsl 'nvidia-smi --query-gpu=name,memory.total,memory.used --format=csv,noheader'
ssh icewalnut 'hostname'
```

---

## 4. ⚠️ WSL 会自己停下来 —— 以及它是怎么被按住的

**这是这台机器最容易浪费时间的一个坑。**

实测机制：**最后一个 `wsl.exe` 客户端退出后约 20–30 秒，Windows 会把整个 WSL 发行版终止掉。**
里面跑着 sshd 拦不住，systemd 也拦不住。曾实测到：WSL 启动后第 9 秒、第 21 秒 SSH 端口还通，
**第 31 秒消失**。

⚠️ 排除掉的三个错误解释（都实测否定过）：不是机器重启、不是系统睡眠、
**也不是 `.wslconfig` 里的空闲超时**（`vmIdleTimeout=-1` 管的是虚拟机、不管发行版，加了照样停）。

⚠️ **从 SSH 会话里挂一个 `sleep infinity` 无效** —— 会话一断那个进程就被杀，发行版跟着停。
**必须由 Windows 侧把它按住。**

**现在的处置**：Windows 上注册了一个登录时触发的计划任务 **`Treader-Keep-WSL-Alive`**
（名字是历史遗留，它跟具体项目无关，对所有项目都生效），动作是

```
powershell -NoProfile -WindowStyle Hidden -Command "wsl.exe -d Ubuntu-22.04 --exec /bin/sleep infinity"
```

它一直开着一个 WSL 客户端，于是发行版不会被回收。

如果哪天 `ssh icewalnut-wsl` 连不上，**先查这个任务是不是还在跑**（在 Windows 侧的任务计划程序里，
或 `ssh icewalnut 'Get-ScheduledTask -TaskName Treader-Keep-WSL-Alive'`）。

⭐ **这一条对 grokgen 尤其重要**：后端网关将来会是一个常驻服务，
它活着的前提是 WSL 发行版没被回收。

---

## 5. 服务器上现成的环境

路径都在 WSL 用户 `icewalnut` 下（2026-09-22 实测）：

| 位置 | 是什么 |
|---|---|
| `~/workspace/ComfyUI/` | **ComfyUI**，MiniMax H3 视频生成跑在这里；启动后监听 `0.0.0.0:8188` |
| `~/workspace/minimax_h3/` | MiniMax H3 的启动脚本、日志与中文使用说明（不是 ComfyUI 本体） |
| `~/stable-diffusion/stable-diffusion-webui/` | SD WebUI，独立的 venv |
| `~/workspace/voxcpm/` | VoxCPM TTS 的虚拟环境与合成脚本（跟 grokgen 无关，别误删） |
| `ffmpeg` / `ffprobe` | `/usr/bin/` 下都有 —— 后端转码可以直接用 |

⚠️ **每个项目都有自己的 venv，系统 `python3` 里什么都没有。**
跑哪个服务就用哪个服务自己的解释器，不要用 `python3`。

**8188 端口经 Tailscale 直接可达**（已实测从开发机连通）：
`http://icewalnut-1060.tail22a711.ts.net:8188`。
⚠️ 但 grokgen 的设计是 **App 不直接连 8188**，由后端网关代理 —— 原因见 `Docs/requirement/`。

### 5.1 ComfyUI / MiniMax H3 的起停

启动（前台）：

```bash
ssh icewalnut-wsl
cd ~/workspace/minimax_h3 && ./start_comfyui.sh
```

后台启动并留日志：

```bash
cd ~/workspace/minimax_h3
setsid ./start_comfyui.sh > comfyui.log 2>&1 < /dev/null &
tail -f ~/workspace/minimax_h3/comfyui.log
```

停止并释放显存：

```bash
pkill -f 'python main.py --listen 0.0.0.0'
pgrep -af 'python main.py --listen|start_comfyui|ComfyUI'   # 确认没了
nvidia-smi                                                   # 确认显存降下来
```

⚠️ **`pkill` 之后显存不一定立刻归零**，要轮询 `nvidia-smi` 等它掉下去，
再启动另一个服务。这正是 grokgen 后端「模型切换」要处理的事。

### 5.2 已装的 MiniMax H3 模型

| 槽位 | 文件 | 大小 |
|---|---|---|
| 主 diffusion model | `models/diffusion_models/h3ErosMax_beta5_fp8.safetensors` | 14 GB |
| Text encoder | `models/text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors` | 15 GB |
| Video VAE | `models/vae/minimax_h3_video_vae_fp16.safetensors` | 4.9 GB |
| Audio VAE | `models/vae/minimax_h3_audio_vae_fp32.safetensors` | 578 MB |
| Turbo LoRA | `models/loras/minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors` | — |
| 图像超分 | `models/upscale_models/2x-AnimeSharpV4_RCAN.safetensors` | 30 MB |
| Latent 超分 | `models/latent_upscale_models/minimax_h3_latent_upscaler_3d_conv_v1_bf16.safetensors` | 659 MB |

⚠️ **主模型 14 GB + text encoder 15 GB，显存只有 16 GB** —— 这些不可能同时常驻。
ComfyUI 靠分批加载与 offload 撑过去，这也是为什么一次生成的启动开销很大。

---

## 6. 已知坑（每一条都栽过）

### 6.1 Windows 那个入口的 shell 是 PowerShell

`ssh icewalnut 'ls | head'` 不会按 bash 的语义走：`|`、`&`、`<`、引号都会被 PowerShell 解析掉，
**而且经常安静地失败**（不报错，只是没做你以为它做的事）。

⇒ **复杂命令一律先 `scp` 一个脚本文件过去再执行**，不要在命令行里拼。

### 6.2 中文在 Windows 那侧显示成乱码

PowerShell 用 GBK 读 UTF-8。⇒ **传到 Windows 侧的配置文件、脚本注释不要写非 ASCII 字符。**
（WSL 那侧没有这个问题。）

### 6.3 WSL 从 Windows 继承了一个代理变量

`.wslconfig` 里 `autoProxy=true`，于是 WSL 里会有
`http_proxy=http://127.0.0.1:7890`。**那个代理不一定在跑** —— 不跑的时候，
任何要联网的库会卡十秒然后抛网络错误。

⭐ 一个真实教训：某些模型即使已经在本地缓存里，启动时**仍然会先去查一次远端仓库**。
⇒ **「模型已下载」不等于「离线能用」。**

处置：**运行时临时摘掉代理环境变量**（`unset http_proxy https_proxy all_proxy`），
**不要去改那台机器的代理配置** —— 那是用户自己在用的。

### 6.4 长任务不要挂在一条 SSH 会话上

GPU 任务动辄十几分钟，断线就白跑。⇒ 用

```bash
setsid nohup <命令> > run.log 2>&1 < /dev/null &
```

脱离会话，然后轮询日志。

### 6.5 ⚠️ 模型加载是一笔与工作量无关的固定开销

大模型加载要几分钟，**每启动一次进程就要付一次**，跟这次要生成几个结果无关。

⇒ **批量任务必须在一个进程里跑完**，不要一个任务起一次进程。
对 grokgen 而言：**服务切换很贵，App 必须把这段等待显式告诉用户**，不能假装是普通排队。

---

## 7. 不在这台服务器上的东西

容易误以为「都在那台机器上」，实际不是：

* **Android SDK / adb / 手机**都在**开发机**上，跟服务器无关。
* 如果项目要用**云端 LLM API**，那是外部服务，不需要连这台服务器。
  密钥放在各自仓库的 `local.properties` 并加进 `.gitignore`，不进版本库。

---

## 8. 新设备要接上这台机器，需要做什么

如果项目开在这台 Ubuntu 上，**什么都不用做** —— Tailscale 已装并登录、
SSH 别名与私钥已配好，直接 `ssh icewalnut-wsl` 即可。

换一台新设备时才需要：

1. 装 Tailscale 并用同一个账号登录（`qinzhao8806@`），`tailscale status` 里能看到 `icewalnut-1060`；
2. 把私钥 `~/.ssh/id_ed25519_icewalnut` 拷过去，权限设成 600；
3. 把上面 §3 那两段 `Host` 配置写进 `~/.ssh/config`；
4. 跑 §3 末尾那三条冒烟测试。

**手机端**只需要第 1 步：装 Tailscale App、登录同一个账号。
手机不需要 SSH 私钥 —— 它只访问 HTTP 网关。

**新项目的文档里不要再抄一遍这些事实，写一句指针指到本文件即可** ——
IP、端口、路径、版本号有变时，只改这一个地方。

---

## 9. 本文档的核对方式

2026-09-22 实测：`tailscale status`（各设备 IP 与在线状态）·
`ssh icewalnut-wsl` 上跑 `nvidia-smi`（GPU 型号、总显存、已用显存）、`ss -tlnp`（确认 8188 在监听）、
`ls ~/workspace`、`ls ~/workspace/ComfyUI/models/*`（模型文件与大小）·
`ssh icewalnut 'hostname'`（顺带确认对端是 PowerShell）。

⚠️ 版本号与 IP 会漂，**引用本文档的数字前先跑一遍 §3 的冒烟测试**。
