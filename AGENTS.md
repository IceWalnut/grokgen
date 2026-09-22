# grokgen 项目规则

本文件是 `grokgen` 项目的补充规则，配合全局规则使用。

优先级：本文件与全局规则冲突时，**本文件优先**；更深层目录若有自己的 `AGENTS.md`，那份再优先。

项目根目录：`/home/zzqin/workspace/grokgen`（原生 Linux / Ubuntu 22.04）

---

## 0. 这个项目是什么

**grokgen 是一个手机端的「远程生成控制台」**：Android App 做前端，家里那台带 RTX 4080 SUPER
的 Windows 机器（WSL2 Ubuntu）做后端，两端通过 Tailscale 私有网络连接。

App 自己不跑任何模型。它提交任务、看进度、看结果；模型加载、显存管理、任务队列、媒体库索引
全部在后端。

**代码分两处，不要搞混：**

前后端在**同一个仓库**：`git@github.com:IceWalnut/grokgen.git`（private，分支 `main`）。

| 放哪 | 是什么 |
|---|---|
| 本仓库 `grokgen/app/` | Android 客户端源码 |
| 本仓库 `grokgen/server/` | **FastAPI 网关的源码 —— 唯一真相源** |
| 服务器 `~/workspace/grokgen/` | 同一个仓库的**只读检出**，由 `scripts/deploy_server.sh` 拉取 |
| 服务器 `~/workspace/ComfyUI/` 等 | 已有的 ComfyUI / MiniMax H3 / SD 部署 |

同步链路：**开发机 push 到 GitHub → 服务器 pull**。

⚠️ **不要在服务器上改代码** —— 部署脚本用 `git reset --hard origin/main`，改了就没了。
要调试就改本仓库、提交、推送、重新部署。

后端的**运行与验证只能在服务器上做**（这台开发机没有 GPU）。
但网关里只有「调用 ComfyUI」那一块依赖 GPU，**它必须放在一个接口后面**，
否则开发机上一个单元测试都跑不了。
连接方式、地址、端口、已知坑一律看 `Docs/runbooks/home_gpu_server.md`，
**不要在别的文档里重抄一遍那些事实。**

需求定义看 `Docs/requirement/`。

---

## 1. 会话启动入口

开始任何开发、调试或文档任务之前，默认按顺序读：

1. `AGENTS.md`（本文件）
2. `Docs/ContextPack.md` —— 当前阶段、当前目标、已知坑、下一步
3. `Docs/TODO.md` —— 里程碑与任务状态
4. `Docs/requirement/` —— 需求与验收定义
5. 相关的 `Docs/design/`、`Docs/architecture/`、`Docs/implementation/` 文档
6. `Docs/runbooks/home_gpu_server.md`（任务涉及后端、GPU、服务器上的任何东西时）
7. 最近的 `Docs/experience/YYYY-MM-DD/*.md`

项目还在早期，上面有些文件可能**还不存在**。不存在就跳过，并在复述里说明它不存在，
不要凭空编造它的内容。

**读完先复述当前项目状态、本轮要验证的假设、以及简短计划，再动手。**

若用户说「本轮不写代码」或「只读只评估」，则不修改任何文件，只记录建议。

## 1.1 什么算一个 round

一个 round 是**为解决一个明确问题所做的一次完整努力**：读日志/结果 → 分析原因 → 改代码 →
构建与测试 → 再分析；不通过就迭代。问题解决、或阻塞在明确的外部因素上，这个 round 结束。

一个 round 内部可以包含多次「改 → 构建 → 测试」，**它不等于一条聊天消息**。

## 1.2 每个 round 结束时必做

1. 写正式总结到 `Docs/experience/YYYY-MM-DD/summary_HH_MM_SS.md`
2. 同步更新 `README.md`、`Docs/TODO.md`、`Docs/ContextPack.md`
3. 若某个文件本轮未受影响，**显式确认它仍然准确**，再决定不改

## 1.3 三个入口文档的职责，不得互相侵入

| 文件 | 职责 | 不应变成 |
|---|---|---|
| `README.md` | 稳定的项目总览：长期事实、入口、指向深层文档的链接 | 会话日志、里程碑跟踪器 |
| `Docs/TODO.md` | 权威的任务与里程碑跟踪 | 项目总览、命令手册 |
| `Docs/ContextPack.md` | 会话引导上下文：当前阶段、目标、已知坑、下一步 | 里程碑归档、README 的副本 |

**非冗余规则**：写之前先判断这条信息属于「长期事实 / 工作状态 / 会话上下文」哪一类，
只写进拥有该类别的那一个文件。必须出现在多处时，选一个作为唯一真相源，其余只留一句指针。

## 1.4 新文档放哪

`Docs/` 根目录只保留 `TODO.md`、`ContextPack.md`、`Validation.md`，**不得堆积新文件**。

| 目录 | 放什么 |
|---|---|
| `Docs/requirement/` | 需求文档：做什么、不做什么、验收标准 |
| `Docs/design/` | 调研、方案比较、范围定义 |
| `Docs/architecture/` | 结构、分层、接口契约、决策记录 |
| `Docs/implementation/` | 执行计划与每个里程碑的执行细节 |
| `Docs/reports/` | 测量与评估产物：性能、显存、生成耗时、退出评审 |
| `Docs/experience/YYYY-MM-DD/` | 每个 round 的会话总结 |
| `Docs/runbooks/` | 操作流程：怎么连服务器、怎么起停服务 |
| `Docs/knowledge/` | 外部来源的原始材料（如导出的对话记录），**只读不改** |
| `Docs/Q&A/` | 设计提问与调研报告 |

移动文档时，**在同一次改动里更新所有引用路径** —— 引用是散文式路径，不是链接，
失效了不会报错，只会误导下一个读者。

---

## 2. 开发管线

```text
粗需求 → Docs/requirement/ 需求文档 → Docs/architecture/ 架构文档
       → Docs/implementation/ 执行文档 → 按执行文档执行
```

**顺序不可跳。** 发现某个决策站不住时，回去改上游文档，改完再继续；
**不要在代码里绕过文档里写死的决策**。

区分两件事：**前提被推翻 → 改文档**；**实现有难度 → 改代码**。

## 3. 项目原则

1. **后端网关是核心，App 是薄前端。** 换 workflow、换模型、换 ComfyUI 自定义节点时，
   Android 端不应该需要大改。模型细节不进 App。
2. **App 不直接连 ComfyUI 的 8188。** 所有请求走自建网关。理由见 `Docs/requirement/`：
   任务队列、显存切换、媒体索引都需要一个能拒绝并发重任务的统一入口。
3. **显存是硬约束，不是优化项。** 16 GB 显存装不下 MiniMax H3 与 Stable Diffusion 同时常驻。
   任何设计如果默认「两个服务都在」，就是错的。
4. **同一时间只允许一个重 GPU 任务。** 这条由网关强制，不靠调用方自觉。
5. **失败必须可见。** 模型切换失败、显存没释放、ComfyUI 进程死掉，都要作为明确状态返回给 App，
   不允许用超时或空结果掩盖。
6. **默认流式查看服务器上的媒体，下载是用户的显式动作。** 不要把生成结果自动同步到手机。
7. **名字描述内容，不描述出处。** `videoAssetId` 而非 `result`，`firstFrameImage` 而非 `img1`。

## 4. 环境约定

**本机（Android 开发端，已装好）：**

- Android SDK：`~/Android/Sdk`，已安装 platform `android-26` 与 `android-36`
- `adb`：`~/Android/Sdk/platform-tools/adb`（**不在 PATH 里**，用全路径或自己加 PATH）
- JDK 17：`/usr/lib/jvm/java-17-openjdk-amd64`
- ⚠️ **系统默认 `java` 是 11，保持不动**，也不用 `update-alternatives` 切换。
  项目通过 Gradle toolchain 声明 JDK 17。
- Gradle 走 Wrapper（`./gradlew`），不要求全局安装
- 本机**没有 GPU**，后端代码可以在本机写、在本机跑单元测试，但**任何涉及模型或显存的验证
  都必须在服务器上跑**

**服务器（GPU 后端）：** 见 `Docs/runbooks/home_gpu_server.md`，不在这里重复。

**真机验证：** App 的播放、上传、相册这几块只能在真机上验证；
模拟器没有相机、相册内容和真实网络条件。

## 5. 结论必须可复现

- 改动前先列假设，不要边想边改。
- 分层定位问题：是 App 的问题、网关的问题、还是 ComfyUI 的问题，先分清再动手。
- 根因不清时先加可观测性（日志、抓包、`nvidia-smi` 采样），而不是猜着改。
- 每个结论都要写清**用什么命令、在什么输入上、得到什么输出**。没有复现路径的结论不算结论。
