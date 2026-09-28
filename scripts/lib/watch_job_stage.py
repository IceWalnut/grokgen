"""经网关提交一个文生视频任务，看 `GET /v1/jobs/{id}` 的 `stage` / `progress` 怎么变（M2R4b，VS-27 / VS-24）。

在服务器上跑（直连本机，免得开发机的代理和 tailnet 延迟掺进读数）：

    python3 watch_job_stage.py --free                       # 生产网关，先卸载模型以便看到 loading_model
    python3 watch_job_stage.py --gateway http://127.0.0.1:7870 --kill-relay-at-progress 0.375
                                                            # VS-24：采样到 3/8 时杀掉 ws 转发

每当 `(state, stage, progress)` 与上一次查询不同就打一行：`秒数  state  stage  progress`。
退出码：0 任务 `done`；1 其他终态；2 超时。
"""

import argparse
import json
import os
import signal
import sys
import time
import urllib.request

COMFY_HTTP_URL = "http://127.0.0.1:8188"
POLL_INTERVAL_SECONDS = 0.5
# 冷启动一次 5 秒视频约 90 秒（M1R7 / M2R4b 实测）；600 秒是「真的卡住了」的界线。
DEADLINE_SECONDS = 600.0
HTTP_TIMEOUT_SECONDS = 10.0

# 服务器的代理变量指向一个没有进程监听的端口（runbook §6.3），一律绕开。
_HTTP_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def request_json(url: str, payload: dict | None = None) -> dict:
    """GET（`payload` 为 `None`）或 POST 一个 JSON。空响应返回 `{}`。"""
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with _HTTP_OPENER.open(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
        body = response.read()
    return json.loads(body) if body else {}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--gateway", default="http://127.0.0.1:7869", help="网关地址")
    parser.add_argument("--free", action="store_true", help="提交前让 ComfyUI 卸载模型")
    parser.add_argument(
        "--kill-relay-at-progress", type=float, default=None,
        help="progress 达到这个值时，向 --relay-pid-file 里的进程发 SIGTERM（VS-24 的断线实验）",
    )
    parser.add_argument("--relay-pid-file", default="/tmp/grokgen_ws_relay.pid")
    args = parser.parse_args()

    if args.free:
        request_json(COMFY_HTTP_URL + "/free", {"unload_models": True, "free_memory": True})

    submitted = request_json(
        args.gateway + "/v1/jobs",
        {
            "type": "h3_video",
            "mode": "T2VA",
            "prompt": {"description": "[grokgen-m2r4b] a paper lantern floating over a night river",
                       "soundscape": "quiet river", "music": "N/A"},
            "duration_seconds": 5.0,
            "turbo": True,
        },
    )
    job_id = submitted["job_id"]
    print(f"提交 {job_id}", flush=True)

    started = time.monotonic()
    last = None
    relay_killed = False
    while time.monotonic() - started < DEADLINE_SECONDS:
        job = request_json(f"{args.gateway}/v1/jobs/{job_id}")
        current = (job["state"], job["stage"], job["progress"])
        if current != last:
            print(f"{time.monotonic() - started:7.1f}  {current[0]:<15} {str(current[1]):<15} {current[2]}", flush=True)
            last = current

        progress = job["progress"]
        if (
            args.kill_relay_at_progress is not None
            and not relay_killed
            and progress is not None
            and progress >= args.kill_relay_at_progress
        ):
            with open(args.relay_pid_file) as f:
                os.kill(int(f.read().strip()), signal.SIGTERM)
            relay_killed = True
            print(f"{time.monotonic() - started:7.1f}  —— 已杀掉 ws 转发 ——", flush=True)

        if job["state"] in ("done", "failed", "cancelled"):
            return 0 if job["state"] == "done" else 1
        time.sleep(POLL_INTERVAL_SECONDS)
    return 2


if __name__ == "__main__":
    sys.exit(main())
