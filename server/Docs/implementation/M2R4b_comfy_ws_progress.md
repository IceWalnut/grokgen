# M2R4b 执行文档：网关接 ComfyUI 的 WebSocket，让进度有真实数据

**状态**：✅ 完成（2026-09-28）。VS-23〜28 全部达成，总结见 `Docs/experience/2026-09-28/M2R4b_comfy_ws_progress.md`
**上游**：`server/Docs/architecture/gateway_architecture_v0.1.md`、契约 `Docs/contract/gateway_api_v0.1.md` §2
**所属里程碑**：M2（Android 原型）。这是插在 M2R4 与 M2R5 之间的**网关侧**一轮。

⚠️ **编号为什么是 `M2R4b`**：M2R5（手机上播放）在文档与 App 代码注释里已经被引用了 20 多处，
重新编号的代价远大于收益。所以这一轮叫 M2R4b，**M2R5 的编号与内容不变**。

---

## 1. 要解决的问题

契约 §2 的 `stage`（`running` 时的细分阶段）与 `progress`（采样百分比）**现在恒为 `null`**。
它们的数据只能来自 ComfyUI 的 WebSocket 事件（M2R4 已读源码核实：`/api/jobs` 系列只给状态与时间，
步数只在 `progress` 事件里，当前节点只在 `executing` 事件里）。

App 侧 M2R4 已按契约把各种取值的显示做好（`model/JobStatusView.kt`），**网关接上之后 App 不用改**。

⇒ 本轮只做「网关 → ComfyUI」这一段。「网关 → App」那段仍是 App 轮询，**不改**（契约 §6.1）。

## 2. 设计上先定死的三件事

1. **WebSocket 只是进度的来源，不是状态的来源。**
   任务走到哪个 `state`、何时结束，仍然由现有的 `/queue` + `/history` 轮询决定。
   ⇒ ws 连不上、中途断开、收到看不懂的消息，**最坏的后果是 `stage`/`progress` 变回 `null`**，
   任务照常跑到终态。这是本轮最重要的一条不变量（VS-24）。
2. **一条常驻连接、一个固定的 `clientId`**，所有任务都用它提交；事件按 `prompt_id` 分给对应任务。
   不按任务各开一条 —— 那样每个任务都要在提交前先建连接，建连失败会变成提交路径上的新故障点。
3. **超时与重连一律显式设置。** 连接超时、握手超时、心跳间隔与心跳超时、重连退避的上下界都写成具名常量。
   ⚠️ 不许出现「传 `None` 表示用默认值」这种写法 —— M2R1 已经在 httpx 的 `timeout=None` 上栽过一次
   （它的意思是「不设超时」）。这台服务器的 WSL 用镜像网络，**关闭端口上的连接是被丢弃而不是被拒绝**，
   所以「没有超时」会真的变成永远等下去（ContextPack §4.001）。

## 3. 步骤

| 步 | 做什么 | 产出 |
|---|---|---|
| 1 | 本文档 | 判据先于实现写下 |
| 2 | **只读录制**：在服务器上用一个临时脚本连 `ws://127.0.0.1:8188/ws?clientId=…`，经 `POST /prompt` 提交一个真实任务，把收到的每一条消息（含二进制帧的长度与类型）原样存下来。**录两次：模型不在显存（先 `/free`）一次、模型已在显存一次**；再录一次「任务跑到一半时断开 ws 再重连」看重连后 ComfyUI 会补发什么 | `server/tests/data/comfy_ws/*.jsonl` |
| 3 | 按录制结果定「哪个节点 / 哪类事件 → 哪个 `stage`」，写进本文档 §5 | 映射表 |
| 4 | 改网关：`ComfyClient` 加事件流；真实实现用 `websockets`；替身按录制回放、也回放断线；任务管理器收到事件后更新 `stage` / `progress` | 代码 + 测试 |
| 5 | 验证：离线测试（关键测试植入缺陷验证会红）→ 部署 → 服务器上看 `GET /v1/jobs/{id}` → 真机看界面 | §4 的判据逐条 |
| 6 | 收尾：总结、契约「`stage` 恒为 null」那段、TODO、ContextPack | |

⚠️ **第 2 步必须先于第 4 步。** 替身要回放的是真实序列，不是按文档想象出来的序列。
ContextPack §4.011 记着这个项目连续三轮犯过同一个错：**替身只演好戏，从不产出真实系统真会产出的坏值**。
录制里出现的每一种「意料之外」（二进制帧、别的客户端的事件、`prompt_id` 缺失、重复事件、乱序）
都要进替身的回放。

## 4. 判据

VS 编号用网关号段里还空着的 VS-23〜29（VS-19〜22 已被 M1R7 用掉）。定义同步登记在 `Docs/Validation.md` §3。

| 编号 | 判据 | 层级 |
|---|---|---|
| VS-23 | **把真实录制的事件序列回放给任务管理器，`stage` 依次出现 `loading_model` → `sampling` → 解码 / 编码阶段，`progress` 在 `sampling` 期间单调不减、最后一次等于 1.0、在其他阶段为 `null`**。⭐ 要有正向的数：断言这几个阶段**确实出现过**、`progress` 至少取到录制里那么多个不同的值 —— 只断言「没出现非法值」的话，把事件处理整段删掉也能通过 | 2 |
| VS-24 | **ws 断开时任务照常跑完，只是进度变回空**：替身在采样中途断线 → `stage`/`progress` 变回 `null`，任务仍走到 `done`，且日志里没有 ERROR；ws 从一开始就连不上时同样 `done`。**Level 3**：在服务器上让 ws 在任务中途断开一次（见 §6），`GET /v1/jobs/{id}` 显示 `stage` 变回 `null`、任务照常 `done` | 2/3 |
| VS-25 | **超时与重连是显式的，并且对真实实现本身测过**（不是只测替身）：真实的 ws 客户端对着本机一个「收下 TCP 连接但永远不握手」的端口，**在设定的超时内**放弃并进入重连，而不是挂住；对着一个会主动断开的本地服务端，会按退避重连 | 2 |
| VS-26 | **只采用属于自己任务的事件**：别的 `prompt_id`（例如用户在 ComfyUI 网页上提交的）、没有 `prompt_id` 的事件、二进制预览帧、看不懂的消息类型，都不会改动任何任务的 `stage`/`progress`，也不会让事件循环退出 | 2 |
| VS-27 | **部署后，`GET /v1/jobs/{id}` 返回的 `stage` / `progress` 真的在变**：在服务器上每秒查一次，把出现过的 `(state, stage, progress)` 去重记下来，至少看到 `loading_model` 或 `sampling` 与 3 个以上不同的 `progress` 值 | 3 |
| VS-28 | **真机上看到「正在加载模型」→「采样中 N%」真的出现**（截图为证），任务最终「完成」 | 3/4 |

⚠️ 「正在加载模型」这一段在模型已经在显存时**可能极短甚至不出现**（加载被缓存跳过）。
VS-28 要在模型**不在**显存时做（先 `/free`，M1R7 实测这种情况总耗时约 94 秒），否则看不到它不代表有 bug。

## 5. 事件 → `stage` 的映射（按 2026-09-28 的三次录制定）

### 5.1 录到了什么

录制脚本 `scripts/lib/record_comfy_ws.py`，录制文件在 `server/tests/data/comfy_ws/`。
条件：ComfyUI `3c80da7f`，4080 SUPER，文生视频 736×416 / 124 帧 / 8 步 Turbo，**每种各 1 次**。

| 录制 | 总耗时 | 要点 |
|---|---|---|
| `t2va_model_not_in_vram.jsonl`（先 `/free`） | 88 秒 | 加载类节点逐个 `executing`（1.2〜15.4 秒，其中 `h3_image_to_video` 文本编码约 10 秒）；**`sampler` 开始执行后 22 秒才来第一个 `progress`** |
| `t2va_model_in_vram.jsonl` | 60 秒 | 加载类节点全在 `execution_cached` 里，**不发 `executing`**；`sampler` 开始后 10.3 秒才来第一个 `progress` |
| `t2va_disconnect_after_step3.jsonl` | 61 秒 | 第 3 步后断开、5 秒后同一 `clientId` 重连：**ComfyUI 补发一条不带 `prompt_id` 的 `executing {"node": "sampler"}`**；断线期间的事件**不补发** |

每一步采样约 5.5 秒，`progress` 在**每一步结束时**发，`value` 1〜8、`max` 8。

**替身必须回放的「意料之外」**（都来自录制，不是想象的）：

1. 第三方插件每秒广播一条 `dasiwa.system_monitor`（CPU / 内存 / GPU 读数），与任务无关；
2. 新版 ComfyUI 每次状态变化都多发一条 `progress_state`（所有节点的快照），与 `executing` / `progress` 重复；
3. `status` 消息只有第一条带 `sid`，都不带 `prompt_id`；
4. **重连后那条没有 `prompt_id` 的 `executing`** —— 见 5.3；
5. 录制里**没出现二进制帧**（这台 ComfyUI 没开采样预览）。开了预览就会有，所以真实客户端仍要处理它，
   但那是构造出来的用例，不是录到的（`Docs/Validation.md` §3 规则 ①）。

### 5.2 映射

按节点的 `class_type` 查（节点 id 是网关自己拼图时起的，`class_type` 从任务自己的 workflow 里取）：

| 事件 | `stage` | `progress` |
|---|---|---|
| `executing` 一个加载 / 准备类节点（`UNETLoader`、`CLIPLoader`、`VAELoader`、`LoraLoaderModelOnly`、`MiniMaxH3SigmaShift`、`MiniMaxH3ImageToVideo`、`LoadImage`、`BasicGuider`、`BasicScheduler`、`KSamplerSelect`、`RandomNoise`） | `loading_model` | `null` |
| `executing` 采样节点（`SamplerCustomAdvanced` 等），**还没收到它的 `progress`** | `loading_model` | `null` |
| 采样节点的 `progress` | `sampling` | `value / max` |
| `executing` `VAEDecode` | `decoding_video` | `null` |
| `executing` `VAEDecodeAudio` | `decoding_audio` | `null` |
| `executing` `CreateVideo` / `SaveVideo` | `encoding` | `null` |
| `executing` 一个不在上表里的节点 | `null`（**不猜**） | `null` |
| `execution_success` / `execution_error` / `execution_interrupted` / `executing` 且 `node` 为 `null` | `null` | `null` |
| ws 断开 | 该任务变回 `null` | `null` |
| 其余一切（`status`、`progress_state`、`execution_start`、`execution_cached`、`executed`、插件广播、没有 `prompt_id` 的、别人的 `prompt_id` 的） | 不变 | 不变 |

⚠️ **「采样节点开始执行、但第一个 `progress` 还没来」为什么算 `loading_model`**：
录制显示主模型是在采样节点**内部**、第一步之前才搬进显存的。模型不在显存时这段空档 22 秒，
而一步只要 5.5 秒 —— 若把它标成「采样中 0%」，用户会看到 0% 卡 20 多秒。
⚠️ **这个标法不精确，如实记下**：`progress` 在一步**结束**时才发，所以这段空档 = 加载 + 第一步。
模型在显存时空档 10.3 秒，其中约 5.5 秒其实是第一步在跑。**两者从事件上分不开。**

⚠️ **不用 `progress_state`**：它与 `executing` + `progress` 表达的是同一件事，而旧版 ComfyUI 没有它。
只认一套，免得两套在某个边界上给出不同答案。

### 5.3 为什么没有 `prompt_id` 的事件一律不用

重连后 ComfyUI 补发的 `executing {"node": "sampler"}` 不带 `prompt_id`，
而且**说不清采样开始多久了** —— 录制里它出现时已经采样到 3/8。
若按 5.2 把它当成「采样节点刚开始执行」，一个跑到 37% 的任务会**倒退回「正在加载模型」**。
丢掉它的代价只是：重连后到下一个 `progress`（至多一步，约 5.5 秒）之间 `stage` 为 `null`。

## 6. 服务器上怎么让 ws 断开而不影响 ComfyUI

VS-24 的 Level 3 需要在任务**正在跑**时断开 ws，同时不能动 ComfyUI 本身（否则任务也没了）。
做法：在服务器上临时起一个 TCP 转发，只转发 ws；再临时起**第二个网关实例**（另一个端口），
让它的 ws 地址指向这个转发；在那个实例上提交任务，采样途中杀掉转发 → 看 `stage` 变空、任务照常完成。
生产那个网关实例在这期间保持空闲，不受影响。
⚠️ 这要求 ws 的地址可以单独配置（`GROKGEN_COMFY_WS_URL`），默认由 `comfy_base_url` 推出。

## 7. 本轮不做

- 网关向 App 推送（契约 §6，M2 明确不做）
- 用 ws 事件驱动 `state` 的转移（见 §2 第 1 条）
- 「图生视频却没带首帧图」的 422、部署脚本的 fetch 进度输出 —— 都记在 TODO，与本轮无关，不顺手做
