"""`HttpComfyClient` 的解析与错误翻译。

⚠️ 这个模块是分层里唯一豁免于「开发机可测」的那一个（VS-13），
但**豁免的只是"不能连真 ComfyUI"，不是"不用测"**。
这里用 `httpx.MockTransport` 喂假响应，测的是它怎么解析、怎么翻译错误 ——
那些逻辑跟有没有 GPU 无关。

M1R2 总结里记过「`HttpComfyClient` 的错误分支一条都没实际触发过」，
本轮把那一条补上。
"""

import json

import httpx
import pytest

from app.comfy.client import ComfyUnreachable, ComfyValidationError
from app.comfy.http_client import HttpComfyClient

SYSTEM_STATS = {
    "devices": [
        {
            "name": "cuda:0 NVIDIA GeForce RTX 4080 SUPER : cudaMallocAsync",
            "vram_total": 17170956288,
            "vram_free": 15647768576,
        }
    ]
}


def client_with(handler) -> HttpComfyClient:
    """造一个把请求交给 `handler` 的客户端。

    Args:
        handler: 接收 `httpx.Request`、返回 `httpx.Response` 的函数。

    Returns:
        已经换好 transport 的 `HttpComfyClient`。
    """
    client = HttpComfyClient(base_url="http://test")
    client._client = httpx.AsyncClient(
        base_url="http://test", transport=httpx.MockTransport(handler)
    )
    return client


async def test_system_stats_parses_first_device():
    client = client_with(lambda request: httpx.Response(200, json=SYSTEM_STATS))
    stats = await client.system_stats()

    assert stats.vram_total_bytes == 17170956288
    assert "4080 SUPER" in stats.name


async def test_system_stats_without_devices_is_unreachable():
    """ComfyUI 在线但没认到 GPU —— 这同样是「上游不可用」，
    不是一个可以返回零值继续走下去的情况。"""
    client = client_with(lambda request: httpx.Response(200, json={"devices": []}))

    with pytest.raises(ComfyUnreachable, match="没有 devices"):
        await client.system_stats()


async def test_connection_error_becomes_unreachable():
    def boom(request):
        raise httpx.ConnectError("connection refused", request=request)

    client = client_with(boom)
    with pytest.raises(ComfyUnreachable, match="不可达"):
        await client.system_stats()


async def test_http_500_becomes_unreachable():
    client = client_with(lambda request: httpx.Response(500, text="boom"))

    with pytest.raises(ComfyUnreachable):
        await client.system_stats()


async def test_submit_returns_prompt_id():
    client = client_with(
        lambda request: httpx.Response(200, json={"prompt_id": "abc-123", "node_errors": {}})
    )
    assert await client.submit({"a": {}}, "cid") == "abc-123"


async def test_submit_validation_error_keeps_node_errors_verbatim():
    """⚠️ 本轮最重要的一条断言。

    ComfyUI 拒绝 workflow 时返回的是结构化的 `node_errors`，
    它指明了是哪个节点的哪个字段有问题。**压成一句「生成失败」就把
    排障时唯一有用的信息丢了**（契约文档 §2 的 `failure_reason.detail`）。
    """
    body = {
        "error": {"type": "prompt_outputs_failed_validation", "message": "Prompt outputs failed validation"},
        "node_errors": {
            "2": {
                "errors": [
                    {"type": "required_input_missing", "details": "channels"}
                ],
                "class_type": "EmptyAudio",
            }
        },
    }
    client = client_with(lambda request: httpx.Response(400, json=body))

    with pytest.raises(ComfyValidationError) as caught:
        await client.submit({"a": {}}, "cid")

    # 正向断言：原文要在，而且要能定位到具体字段。
    assert caught.value.node_errors["2"]["errors"][0]["details"] == "channels"
    assert caught.value.node_errors["2"]["class_type"] == "EmptyAudio"


async def test_submit_non_json_error_is_unreachable_not_validation():
    """400 但响应不是 JSON —— 那不是 workflow 的问题，别误报成校验失败。"""
    client = client_with(lambda request: httpx.Response(502, text="<html>bad gateway</html>"))

    with pytest.raises(ComfyUnreachable):
        await client.submit({"a": {}}, "cid")


async def test_history_missing_record_returns_none():
    """任务还在排队时 ComfyUI 返回空 dict，那**不是错误**。"""
    client = client_with(lambda request: httpx.Response(200, json={}))
    assert await client.history("nope") is None


async def test_history_collects_outputs_with_node_id():
    body = {
        "pid-1": {
            "status": {"status_str": "success", "completed": True, "messages": []},
            "outputs": {
                "save_video": {
                    "images": [{"filename": "grokgen_00001_.mp4", "subfolder": "video", "type": "output"}],
                    "animated": [True],
                }
            },
        }
    }
    client = client_with(lambda request: httpx.Response(200, json=body))
    record = await client.history("pid-1")

    assert record is not None
    assert record.status == "success"
    assert record.completed is True
    assert len(record.outputs) == 1
    assert record.outputs[0].filename == "grokgen_00001_.mp4"
    assert record.outputs[0].subfolder == "video"
    assert record.outputs[0].node_id == "save_video"


async def test_history_of_failed_job_keeps_messages():
    """失败时 ComfyUI 的执行消息是唯一线索，要带出来。"""
    body = {
        "pid-1": {
            "status": {
                "status_str": "error",
                "completed": False,
                "messages": [["execution_error", {"exception_message": "OOM"}]],
            },
            "outputs": {},
        }
    }
    client = client_with(lambda request: httpx.Response(200, json=body))
    record = await client.history("pid-1")

    assert record is not None
    assert record.status == "error"
    assert record.outputs == []
    assert "OOM" in str(record.messages)


# ---- M1R5 新增：队列、中断、从队列删除 ----


async def test_queue_parses_running_and_pending_prompt_ids():
    """`/queue` 的两个列表各自取出 prompt_id。

    ⚠️ **prompt_id 在每个条目的下标 1**，条目形如
    `[number, prompt_id, prompt, extra_data, outputs]`。
    取错槽位不会报错，只会让「任务开始跑了没」这个判断永远为假 ——
    表现为任务卡在 submitted，而不是一个明确的错误。
    """
    body = {
        "queue_running": [[0, "pid-running", {}, {}, []]],
        "queue_pending": [[1, "pid-a", {}, {}, []], [2, "pid-b", {}, {}, []]],
    }
    client = client_with(lambda request: httpx.Response(200, json=body))
    state = await client.queue()

    assert state.running == ("pid-running",)
    assert state.pending == ("pid-a", "pid-b")


async def test_queue_skips_malformed_entries_instead_of_blowing_up():
    """一条畸形条目不该让整次查询变成异常。

    轮询循环拿这个结果去判断任务状态：宁可这一轮少看见一个 id，
    也不该因此把一个正常任务判成失败。
    """
    body = {
        "queue_running": [[0], "not-a-list", [1, 42, {}], [2, "pid-ok", {}, {}, []]],
        "queue_pending": [],
    }
    client = client_with(lambda request: httpx.Response(200, json=body))
    state = await client.queue()

    assert state.running == ("pid-ok",)
    assert state.pending == ()


async def test_queue_of_an_idle_comfyui_is_empty():
    """ComfyUI 空闲时两个元组都是空的，不是 None。"""
    client = client_with(
        lambda request: httpx.Response(200, json={"queue_running": [], "queue_pending": []})
    )
    state = await client.queue()

    assert state.running == ()
    assert state.pending == ()


async def test_interrupt_posts_to_the_interrupt_route():
    """中断打的是 `POST /interrupt`。"""
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        return httpx.Response(200, json={})

    client = client_with(handler)
    await client.interrupt()

    assert seen == {"method": "POST", "path": "/interrupt"}


async def test_delete_queued_sends_the_prompt_id_in_a_delete_list():
    """从队列删除用的是 `POST /queue` 带 `delete` 列表。"""
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={})

    client = client_with(handler)
    await client.delete_queued("pid-x")

    assert seen["path"] == "/queue"
    assert seen["body"] == {"delete": ["pid-x"]}


async def test_control_endpoint_failures_become_unreachable():
    """控制接口返回非 2xx 时翻译成 ComfyUnreachable，而不是静默当成功。"""
    client = client_with(lambda request: httpx.Response(500, text="boom"))

    with pytest.raises(ComfyUnreachable):
        await client.interrupt()
