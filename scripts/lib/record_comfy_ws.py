"""录下一次真实生成期间 ComfyUI 在 WebSocket 上发出的全部消息（M2R4b 第 2 步）。

用途：网关的 ws 事件处理与测试替身都要照着**真实序列**写，而不是照着文档想象（ContextPack §4.011）。
这个脚本只做三件事：连 ws、提交一张文生视频的图、把收到的每一条消息原样写进 jsonl。
**它不经过网关**，直接对 ComfyUI 说话 —— 录的是 ComfyUI 的行为，不是网关的。

在服务器上跑（要用网关的 venv，里面有 `websockets` 和网关自己的 workflow 构造器）：

    cd ~/workspace/grokgen/server
    # 脚本不在仓库里（例如拷到了 /tmp）时，加 GROKGEN_SERVER_ROOT=$PWD
    .venv/bin/python ../scripts/lib/record_comfy_ws.py --free --out /tmp/cold.jsonl
    .venv/bin/python ../scripts/lib/record_comfy_ws.py --out /tmp/warm.jsonl
    .venv/bin/python ../scripts/lib/record_comfy_ws.py --disconnect-after-progress 3 \\
        --reconnect-delay 5 --out /tmp/reconnect.jsonl

输出每行一条记录：
    {"t": 距开始的秒数, "conn": 第几条连接, "kind": "text", "data": 解析后的 JSON}
    {"t": ..., "conn": ..., "kind": "binary", "length": 字节数, "head_hex": 前 8 字节}
    {"t": ..., "conn": ..., "kind": "note", "data": 脚本自己的动作（连接、提交、断开……）}
二进制帧只记长度与开头 —— 那是采样预览图，内容本身对网关没有意义，但「会出现二进制帧」这件事有意义。
"""

import argparse
import asyncio
import json
import os
import sys
import time
import urllib.request
import uuid
from pathlib import Path

from websockets.asyncio.client import connect

# 让脚本能导入网关自己的 workflow 构造器：录的图与网关真实提交的图是同一个形状。
# 默认按仓库布局找 `server/`；脚本被拷到别处（例如服务器的 /tmp）时用环境变量指定，
# 因为服务器上的仓库检出是只读的，不在那里放临时文件。
SERVER_ROOT = Path(
    os.environ.get("GROKGEN_SERVER_ROOT") or Path(__file__).resolve().parent.parent.parent / "server"
)
sys.path.insert(0, str(SERVER_ROOT))

from app.comfy.workflows.h3_video import build_workflow  # noqa: E402
from app.models.video_job import PromptParts, VideoJobRequest, VideoMode  # noqa: E402

COMFY_HTTP_URL = "http://127.0.0.1:8188"
COMFY_WS_URL = "ws://127.0.0.1:8188/ws"

# ⚠️ 超时全部显式给数值。这台服务器的 WSL 用镜像网络，关闭端口上的连接是被丢弃的，
#    不设超时就会永远等下去（ContextPack §4.001）。
WS_OPEN_TIMEOUT_SECONDS = 5.0
HTTP_TIMEOUT_SECONDS = 30.0
# 一次 5 秒视频冷启动实测约 94 秒（M1R7），600 秒是「真的卡住了」的界线，不是预期耗时。
RECORD_DEADLINE_SECONDS = 600.0
# 预览帧可能有几百 KB，websockets 默认 1 MiB 上限够用，但放宽到 64 MiB 免得录制被它截断。
WS_MAX_MESSAGE_BYTES = 64 * 1024 * 1024

# 绕过环境里的 http_proxy：服务器的代理变量指向一个没有进程监听的端口（runbook §6.3）。
_HTTP_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def post_json(path: str, payload: dict) -> dict:
    """向 ComfyUI POST 一个 JSON，返回解析后的响应（空响应返回 `{}`）。

    Raises:
        urllib.error.URLError: 连不上或返回非 2xx。
    """
    request = urllib.request.Request(
        COMFY_HTTP_URL + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with _HTTP_OPENER.open(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
        body = response.read()
    return json.loads(body) if body else {}


def get_json(path: str) -> dict:
    """GET ComfyUI 的一个 JSON 接口。"""
    with _HTTP_OPENER.open(COMFY_HTTP_URL + path, timeout=HTTP_TIMEOUT_SECONDS) as response:
        return json.loads(response.read())


def build_recording_workflow(seed: int) -> dict:
    """拼一张 5 秒、Turbo、文生视频的图 —— 与 M1R7 冷启动测量同一档，便于对照耗时。"""
    request = VideoJobRequest(
        mode=VideoMode.T2VA,
        prompt=PromptParts(
            description="A red paper boat drifts slowly across a calm pond at sunrise.",
            soundscape="soft water ripples",
        ),
        duration_seconds=5.0,
        turbo=True,
        seed=seed,
    )
    workflow, _normalized = build_workflow(request)
    return workflow


def is_end_of_our_prompt(message: dict, prompt_id: str) -> bool:
    """这条消息是否表示「我们那个任务的执行结束了」（成功、报错、被中断都算）。"""
    kind = message.get("type")
    data = message.get("data") or {}
    if data.get("prompt_id") != prompt_id:
        return False
    if kind in {"execution_success", "execution_error", "execution_interrupted"}:
        return True
    # 旧版 ComfyUI 只用 `executing` + `node=None` 表示结束，也认它。
    return kind == "executing" and data.get("node") is None


async def record(args: argparse.Namespace) -> int:
    """连 ws → 提交 → 录到任务结束为止。

    Returns:
        进程退出码：0 录到了结束事件；1 超过 `RECORD_DEADLINE_SECONDS` 仍未结束。
    """
    client_id = args.client_id or f"grokgen-record-{uuid.uuid4().hex[:8]}"
    out = open(args.out, "w", encoding="utf-8")
    started = time.monotonic()

    def write(conn: int, kind: str, **fields) -> None:
        line = {"t": round(time.monotonic() - started, 3), "conn": conn, "kind": kind, **fields}
        out.write(json.dumps(line, ensure_ascii=False) + "\n")
        out.flush()

    if args.free:
        post_json("/free", {"unload_models": True, "free_memory": True})
        write(0, "note", data={"action": "free", "vram_after": get_json("/system_stats")["devices"][0]["vram_free"]})

    url = f"{COMFY_WS_URL}?clientId={client_id}"
    prompt_id: str | None = None
    progress_events = 0
    conn = 0
    finished = False

    while not finished:
        conn += 1
        async with connect(url, open_timeout=WS_OPEN_TIMEOUT_SECONDS, max_size=WS_MAX_MESSAGE_BYTES) as ws:
            write(conn, "note", data={"action": "connected", "client_id": client_id})
            if prompt_id is None:
                seed = args.seed if args.seed is not None else int(time.time())
                response = post_json("/prompt", {"prompt": build_recording_workflow(seed), "client_id": client_id})
                prompt_id = response["prompt_id"]
                write(conn, "note", data={"action": "submitted", "prompt_id": prompt_id, "seed": seed})

            while True:
                remaining = RECORD_DEADLINE_SECONDS - (time.monotonic() - started)
                if remaining <= 0:
                    write(conn, "note", data={"action": "deadline_exceeded"})
                    out.close()
                    return 1
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
                except asyncio.TimeoutError:
                    continue

                if isinstance(raw, bytes):
                    write(conn, "binary", length=len(raw), head_hex=raw[:8].hex())
                    continue

                message = json.loads(raw)
                write(conn, "text", data=message)

                if message.get("type") == "progress":
                    progress_events += 1
                if is_end_of_our_prompt(message, prompt_id):
                    finished = True
                    break

                # 断线实验：第 N 个 progress 之后主动关掉连接，隔一会儿用**同一个 clientId** 重连，
                # 看 ComfyUI 重连后补发什么（它会不会告诉我们当前在跑哪个节点）。
                if args.disconnect_after_progress and progress_events == args.disconnect_after_progress and conn == 1:
                    write(conn, "note", data={"action": "closing_for_reconnect_test"})
                    break

        if not finished:
            await asyncio.sleep(args.reconnect_delay)

    # 收尾：`/history` 的最终记录，便于把 ws 序列和轮询看到的结果对上。
    history = get_json(f"/history/{prompt_id}").get(prompt_id, {})
    write(conn, "note", data={"action": "history", "status": history.get("status")})
    out.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", required=True, help="jsonl 输出路径")
    parser.add_argument("--free", action="store_true", help="提交前先让 ComfyUI 卸载模型，录「模型不在显存」那种情况")
    parser.add_argument("--client-id", default=None, help="ws 的 clientId；默认随机生成")
    parser.add_argument("--seed", type=int, default=None, help="默认取当前时间戳，避免命中 ComfyUI 的结果缓存")
    parser.add_argument(
        "--disconnect-after-progress", type=int, default=0,
        help="收到第 N 个 progress 事件后主动断开 ws 再重连（0 = 不做）",
    )
    parser.add_argument("--reconnect-delay", type=float, default=5.0, help="断开后隔多少秒重连")
    return asyncio.run(record(parser.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
