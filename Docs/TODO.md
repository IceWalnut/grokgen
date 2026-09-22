# TODO

**本文件是任务与里程碑的权威跟踪。** 项目总览看根 `README.md`，
当前会话上下文看 `Docs/ContextPack.md`，三者职责见 `AGENTS.md` §1.3。

⚠️ **只有这一份，不在 `server/Docs/` 或 `app/Docs/` 下另建。**
里程碑是跨两端的（M2 起两端都要动），状态分两处记就得读两份才知道项目在哪。

状态记号：⬜ 未开始 · 🔄 进行中 · ✅ 已完成 · ⏸ 阻塞 · ❌ 作废

---

## 里程碑

里程碑的内容与验收标准见 `Docs/requirement/grokgen_requirements_v0.1.md` §9。

| | 里程碑 | 涉及 | 状态 |
|---|---|---|---|
| M1 | 后端主链路：提交 H3 任务、报告状态、取回视频 | server | 🔄 |
| M2 | Android 原型：连接、上传、提交、播放 | app + server | ⬜ |
| M3 | 媒体库：索引、缩略图、网格、在线播放 | server + app | ⬜ |
| M4 | 接入 SD：生图 + 服务切换 + 显存释放 | server + app | ⬜ |
| M5 | 高级功能：LoRA、REF2VA、多参考、收藏、批量下载 | 两端 | ⬜ |

---

## M1 轮次

执行文档：`server/Docs/implementation/M1_gateway_mainline.md`
每轮的判据与 VS 编号见该文档 §3。

| 轮次 | 要解决的问题 | VS | 状态 |
|---|---|---|---|
| M1R1 | 网关骨架 + 部署链路首次实跑 | VS-12, VS-13 | ✅ |
| M1R2 | workflow 构造器正确 | VS-1〜4 | 🔄 下一轮 |
| M1R3 | ComfyUI 客户端 + 第一次真实生成 | VS-5〜7 | ⬜ |
| M1R4 | 上传 + 首帧真的生效 | VS-8 | ⬜ |
| M1R5 | 任务状态机与串行队列 | VS-9, VS-10 | ⬜ |
| M1R6 | 对外 API + Range | VS-11 | ⬜ |
| M1R7 | 端到端冒烟脚本 | 全部复跑 | ⬜ |

---

## 已完成的准备工作

| 事项 | 完成于 | 记录在 |
|---|---|---|
| ✅ 需求整理（从 chat_dialogue.json） | 2026-09-22 | `Docs/requirement/` |
| ✅ 网关架构定稿 | 2026-09-22 | `server/Docs/architecture/` |
| ✅ 接口契约初稿 | 2026-09-22 | `Docs/contract/` |
| ✅ 验证标准 | 2026-09-22 | `Docs/Validation.md` |
| ✅ M1 执行文档 | 2026-09-22 | `server/Docs/implementation/` |
| ✅ git 仓库 + 服务器 clone + 部署脚本 | 2026-09-22 | `scripts/deploy_server.sh` |
| ✅ SD 模型软链接归位并验证 | 2026-09-22 | M1 执行文档 §5 |

---

## 未决事项

按「什么时候必须解决」排，不按提出时间。

### 挡住 M1

*（当前没有）*

### 挡住 M2（Android 原型）

- ⬜ **进度怎么推给 App**：转发 ComfyUI 的 WebSocket，还是让 App 轮询。
  影响 App 端的实时性与耗电。
- ⬜ **接口契约要按 M1 的实际实现回填**：`Docs/contract/gateway_api_v0.1.md`
  现在是草案，M1 跑通后按真实字段更新。

### 挡住 M3（媒体库）

- ⬜ **任务与媒体的元数据存哪**：SQLite 还是 JSON 索引文件。
  倾向 SQLite（要分页和按来源过滤）。
- ⬜ **缩略图什么时候生成**：任务结束时顺手做，还是首次访问时懒生成。
  倾向前者，那时文件刚写完、磁盘缓存还热。
- ⬜ **任务状态持久化**：M1 只存内存，网关一重启就丢。

### 挡住 M4（接入 SD）

- ⬜ **SD 的两代模型怎么处理**：`v1-5-pruned-emaonly` 是 SD 1.5，
  其余三个（6.5–6.7 GB）看着像 SDXL 系。**两代 workflow 不一样** ——
  要么只支持一代，要么按模型类型分别拼图。

### 不挡任何里程碑

- ⬜ **`server/requirements.lock.txt` 从未被用来安装过**。它是 M1R1 部署后从服务器
  `pip freeze` 出来的记录，部署脚本装的仍是不锁版本的 `requirements.txt`。
  真要靠它复现环境时可能装不出来。
- ⬜ **`HttpComfyClient` 的错误分支一条都没实际触发过**（超时、非 2xx、`devices` 为空）。
  按分层它豁免于开发机测试，但「只有代码没有证据」这件事要记着。
- ⬜ **网关升级成 systemd 服务**（自动重启、开机自起）。
  需要一次交互式 sudo 或 `loginctl enable-linger`。
  现在用 `setsid nohup`，和 ComfyUI 一样。
- ⬜ **把 ComfyUI 收回到只监听 `127.0.0.1`**。
  等 M2 的 App 打通后再做，现在两个入口并存不影响什么。
