# ContextPack

**这份文件是新会话的引导上下文**：现在在哪、下一步做什么、有哪些坑会咬人。

它不是里程碑归档（那是 `Docs/TODO.md`），也不是项目总览（那是根 `README.md`）。
⚠️ **只有这一份，不在 `server/Docs/` 或 `app/Docs/` 下另建** ——
两份"当前状态"一旦不一致，哪份都不能信。

最后更新：2026-09-23（M1R6 + M1R7 完成后）

---

## 1. 现在在哪

**M1 的网关侧完成了。** M1R1〜M1R7 全部做完，端到端冒烟一条命令跑通。

- **M1R4** 打通生成链路：上传图 → 提交 → ComfyUI 生成 → 首帧就是那张图且不变形
- **M1R5** 加上任务的概念：状态机 + 容量为 1 的串行队列 + 四个对外路由
- **M1R6** 取回产物：`GET /v1/jobs/{id}/video`，支持 Range（手机能拖进度条）
- **M1R7** 端到端冒烟：`scripts/smoke_m1.sh`，退出码 0

测试 145 条离线（Level 2）+ 5 条预检（Level 2.5，不占 GPU）+ 冒烟（Level 3）。

⚠️ **M1 的退出标准只达成了一半。** 执行文档要求「下载回一个**能在手机上播放的**
H.264 MP4」，冒烟跑通 = 网关侧达成，**但「手机上能播」只有真在手机上播过才算** ——
而 App 还不存在。**不要因为冒烟全绿就把 M1 标成完成。**

**下一步是 M2** —— Android 原型：连接、上传、提交、播放。
VS 编号从 **VS-30** 开始（VS-19〜29 预留给网关）。

## 2. 这个项目一句话

Android App 当手机端的远程生成控制台，家里那台 4080 SUPER 的 Windows/WSL 机器
跑 ComfyUI 出视频和图，中间隔一个自己写的 FastAPI 网关，全程走 Tailscale。

两端在同一个仓库：`app/` 和 `server/`。

---

## 3. 下一轮（M2）要验证的假设

**手机上到底播不播得动。**

这是 M1 退出标准里唯一没达成的那条。网关侧已经准备好：
`GET /v1/jobs/{id}/video` 支持 Range，响应头里有 `Accept-Ranges: bytes`
（播放器靠它决定要不要让用户拖进度条）。

还没定的事：**进度怎么推给 App** —— 转发 ComfyUI 的 WebSocket，还是让 App 轮询。
⚠️ 这条现在有了具体后果：**`stage` 与 `progress` 在 M1 恒为空**，
`GET /queue` 只能回答「是不是在跑」，回答不了「跑到哪一段」。
而需求 F2 明确要求把分钟级的 `loading_model` 单独显示 ——
App 上把它和采样混成一个「生成中」，用户会以为卡死了。

## 4. 会咬人的坑

按「多久会撞上」排。

### 4.0 跑网关的测试必须 `cd server`，不能在仓库根目录跑

`asyncio_mode = "auto"` 写在 `server/pyproject.toml` 里，
从根目录跑 pytest 读不到它，所有裸 `async def` 测试都报
`async def functions are not natively supported`。

实测：根目录跑有一批**假失败**，`server/` 目录跑才是全绿（当前 145 条）。
**那 10 条是假失败**，很容易被当成真回归去查。

### 4.01 ⚠️ ComfyUI 的 `completed` 字段不是「结束了」的意思

**它的含义是「成功完成」。** 被中断或报错的任务是：

```
status_str = "error"    completed = False
```

M1R7 冒烟实测到的。用 `completed` 判断「这次执行结束了没有」，
**被中断和报错的任务就永远等不到终态** —— 而任务超时默认是关的，即无限轮询。

⇒ 判断执行是否结束用 `JobRecord.finished`（白名单 `{success, error}`），
不要用 `completed`。

⚠️ **更该记住的是这个 bug 为什么没被测出来**：`FakeComfyClient` 把失败写成了
`completed=True`，**替身与真实行为不符**，于是 145 条离线测试全绿。
**替身写错时，它保护的不是代码，是那个 bug。**

### 4.05 归一化只能做一次，否则 seed 会对不上

`normalize()` 在用户没填 seed 时会**随机生成**一个，
而 `build_workflow()` 内部会调 `normalize()`。

所以**不能**「提交时算一次回给 App，执行时再算一次喂给 ComfyUI」——
两个 seed 不一样，任务照样成功、视频照样出来，
只是用户拿回报的 seed **永远复现不出那个视频**，**而且不报错**。

现在的做法：探测首帧尺寸与建图都在 `POST /v1/jobs` 里做完，
结果存在 Job 上带着走。VS-18 守这条。

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

### 4.6 装 Python 包：两台机器的做法不一样

**服务器**：代理变量指向 `127.0.0.1:7890`，而那个端口**没有进程监听**。
`pip install` 必须摘掉代理并走镜像 —— `scripts/deploy_server.sh` 已经处理好了，
手工在那边装包时要记得同样处理。

**开发机**：代理是活的，但**缺 `python3.10-venv`**（`ensurepip` 不存在，装它要 sudo）。
所以开发机建 venv 用 `~/.local/bin/uv`：

```bash
cd server && uv venv .venv --python 3.10
uv pip install --python .venv/bin/python -r requirements.txt
```

⚠️ 两端建 venv 的工具不同（uv vs `python3 -m venv`），这是一处已知的不对称。

### 4.7 ⚠️ WSL 端口连不上时，先分清是两种坑里的哪一种

M1R3 中途撞到过：8188 / 2222 / 7869 从外面全连不上，但 Windows 的 22 正常。
**不是 WSL 被回收**（发行版当时已经跑了 1 天 3 小时），
而是 Hyper-V 防火墙只放行环回、不放行外部入站。

判别方法与处置见 runbook §6.5。**已加规则恢复，但可能再次发生。**

### 4.8 用户实际在用的模板不在 ComfyUI 目录里

`Docs/knowledge/DasiwaMinimaxH3WorkflowsT2VA_cMMH3V23.json` 是**第一手依据**
（采样 profile、对齐指令行写法、`N/A` 规则都来自它）。
它原本只在 Windows 的 `E:\Downloads\minimax-h3\`，已拷进仓库。
ComfyUI 自带的 V13/V16 是**不同的版本**，别拿它们当准。

### 4.9 WSL 会自己停下来

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
