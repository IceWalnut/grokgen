# grokgen

**手机端的远程生成控制台。**

Android App 做前端，家里那台带 RTX 4080 SUPER 的 Windows 机器（WSL2 Ubuntu）做后端，
两端通过 Tailscale 私有网络连接。在手机上提交 MiniMax H3 视频生成任务和
Stable Diffusion 生图任务，看进度，浏览和播放机器上已经生成的图片与视频。

App 自己不跑任何模型。模型加载、显存管理、任务队列、媒体库索引全部在后端。

---

## 仓库结构

```text
grokgen/
├── app/            Android 客户端
├── server/         FastAPI 网关
├── scripts/        部署与冒烟脚本
└── Docs/           产品级与两端共享的文档
```

前后端在同一个仓库。**它们之间有一份共同的接口契约，改一个接口必然要同时改两边** ——
放一个仓库里，一次改动就是一个 commit。

## 架构

```text
Android App
   │  Tailscale 私网
   ▼
FastAPI 网关（跑在服务器的 WSL 里）
   ├─→ ComfyUI 127.0.0.1:8188   ← MiniMax H3 与 SD 都走这里
   ├─→ 输出目录 / 媒体库索引
   └─→ GPU 显存与进程管理
```

App 不直接连 ComfyUI。网关负责把表单翻译成 workflow、管显存与任务队列、
建媒体库索引、按 Range 分段传视频 —— 这些 ComfyUI 一个都不提供。

**这样做的最大价值：以后换 workflow、换模型、换自定义节点，Android 端不用发版。**

## 两个硬约束

1. **显存 16 GB 装不下 MiniMax H3 和 Stable Diffusion 同时常驻。**
   主模型 14 GB + text encoder 15 GB 已经超过显存总量，靠分批加载和 offload 才跑得起来。
   任何设计如果默认「两个服务都在」，就是错的。
2. **同一时间只允许一个重 GPU 任务**，由网关强制，不靠调用方自觉。

## 从哪读起

| 想知道 | 看 |
|---|---|
| 怎么开始一个会话、项目有什么规矩 | `AGENTS.md` |
| 现在做到哪、下一步做什么、有什么坑 | `Docs/ContextPack.md` |
| 里程碑与任务状态 | `Docs/TODO.md` |
| 做什么、不做什么、验收标准 | `Docs/requirement/` |
| App 与网关之间的接口 | `Docs/contract/` |
| 什么算验证过 | `Docs/Validation.md` |
| 怎么连服务器、怎么起停服务 | `Docs/runbooks/home_gpu_server.md` |
| 文档为什么这么分 | `Docs/README.md` |
| 网关怎么分层、怎么拼 workflow | `server/Docs/architecture/` |

## 开发与部署

代码写在这台开发机上，**服务器只有一份只读检出**：

```text
开发机 ──push──> GitHub ──pull──> 服务器 ~/workspace/grokgen
```

```bash
scripts/deploy_server.sh        # 服务器上 git pull + 装依赖 + 重启网关
```

⚠️ **不要在服务器上改代码** —— 部署用 `git reset --hard origin/main`。

开发机没有 GPU。网关里只有「调用 ComfyUI」那一块依赖它，
**那块必须放在一个接口后面**，否则本地一个单元测试都跑不了。
