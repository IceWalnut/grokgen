# ContextPack

**这份文件是新会话的引导上下文**：现在在哪、下一步做什么、有哪些坑会咬人。

它不是里程碑归档（那是 `Docs/TODO.md`），也不是项目总览（那是根 `README.md`）。
⚠️ **只有这一份，不在 `server/Docs/` 或 `app/Docs/` 下另建** ——
两份"当前状态"一旦不一致，哪份都不能信。

最后更新：2026-09-22

---

## 1. 现在在哪

**文档阶段刚结束，代码一行还没写。**

已经定稿的：产品需求、接口契约（草案）、验证标准、网关架构、M1 执行文档。
基础设施已通：git 仓库、服务器 clone、部署脚本的拉取部分、SD 模型软链接。

**下一步是 M1R1** —— 建 `server/` 骨架，让 `/v1/health` 能从开发机访问到。

---

## 2. 这个项目一句话

Android App 当手机端的远程生成控制台，家里那台 4080 SUPER 的 Windows/WSL 机器
跑 ComfyUI 出视频和图，中间隔一个自己写的 FastAPI 网关，全程走 Tailscale。

两端在同一个仓库：`app/` 和 `server/`。

---

## 3. 本轮（M1R1）要验证的假设

**`scripts/deploy_server.sh` 的执行部分能跑通。**

这个脚本的前半段（服务器 `git fetch` + `reset --hard`）已经实跑验证过；
**后半段——建 venv、装依赖、起 uvicorn、确认端口监听——从未被执行过。**
M1R1 就是它的验收。

⚠️ **脚本报错要修脚本，不要绕过去手工起服务。**
手工起来一次，这个脚本就永远是坏的，而且没人会发现。

---

## 4. 会咬人的坑

按「多久会撞上」排。

### 4.1 开发机的代理会把 Tailscale 请求变成 502

本机 shell 里有 `http_proxy=127.0.0.1:7890`，而 `no_proxy` 里没有 tailnet 域名。
`curl http://icewalnut-1060.tail22a711.ts.net:8188/...` 会返回 **502**，
**看起来像服务器挂了，实际服务器好好的**。

⇒ 从开发机发的每一条 curl 都要带 `--noproxy '*'`。（runbook §6.4）

### 4.2 不要在服务器上改代码

服务器上 `~/workspace/grokgen` 是 `origin/main` 的只读检出，
部署脚本用 `git reset --hard`，**改了下次部署就没了**。
要调试就改本仓库 → 提交 → 推送 → 重新部署。

### 4.3 跑 GPU 验证前先看 ComfyUI 忙不忙

```bash
curl -s --noproxy '*' http://icewalnut-1060.tail22a711.ts.net:8188/queue
```

模型加载是**分钟级的固定开销**，跟这次生成多少内容无关。
排队等别人跑完，比自己插进去更省时间。

### 4.4 三个数值约束，写错了不会报错

| 约束 | 值 | 写错的后果 |
|---|---|---|
| 分辨率 | **必须是 32 的倍数** | 节点直接拒绝 |
| 帧数 | **17k+5 的格子**（5, 22, 39 … 124 …） | 会被静默取整，实际时长和用户填的不一样 |
| 对齐指令行的秒数 | 用**换算后**的实际时长（5 秒 → `5.17`） | **不会报错**，只是模型把尾帧对到错的时间点 |

⚠️ 需求文档 §8.2 里建议的 `768x432` **在网关这条路上无效**（432 不是 32 的倍数）。
M1 用 `736x416`，那是服务器上已有成品的真实分辨率。

### 4.5 首帧是拉伸，尾帧是居中裁剪

原生节点里 `first_frame` 用 `crop="disabled"`（直接拉伸），
`last_frame` 用 `crop="center"`。**首帧宽高比不匹配时会变形，不是裁剪。**

### 4.6 WSL 会自己停下来

Windows 那侧有个计划任务按住它。网关是常驻服务，
**它活着的前提是 WSL 发行版没被回收**。连不上时先查那个任务。（runbook §4）

---

## 5. 一个已经被推翻过的结论

**需求文档 §6 说"必须做转码"——那是错的，别照着实现。**

AV1 + Opus 是 DaSiWa workflow 里 `EnhancedVideoCombine` 节点选的，不是模型属性。
网关自己拼 workflow 时用 `CreateVideo(codec="h264")`，
ComfyUI **直接产出 H.264 + AAC 的 MP4**（已实跑 ffprobe 验证）。

ffmpeg 只作兜底，用于看以前生成的老 WebM 文件。

---

## 6. 开工前读什么

`AGENTS.md` §1 那份清单。M1 阶段实际要看的是这四份：

1. `Docs/TODO.md` —— 轮次状态
2. `server/Docs/implementation/M1_gateway_mainline.md` —— 本轮要做什么
3. `Docs/Validation.md` —— 判据和 VS 编号
4. `server/Docs/architecture/gateway_architecture_v0.1.md` —— 节点图和分层

写接口时再看 `Docs/contract/gateway_api_v0.1.md`。
