"""`HttpComfyClient.events` 的真实实现：超时、断线、坏消息、代理（VS-25）。

⚠️ **这里测的是真实实现本身，不是替身**（ContextPack §4.011 的做法）。
连的是本机上临时起的几个服务端，各自演一种 ComfyUI 真会出现、或这台服务器的网络真会造成的情形：

| 服务端 | 演的是 |
|---|---|
| 收下 TCP 连接、永远不回握手 | WSL 镜像网络下「包被丢掉」时连接方看到的样子（ContextPack §4.001） |
| 握手成功、发一条消息、之后再也不读不写 | 连接半死：对方没发关闭，但也不再回应 |
| 正常的 ws 服务端，发一串好坏混杂的消息后正常关闭 | ComfyUI 重启、或发来插件的怪消息 |
| 没有任何进程监听的端口 | ComfyUI 没跑 |

超时都缩到零点几秒；断言的是「在设定的时限附近放弃」，**不是「最终放弃了」** ——
后者在超时被设成「不设」时照样能通过（只要测试自己的超时够长），那正是 M2R1 撞到的坑。
"""

import asyncio
import base64
import hashlib
import json
import time

import pytest
from websockets.asyncio.server import serve

from app.comfy.client import ComfyUnreachable
from app.comfy.http_client import HttpComfyClient

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


async def drain(client: HttpComfyClient, client_id: str = "grokgen-test") -> tuple[list, BaseException]:
    """把一条连接的事件全部读出来，直到它抛异常结束。

    Returns:
        `(events, 结束时抛出的异常)`。
    """
    events = []
    try:
        async for event in client.events(client_id):
            events.append(event)
    except BaseException as exc:  # noqa: BLE001 —— 测试要拿到异常本身做断言
        return events, exc
    raise AssertionError("events() 正常结束了 —— 约定是连接一断就抛 ComfyUnreachable")


async def test_handshake_that_never_answers_gives_up_within_the_open_timeout():
    """VS-25：TCP 收下了、握手永远不回 ⇒ 在 `ws_open_timeout` 附近放弃，而不是挂住。"""
    server = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    client = HttpComfyClient(ws_url=f"ws://127.0.0.1:{port}/ws", ws_open_timeout=0.3)

    started = time.monotonic()
    events, exc = await asyncio.wait_for(drain(client), timeout=10)
    elapsed = time.monotonic() - started

    assert isinstance(exc, ComfyUnreachable), repr(exc)
    assert events == []
    assert 0.25 <= elapsed < 2.0, f"握手超时设的是 0.3 秒，实际等了 {elapsed:.2f} 秒"
    server.close()


async def _half_dead_server(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """手写 ws 握手，发一条文本帧，然后**再也不读不写**（不回心跳）。

    用手写而不是 websockets 的服务端：后者会自动回应心跳，演不出「连接半死」。
    """
    request = await reader.readuntil(b"\r\n\r\n")
    key = next(
        line.split(b":", 1)[1].strip()
        for line in request.split(b"\r\n")
        if line.lower().startswith(b"sec-websocket-key:")
    )
    accept = base64.b64encode(hashlib.sha1(key + WS_GUID.encode()).digest())
    writer.write(
        b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
        b"Sec-WebSocket-Accept: " + accept + b"\r\n\r\n"
    )
    payload = json.dumps({"type": "status", "data": {"status": {}}}).encode()
    writer.write(bytes([0x81, len(payload)]) + payload)  # FIN + 文本帧，服务端帧不加掩码
    await writer.drain()
    await asyncio.sleep(3600)


async def test_half_dead_connection_is_detected_by_the_heartbeat():
    """VS-25：握手成功、之后对方不再回应 ⇒ 心跳超时后抛 `ComfyUnreachable`。

    没有心跳的话，这种连接会被当成「还连着、只是暂时没事件」永远挂下去 ——
    而进度就一直停在断开前的最后一个值。
    """
    server = await asyncio.start_server(_half_dead_server, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    client = HttpComfyClient(
        ws_url=f"ws://127.0.0.1:{port}/ws", ws_ping_interval=0.2, ws_ping_timeout=0.2
    )

    started = time.monotonic()
    events, exc = await asyncio.wait_for(drain(client), timeout=15)
    elapsed = time.monotonic() - started

    assert [e.type for e in events] == ["status"], "握手后那条消息没收到 —— 测的就不是「连上之后半死」了"
    assert isinstance(exc, ComfyUnreachable), repr(exc)
    # 心跳 0.2 + 0.2 秒；之后 websockets 还要等关闭确认（close_timeout，2 秒）。
    assert elapsed < 5.0, f"心跳设的是 0.2 + 0.2 秒，实际 {elapsed:.2f} 秒才发现连接已死"
    server.close()


async def test_bad_messages_are_skipped_and_a_normal_close_still_raises():
    """VS-25 / VS-26：二进制帧、坏 JSON、形状不对的消息被跳过而不断开连接；
    对方正常关闭也抛 `ComfyUnreachable`（对调用方而言同样是「进度暂时没了」）。"""
    received_paths = []

    async def handler(ws):
        received_paths.append(ws.request.path)
        await ws.send(json.dumps({"type": "executing", "data": {"node": "sampler", "prompt_id": "p"}}))
        await ws.send(b"\x00\x00\x00\x01preview-jpeg-bytes")  # 采样预览图
        await ws.send("not json")
        await ws.send(json.dumps(["a", "list"]))
        await ws.send(json.dumps({"type": "progress", "data": "not-a-dict"}))
        await ws.close()

    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        client = HttpComfyClient(ws_url=f"ws://127.0.0.1:{port}/ws")
        events, exc = await asyncio.wait_for(drain(client, client_id="grokgen-abc"), timeout=10)

    assert [(e.type, e.data) for e in events] == [
        ("executing", {"node": "sampler", "prompt_id": "p"}),
        ("progress", {}),
    ]
    assert isinstance(exc, ComfyUnreachable), repr(exc)
    assert received_paths == ["/ws?clientId=grokgen-abc"]


async def test_nothing_listening_is_unreachable():
    """VS-25：ComfyUI 没跑 ⇒ `ComfyUnreachable`。

    ⚠️ 开发机上这是秒拒（ECONNREFUSED）；那台服务器上是包被丢掉，走的是第一条测试那条路。
    """
    probe = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
    port = probe.sockets[0].getsockname()[1]
    probe.close()
    await probe.wait_closed()
    client = HttpComfyClient(ws_url=f"ws://127.0.0.1:{port}/ws", ws_open_timeout=0.5)

    events, exc = await asyncio.wait_for(drain(client), timeout=10)

    assert isinstance(exc, ComfyUnreachable), repr(exc)
    assert events == []


async def test_proxy_environment_variables_are_ignored(monkeypatch):
    """VS-25：环境里的代理变量不影响 ws。

    websockets 默认会读 `http_proxy`。服务器上那个代理端口没有进程监听（runbook §6.3），
    开发机的 `no_proxy` 不含 `127.0.0.1`。这里把代理指向一个黑洞端口：
    客户端若走了代理，就连不上本机这个好好的服务端。
    """
    black_hole = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
    proxy_port = black_hole.sockets[0].getsockname()[1]
    for name in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
        monkeypatch.setenv(name, f"http://127.0.0.1:{proxy_port}")
    for name in ("no_proxy", "NO_PROXY"):
        monkeypatch.setenv(name, "")

    async def handler(ws):
        await ws.send(json.dumps({"type": "status", "data": {}}))
        await ws.close()

    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        client = HttpComfyClient(ws_url=f"ws://127.0.0.1:{port}/ws", ws_open_timeout=1.0)
        events, exc = await asyncio.wait_for(drain(client), timeout=10)

    assert [e.type for e in events] == ["status"], f"没收到消息，结束于 {exc!r} —— 大概是走了代理"
    black_hole.close()


def test_ws_url_is_derived_from_the_http_base_url():
    from app.core.config import Settings

    assert Settings(comfy_base_url="http://127.0.0.1:8188").resolved_comfy_ws_url() == "ws://127.0.0.1:8188/ws"
    assert Settings(comfy_base_url="https://h:1/").resolved_comfy_ws_url() == "wss://h:1/ws"
    assert Settings(comfy_ws_url="ws://relay:9/ws").resolved_comfy_ws_url() == "ws://relay:9/ws"
    with pytest.raises(ValueError):
        Settings(comfy_base_url="ftp://x").resolved_comfy_ws_url()
