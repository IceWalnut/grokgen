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
from app.core.config import settings

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


# ---------------------------------------------------------------------------
# 超时必须真的生效（M2R1 实测发现的 bug）
# ---------------------------------------------------------------------------


def client_keeping_timeout(handler) -> HttpComfyClient:
    """和 `client_with` 一样换 transport，**但保留真实的超时设置**。

    `client_with` 造的客户端没设 timeout，所以用它测不出超时被覆盖的问题。
    """
    client = HttpComfyClient(base_url="http://test")
    client._client = httpx.AsyncClient(
        base_url="http://test",
        timeout=settings.comfy_timeout_seconds,
        transport=httpx.MockTransport(handler),
    )
    return client


def _timeout_of(seen: dict) -> dict:
    assert "timeout" in seen, "没有抓到请求"
    assert seen["timeout"] is not None, (
        "请求带的超时是 None —— httpx 里那表示**不设超时**，"
        "不是「用客户端默认值」。ComfyUI 挂掉时这会让请求永远等下去。"
    )
    return seen["timeout"]


async def test_system_stats_keeps_the_client_timeout():
    """`/v1/health` 那条路上的 GET 必须带着客户端那 2 秒超时出去。

    ⚠️ **这条测试是 M2R1 在真机验证时踩出来的，不是设计出来的。**
    当时停掉 ComfyUI、网关留着，期望 health 在 2 秒内返回
    `comfy.reachable = false`；实际是**永远不返回**。

    根因：`_get_json` 把调用方的 `timeout=None` 原样传给 httpx，
    而 httpx 里请求级的 `None` 意思是「不设超时」，会覆盖掉构造器里那 2 秒。

    ⚠️ **为什么以前一直没暴露**：连本机的关闭端口正常会立刻收到 ECONNREFUSED，
    有没有超时都一样快。但服务器的 WSL 用镜像网络，
    **关闭端口上的连接是被丢弃而不是被拒绝**，于是「没有超时」变成真的永远等。

    ⚠️ **为什么离线测试没抓到**：`FakeComfyClient` 是立刻抛异常的，
    替身根本走不到超时那条路 —— 和 M1R7 那个 `completed` 是同一类问题。
    """
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["timeout"] = request.extensions.get("timeout")
        return httpx.Response(200, json=SYSTEM_STATS)

    client = client_keeping_timeout(handler)
    await client.system_stats()

    assert _timeout_of(seen)["connect"] == settings.comfy_timeout_seconds


async def test_queue_keeps_the_client_timeout():
    """`queue()` 同样不许丢超时 —— 它比 health 那条更危险。

    它跑在任务轮询循环里：ComfyUI 中途挂掉而这个请求没有超时的话，
    **网关会永远轮询下去**，任务永远等不到终态。
    这正是 M1R7 修过的那种「无限轮询」，只是换了一条进去的路。
    """
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["timeout"] = request.extensions.get("timeout")
        return httpx.Response(200, json={"queue_running": [], "queue_pending": []})

    client = client_keeping_timeout(handler)
    await client.queue()

    assert _timeout_of(seen)["connect"] == settings.comfy_timeout_seconds


async def test_slow_endpoints_get_a_long_read_timeout():
    """慢接口（拉 object_info、查 history、提交、上传）要能读久一点。

    ⚠️ **这条测试原本断言的是「连接超时也跟着放宽」，那个前提被 M2R2 实测推翻了。**
    见下面 `test_slow_endpoints_keep_a_short_connect_timeout`：
    连接超时跟着放宽会导致一次误诊。现在只断言**读**超时被放宽。
    """
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["timeout"] = request.extensions.get("timeout")
        return httpx.Response(200, json={})

    client = client_keeping_timeout(handler)
    await client.object_info()

    read = _timeout_of(seen)["read"]
    assert read > settings.comfy_timeout_seconds, (
        f"object_info 的读超时应当放宽，实际是 {read}"
    )


async def test_slow_endpoints_keep_a_short_connect_timeout():
    """⭐ 慢接口只放宽**读**超时，**连接**超时必须仍然很短。

    ⚠️ M2R2 真机实测踩到的：`timeout=LONG_TIMEOUT_SECONDS` 在 httpx 里是一个标量，
    会**同时**设成连接、读、写、池四个超时。而 ComfyUI 就在本机 ——
    连本机端口从来不该要 60 秒。

    后果是一次**误诊**：ComfyUI 停掉时上传一张图，网关要等满 60 秒才返回 502，
    而 App 的读超时也是 60 秒，于是客户端先一步超时 ——
    用户看到「服务器没有应答」，而实际上网关好好的、挂掉的是 ComfyUI。

    ⚠️ 这个坑在别的机器上未必出现（连关闭端口正常会秒拒），
    是这台服务器的 WSL 镜像网络**丢包而不是拒绝**才让它暴露出来。
    """
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["timeout"] = request.extensions.get("timeout")
        return httpx.Response(200, json={})

    client = client_keeping_timeout(handler)
    await client.object_info()

    timeout = _timeout_of(seen)
    assert timeout["connect"] == settings.comfy_timeout_seconds, (
        f"连接超时应当是 {settings.comfy_timeout_seconds} 秒，实际 {timeout['connect']} —— "
        "连本机端口不该等那么久"
    )
    assert timeout["read"] > settings.comfy_timeout_seconds, (
        f"读超时应当放宽，实际 {timeout['read']}"
    )
