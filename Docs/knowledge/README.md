# knowledge —— 外部来源的原始材料

**这个目录只读不改。** 里面的东西不是本项目写的，改它等于伪造证据来源。
需要引用时指过来，不要把内容抄进别的文档。

| 文件 | 是什么 | 来源 |
|---|---|---|
| `chat_dialogue.json` | 2026-09-21～22 与 Codex 在服务器上的会话记录（JSONL，114 行） | 需求就是从这里整理出来的，见 `Docs/requirement/` |
| `DasiwaMinimaxH3WorkflowsT2VA_cMMH3V23.json` | **用户实际在用的那份 ComfyUI 工作流模板** | Windows `E:\Downloads\minimax-h3\`，2026-09-22 经 WSL 的 `/mnt/e/` 取出 |

---

## 关于那份 V23 模板

⚠️ **它不在 ComfyUI 的目录里。** 用户是手动拖进前端页面加载的，
ComfyUI 自带的 `custom_nodes/ComfyUI-DaSiWa-Nodes/workflows/` 下只有 V13 和 V16
（84 KB，与 V23 的 md5 都不同）。所以**要看它只能看这一份**。

**它是网关拼 workflow 的第一手依据**，尤其这几处：

* **采样参数分 Turbo / 非 Turbo 两套**，见它的 `Settings & Post-Processing` 注释。
  需求文档 §8.2 最初把两套混在了一起，M1R2 据此更正。
* **对齐指令行的实际写法**，见 MarkdownNote `#2695`（单帧）与 `#2696`（首尾帧）。
  ⚠️ 与 Director 的**文档**不一致：文档带 `<>` 和 `[]`，这两个示例不带。
* **空的 `non_diegetic_music` 要填 `N/A`**，见 Quick Start 注释。
* 它用 `MiniMaxH3SigmaShift`，而 ComfyUI 自带的官方模板没有 ——
  所以网关要带这个节点。

另一份参考是 ComfyUI 自带的官方模板，**没有拷进来**（它随 ComfyUI 安装，
在服务器的 `comfyui_workflow_templates_json/templates/video_minimax_h3_{i2v,t2v}.json`）。
两份在采样链结构上一致；帧数换算公式取自官方那份。

### 怎么再看一次

```bash
# 这份（已在仓库里）
python3 -c "import json; d=json.load(open('Docs/knowledge/DasiwaMinimaxH3WorkflowsT2VA_cMMH3V23.json')); \
print([n['type'] for n in d['definitions']['subgraphs'][0]['nodes']])"

# 官方那份（在服务器上）
ssh icewalnut-wsl 'ls ~/workspace/ComfyUI/.venv/lib/python3.10/site-packages/comfyui_workflow_templates_json/templates/ | grep minimax_h3'
```
