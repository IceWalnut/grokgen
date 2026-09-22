"""对着真实 ComfyUI 的预检（VS-14）。

**这一层需要 ComfyUI 在线，但不占 GPU。** 它介于 Level 2 与 Level 3 之间：
拉一次 `/object_info`，把网关拼出来的图对着真实节点定义检查形状。

为什么值得单独一层：`POST /prompt` 没有「只校验不执行」的模式 ——
校验一过任务就排队跑了，而 MiniMax H3 加载模型是分钟级的固定开销。
所以「提交一次看报错」这个动作本身不便宜，一次拼写错误要付几分钟。

默认跳过，手动触发：

    .venv/bin/python -m pytest -m integration

⚠️ 从开发机跑要保证 `GROKGEN_COMFY_BASE_URL` 指向 tailnet 地址，
且本机代理不拦截 —— `HttpComfyClient` 用的是 `trust_env=False`，
所以它自己不走代理，但 DNS 仍需可解析（runbook §6.4）。
"""

import os

import pytest

from app.comfy.http_client import HttpComfyClient
from app.comfy.workflows.h3_video import build_workflow
from app.comfy.workflows.validation import check_workflow
from app.models.video_job import PromptParts, VideoJobRequest, VideoMode

pytestmark = pytest.mark.integration

COMFY_URL = os.environ.get(
    "GROKGEN_COMFY_BASE_URL", "http://icewalnut-1060.tail22a711.ts.net:8188"
)


def t2va_request() -> VideoJobRequest:
    """一个不接图的请求 —— 预检时不依赖 `input/` 下有什么文件。"""
    return VideoJobRequest(
        mode=VideoMode.T2VA,
        prompt=PromptParts(description="A red sports car on a coastal road at sunset."),
        width=736,
        height=416,
        duration_seconds=5.0,
        turbo=True,
        seed=20260922,
    )


async def test_t2va_workflow_passes_preflight_against_real_object_info():
    """网关拼的 T2VA 图，在真实节点定义上零违例。

    这一条挡住的是：节点改名、模型文件被移走或改名、某个输入的候选值变了。
    **它不检查语义** —— 两个 VAE 接反、prompt 写错、连到错误的输出槽位，
    形状上都是合法的。真正的判据仍然是跑出来的画面（VS-7）。
    """
    client = HttpComfyClient(base_url=COMFY_URL)
    try:
        object_info = await client.object_info()
    finally:
        await client.aclose()

    workflow, _ = build_workflow(t2va_request())
    violations = check_workflow(workflow, object_info)

    assert not violations, "预检违例：\n  " + "\n  ".join(str(v) for v in violations)


async def test_configured_models_still_exist_on_the_server():
    """配置里写的模型文件名仍然存在。

    ⚠️ 这一条独立于上面那条：模型被改名时，上面那条也会红，
    但报的是「值不在候选里」；这一条直接说清是哪个模型不见了。
    启动自检将来要用的就是这套判断。
    """
    from app.core.config import H3_AUDIO_VAE, H3_VIDEO_VAE, settings

    client = HttpComfyClient(base_url=COMFY_URL)
    try:
        object_info = await client.object_info()
    finally:
        await client.aclose()

    unet_options = object_info["UNETLoader"]["input"]["required"]["unet_name"][0]
    clip_options = object_info["CLIPLoader"]["input"]["required"]["clip_name"][0]
    vae_options = object_info["VAELoader"]["input"]["required"]["vae_name"][0]
    lora_options = object_info["LoraLoaderModelOnly"]["input"]["required"]["lora_name"][0]

    missing = [
        name
        for name, options in (
            (settings.h3_unet, unet_options),
            (settings.h3_clip, clip_options),
            (H3_VIDEO_VAE, vae_options),
            (H3_AUDIO_VAE, vae_options),
            (settings.h3_turbo_lora, lora_options),
        )
        if name not in options
    ]

    assert not missing, f"这些模型在服务器上找不到了：{missing}"
