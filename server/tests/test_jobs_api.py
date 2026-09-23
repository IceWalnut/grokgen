"""任务四个路由的契约形状（`Docs/contract/gateway_api_v0.1.md` §2）。

队列不变量那部分判据在 `test_jobs.py` 里直接对 `JobManager` 测 ——
`TestClient` 在自己的事件循环线程里跑应用，跨线程断言状态既不好写也不可靠。
**这个文件只管「线上的字段名与状态码对不对」。**

全部用替身：`FakeComfyClient` 顶掉 ComfyUI，`FakeImageProbe` 顶掉 ffprobe。
"""

import pytest
from fastapi.testclient import TestClient

from app.comfy.fake_client import FakeComfyClient
from app.core.jobs import JobManager
from app.main import create_app
from app.media.probe import FakeImageProbe, ImageSize

# 契约 §2 的请求体。⚠️ 这里刻意用**原始 JSON**发请求而不是构造 pydantic 对象 ——
# `first_frame_asset_id` 这类线上名字是靠别名映射的，
# 别名写错时只有走 JSON 才会暴露。
T2VA_PAYLOAD = {
    "type": "h3_video",
    "mode": "T2VA",
    "prompt": {"description": "a cat walking on a table"},
    "duration_seconds": 5,
}


def make_client(comfy=None, probe=None):
    """造一个全替身的测试客户端。

    Returns:
        `(TestClient, FakeComfyClient)`。任务管理器的轮询间隔设 0、关掉预检 ——
        预检要拉 `/object_info`，那不是这个文件要测的东西。
    """
    comfy = comfy or FakeComfyClient(gated=True)
    probe = probe or FakeImageProbe(ImageSize(1472, 832))
    manager = JobManager(comfy, poll_interval_seconds=0, preflight=False)
    return TestClient(create_app(comfy, probe, manager)), comfy


def test_submitting_a_job_returns_queued_with_the_normalized_parameters():
    """提交后立刻返回 queued，并带回归一化结果。

    ⚠️ `normalized` 不是装饰：尺寸、实际时长、seed 三个值都可能和用户填的不一样，
    不回报的话用户会以为模型不准（契约 §2）。
    """
    client, _ = make_client()
    with client:
        response = client.post("/v1/jobs", json=T2VA_PAYLOAD)

    assert response.status_code == 200
    body = response.json()
    assert body["job_id"].startswith("job_")
    assert body["state"] == "queued"

    # 契约要求的九个字段一个都不能少。
    normalized = body["normalized"]
    for key in (
        "width",
        "height",
        "length_frames",
        "actual_duration_seconds",
        "seed",
        "sampler",
        "steps",
        "shift_video",
        "shift_audio",
    ):
        assert key in normalized, f"normalized 少了 {key}"

    # 用户填 5 秒，模型只接受 17k+5 的帧数，所以实际会变长 —— 这必须被说明。
    assert normalized["length_frames"] == 124
    assert normalized["actual_duration_seconds"] == pytest.approx(5.17, abs=0.01)
    assert any("时长已调整" in n for n in body["notices"])


def test_the_wire_field_for_the_first_frame_is_the_contract_name():
    """线上用的是 `first_frame_asset_id`，不是内部的 `first_frame`。

    ⚠️ 这条只有走原始 JSON 才测得到。别名映射断了的话，
    传上来的首帧图会被**静默忽略**，任务照样成功 ——
    只是悄悄变成了一个 T2VA（M1R4 记过的那类事故）。
    """
    client, _ = make_client()
    payload = {
        **T2VA_PAYLOAD,
        "mode": "I2VA",
        "first_frame_asset_id": "grokgen/img_test.png",
    }
    with client:
        response = client.post("/v1/jobs", json=payload)
        assert response.status_code == 200
        job_id = response.json()["job_id"]

        detail = client.get(f"/v1/jobs/{job_id}").json()

    # 首帧图是 1472x832，画布应当按它的比例推出来，而不是用默认值。
    assert detail["normalized"]["width"] / detail["normalized"]["height"] == pytest.approx(
        1472 / 832, abs=0.02
    )


def test_an_unknown_job_type_is_rejected():
    """`type` 只接受 h3_video —— 它是 M4 接 SD 时的判别位，现在就要守住。"""
    client, _ = make_client()
    with client:
        response = client.post("/v1/jobs", json={**T2VA_PAYLOAD, "type": "sd_image"})
    assert response.status_code == 422


def test_getting_an_unknown_job_is_404():
    """查一个不存在的任务返回 404。"""
    client, _ = make_client()
    with client:
        response = client.get("/v1/jobs/job_20260101_9999")
    assert response.status_code == 404


def test_the_job_detail_has_the_contract_shape():
    """详情的字段形状与契约一致，且 M1 阶段 stage/progress 明确为 null。"""
    client, _ = make_client()
    with client:
        job_id = client.post("/v1/jobs", json=T2VA_PAYLOAD).json()["job_id"]
        body = client.get(f"/v1/jobs/{job_id}").json()

    assert body["job_id"] == job_id
    assert body["outputs"] == []
    assert body["failure_reason"] is None
    assert "created_at" in body
    # ⚠️ M1 没有 WebSocket，这两个字段拿不到值。
    # 断言它们是 null 而不是被填了一个编造的值。
    assert body["stage"] is None
    assert body["progress"] is None


def test_listing_jobs_wraps_items_and_reports_the_untruncated_total():
    """列表外面套一层 items，并回报截断前的总数。

    ⚠️ `total` 不能省：没有它 App 不知道列表有没有被 limit 截掉。
    """
    client, _ = make_client()
    with client:
        for _ in range(3):
            client.post("/v1/jobs", json=T2VA_PAYLOAD)
        body = client.get("/v1/jobs?limit=2").json()

    assert len(body["items"]) == 2
    assert body["total"] == 3
    # 倒序：先看到最新提交的那个。
    assert body["items"][0]["job_id"] > body["items"][1]["job_id"]


def test_listing_can_filter_by_state():
    """按状态过滤 —— 队列页要分开显示「在跑的」和「排队的」。"""
    client, _ = make_client()
    with client:
        client.post("/v1/jobs", json=T2VA_PAYLOAD)
        body = client.get("/v1/jobs?state=done").json()

    assert body["items"] == []
    assert body["total"] == 0


def test_an_out_of_range_limit_is_rejected():
    """limit 有上限，不允许一次把全部任务拉下来。"""
    client, _ = make_client()
    with client:
        assert client.get("/v1/jobs?limit=0").status_code == 422
        assert client.get("/v1/jobs?limit=99999").status_code == 422


def test_cancelling_a_finished_job_returns_409():
    """已经结束的任务不能取消，契约明写 409。"""
    client, comfy = make_client()
    with client:
        job_id = client.post("/v1/jobs", json=T2VA_PAYLOAD).json()["job_id"]

        # 等它被提交出去，然后让它成功结束。
        for _ in range(200):
            detail = client.get(f"/v1/jobs/{job_id}").json()
            if detail["state"] in ("submitted", "running"):
                break
        comfy.complete(comfy.prompt_ids[0])

        for _ in range(200):
            detail = client.get(f"/v1/jobs/{job_id}").json()
            if detail["state"] == "done":
                break
        assert detail["state"] == "done"

        response = client.post(f"/v1/jobs/{job_id}/cancel")

    assert response.status_code == 409


def test_cancelling_an_unknown_job_returns_404():
    """取消一个不存在的任务返回 404。"""
    client, _ = make_client()
    with client:
        response = client.post("/v1/jobs/job_20260101_9999/cancel")
    assert response.status_code == 404


def test_a_rejected_workflow_surfaces_the_node_errors_over_http():
    """VS-10 在 HTTP 这一侧的形状：node_errors 原文要出现在响应里。

    ⚠️ App 可以折叠显示它，但不能丢 —— 排障时只有它有用（契约 §2）。
    """
    node_errors = {"2": {"errors": [{"type": "required_input_missing", "details": "channels"}]}}
    comfy = FakeComfyClient(gated=True, reject_with=node_errors)
    client, _ = make_client(comfy=comfy)

    with client:
        job_id = client.post("/v1/jobs", json=T2VA_PAYLOAD).json()["job_id"]
        for _ in range(200):
            body = client.get(f"/v1/jobs/{job_id}").json()
            if body["state"] == "failed":
                break

    assert body["state"] == "failed"
    assert body["failure_reason"]["kind"] == "comfy_validation"
    assert body["failure_reason"]["detail"] == node_errors


def test_a_non_empty_loras_field_is_refused_instead_of_silently_dropped():
    """传 LoRA 要被明确拒绝，不能静默丢掉。

    ⚠️ **这条守的是契约与实现之间的空档。**

    契约里已经写了 `loras` 字段（M6 才实现），而 pydantic 默认会把没声明的
    字段直接丢掉。实测过：不加这道守卫时，传一个非空的 `loras` 进来，
    请求照样通过、任务照样成功、视频照样生成，**只是没有 LoRA 效果**，
    而且哪里都不报错 —— 用户拿到的是一个「成功」的错结果。

    与 seed 算两次、`completed` 当成「结束了」用是同一类问题：
    **不报错的错误**。处置方式也一样，让它响亮地失败。
    """
    client, _ = make_client()
    payload = {**T2VA_PAYLOAD, "loras": [{"name": "whatever.safetensors", "strength": 0.8}]}
    with client:
        response = client.post("/v1/jobs", json=payload)

    assert response.status_code == 422
    # 正向断言：错误信息里要说清楚为什么，不是一句干巴巴的「校验失败」。
    assert "M6" in response.text


def test_an_empty_loras_field_is_accepted():
    """空数组要照常通过 —— App 传一个空列表是完全正常的。"""
    client, _ = make_client()
    with client:
        response = client.post("/v1/jobs", json={**T2VA_PAYLOAD, "loras": []})

    assert response.status_code == 200
