from pathlib import Path

from app.core import config
from app.core.config import Settings


def test_defaults():
    """默认值与执行文档 §4 一致。"""
    settings = Settings()
    assert settings.comfy_base_url == "http://127.0.0.1:8188"
    assert settings.gateway_port == 7869
    assert settings.h3_unet == "h3ErosMax_beta5_fp8.safetensors"


def test_env_prefix_overrides(monkeypatch):
    """GROKGEN_ 前缀的环境变量能覆盖配置。"""
    monkeypatch.setenv("GROKGEN_COMFY_BASE_URL", "http://127.0.0.1:9999")
    assert Settings().comfy_base_url == "http://127.0.0.1:9999"


def test_vae_names_are_constants_not_settings(monkeypatch):
    """两个 VAE 文件名必须是常量。

    它们接反过（需求 §8.4，报 "MiniMax H3 VAE MISMATCH"）。
    能配置就能配错，而配错要跑几分钟才会失败。
    """
    monkeypatch.setenv("GROKGEN_H3_VIDEO_VAE", "wrong.safetensors")
    monkeypatch.setenv("GROKGEN_H3_AUDIO_VAE", "wrong.safetensors")

    assert config.H3_VIDEO_VAE == "minimax_h3_video_vae_fp16.safetensors"
    assert config.H3_AUDIO_VAE == "minimax_h3_audio_vae_fp32.safetensors"
    assert not hasattr(Settings(), "h3_video_vae")


def test_output_dir_expands_home():
    """输出目录里的 ~ 要展开，否则媒体库会去扫一个字面量叫 ~ 的目录。"""
    settings = Settings()
    resolved = settings.resolved_output_dir()
    assert resolved.is_absolute()
    assert "~" not in str(resolved)
    assert resolved == Path.home() / "workspace/ComfyUI/output"
