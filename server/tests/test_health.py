from fastapi.testclient import TestClient

from app.comfy.fake_client import DEFAULT_STATS, FakeComfyClient
from app.main import create_app


def test_health_reports_gpu_when_comfy_reachable():
    """ComfyUI 可达时，health 要把显存读数带出来。"""
    with TestClient(create_app(FakeComfyClient())) as client:
        response = client.get("/v1/health")

    assert response.status_code == 200
    body = response.json()
    assert body["gateway"]["status"] == "ok"
    assert body["comfy"]["reachable"] is True
    assert body["comfy"]["vram_total_bytes"] == DEFAULT_STATS.vram_total_bytes


def test_health_stays_200_and_carries_reason_when_comfy_is_down():
    """ComfyUI 挂了不等于网关挂了。

    这两种情况 App 要分别提示（需求 F1），所以不能都变成一个失败的 HTTP 状态码。
    """
    client_stub = FakeComfyClient(unreachable_reason="connection refused")
    with TestClient(create_app(client_stub)) as client:
        response = client.get("/v1/health")

    assert response.status_code == 200
    body = response.json()
    assert body["gateway"]["status"] == "ok"
    assert body["comfy"]["reachable"] is False
    # 正向断言：原因要带回来，不能只是 reachable=false。
    assert "connection refused" in body["comfy"]["error"]
