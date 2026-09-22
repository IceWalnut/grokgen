# M1R1 总结：网关骨架与部署链路

日期：2026-09-22
执行文档：`server/Docs/implementation/M1_gateway_mainline.md` §3 M1R1

**要解决的问题**：网关能被部署到服务器并从开发机访问到。
**结论**：解决了。VS-12、VS-13 通过。

---

## 1. 验证了什么

### VS-12 部署链路（Level 3）

```bash
scripts/deploy_server.sh
curl -s --noproxy '*' http://icewalnut-1060.tail22a711.ts.net:7869/v1/health
```

返回：

```json
{"gateway": {"status": "ok", "version": "aa4ca40"},
 "comfy": {"reachable": true,
           "gpu": "cuda:0 NVIDIA GeForce RTX 4080 SUPER : cudaMallocAsync",
           "vram_total_bytes": 17170956288, "vram_free_bytes": 15647768576}}
```

HTTP 200，耗时 58 ms。

两个点使这条结果有说服力：

1. `version` 是 `aa4ca40`，正是本轮刚推上去的 commit ——
   证明访问到的确实是新部署的那一版，不是某个残留进程；
2. `vram_total_bytes` 是真实读数 —— **开发机上没有 GPU，拿不到这个数**，
   所以它证明了网关确实跑在服务器上。

### VS-13 分层约束（Level 1）

`server/tests/test_layering.py` 扫 AST，断言除 `comfy/http_client.py` 外
没有模块 import HTTP 库、没有 ComfyUI 地址的字面量。

⚠️ **两条都植入缺陷验证过会红**，不是只看它绿：

| 植入的缺陷 | 测试的反应 |
|---|---|
| 在 `app/main.py` 加 `import httpx` | 报 `app/main.py:7 import httpx` |
| 在 `app/main.py` 加 `_planted = "http://127.0.0.1:8188"` | 报 `app/main.py:67` |

两次都报出了**具体文件和行号**，不是一个笼统的失败。验证完已恢复。

### Level 2 自动化测试

```bash
server/.venv/bin/python -m pytest -m "not integration" -q
# 9 passed
```

含 health 的两条路径（可达 / 不可达）、配置默认值、VAE 常量不可被环境变量覆盖、
输出目录 `~` 展开、以及上面两条分层检查。

### Level 2 本地起服务

开发机上 `uvicorn app.main:app --port 7869`，`/v1/health` 返回 200 且
`comfy.reachable` 为 `false`，error 是 `ConnectError('All connection attempts failed')`。

⭐ **这一条不是顺带**：它验证了「上游不可达时如实报告而不是崩」，
而开发机恰好提供了这个场景（本机没有 8188）。

---

## 2. 本轮撞到并解决的两个环境问题

### 2.1 服务器上 pip 装不了包（计划阶段发现，已修）

服务器的 WSL 继承了 `http_proxy=http://127.0.0.1:7890`，
**而那个端口上没有任何进程在监听**。实测：

| 从服务器访问 | 结果 |
|---|---|
| 带当前代理访问 pypi.org | 20 秒超时，`http=000` |
| 摘代理直连 pypi.org | 不稳定（一次超时、一次 302 后停住） |
| 清华镜像 | **http=200，0.22 秒，755 KB/s** |

对照：**开发机上代理是活的**，访问 pypi 3.5 秒成功。
⇒ 两台机器的 pip 行为不同，不能假设本地装得上服务器就装得上。

处置写进 `scripts/deploy_server.sh`：**只摘 pip 那一次调用的代理变量**，
并走清华镜像（地址提成变量 `PIP_INDEX_URL`，便于更换）。
不改那台机器的代理配置 —— 那是用户自己在用的（runbook §6.3 的既有方针）。

### 2.2 开发机缺 `python3.10-venv`（本轮新发现）

开发机上 `python3 -m venv` 失败：`ensurepip is not available`，
`/usr/lib/python3.10/ensurepip` 不存在。装它需要 sudo。

处置：**开发机用已装好的 `uv`（`~/.local/bin/uv`，v0.12.1）建 venv**，不需要 sudo。
服务器那侧 `python3 -m venv` 是好的（已验证 `ensurepip` 可用，pip 22.0.2），
部署脚本不动。

⚠️ **两台机器建 venv 的工具不同，这是一个已知的不对称**，已写进 ContextPack。

---

## 3. 没验证什么

- **`requirements.lock.txt` 没有被用来安装过。** 它是 M1R1 部署后从服务器
  `pip freeze` 出来的**记录**，部署脚本装的仍然是不锁版本的 `requirements.txt`。
  也就是说「用 lock 文件能否装出同样的环境」这件事没验过。
- **`HttpComfyClient` 只在真实部署里跑过一次 `/system_stats`**，没有针对它的
  自动化测试（按架构分层，它是唯一豁免于开发机测试的模块）。
  它的错误分支（超时、非 2xx、`devices` 为空）**一条都没有实际触发过**。
- **网关重启后的行为没验。** 这一轮每次都是干净启动，没测过
  「ComfyUI 先挂再起，网关能否自己恢复」。
- **并发没验。** 只有单个 curl，没有并发请求。
- **`pkill -f 'uvicorn app.main:app'` 只在有且仅有一个 uvicorn 时用过**，
  没验证过它会不会误杀别的进程。

## 4. 哪些验证失败过

`test_no_hardcoded_comfy_address_outside_config` 第一版写成逐行扫文本，
**把 `http_client.py` docstring 里解释默认地址的那句话判成了违例**。

这是判据本身写得太粗，不是实现有问题 —— 按 `AGENTS.md` §2 属于「前提被推翻 → 改判据」。
改成按 AST 只看**代码里的字符串字面量**，跳过 docstring：文档里提到地址是无害的，
拿 docstring 发不了请求。改完后仍用植入缺陷的方式验证它会红。

## 5. 遗留风险

| 风险 | 说明 |
|---|---|
| lock 文件未经验证 | 见 §3 第一条。真要靠它复现环境时可能装不出来 |
| `HttpComfyClient` 的错误分支未触发 | 超时、非 2xx、无 devices 三条路径只有代码，没有证据 |
| 清华镜像单点 | 它不可用时部署会失败。退路是改 `PIP_INDEX_URL` 或摘代理直连 |
| 开发机与服务器 venv 工具不同 | uv vs `python3 -m venv`。目前产出等价，但这是一处潜在分叉 |

## 6. 下一轮

**M1R2：workflow 构造器。** 纯函数，不碰网络，全部在开发机上测。
对应 VS-1〜VS-4。执行文档 §3 M1R2。
