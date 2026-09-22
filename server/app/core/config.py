"""网关配置。

环境变量前缀 `GROKGEN_`，例如 `GROKGEN_COMFY_BASE_URL`。
默认值取自 `server/Docs/implementation/M1_gateway_mainline.md` §4。
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# ⚠️ 这两个 VAE 文件名是常量，不是配置项。
#
# 它们接反过，ComfyUI 会报 "MiniMax H3 VAE MISMATCH"（需求文档 §8.4）。
# 做成可配置项就意味着可以配错，而配错要等一次几分钟的生成跑完才会暴露。
H3_VIDEO_VAE = "minimax_h3_video_vae_fp16.safetensors"
H3_AUDIO_VAE = "minimax_h3_audio_vae_fp32.safetensors"


class Settings(BaseSettings):
    """网关的运行配置，可由 `GROKGEN_*` 环境变量覆盖。

    Attributes:
        comfy_base_url: ComfyUI 地址。网关与它同机，所以默认走回环地址。
        comfy_output_dir: ComfyUI 的输出根目录，媒体库要扫它。可含 `~`，
            用 `resolved_output_dir()` 取展开后的绝对路径。
        comfy_input_dir: ComfyUI 的输入目录。上传的图落在它的
            `upload_subfolder` 子目录下，读尺寸时要拼这个路径。
        comfy_timeout_seconds: 单次 ComfyUI 请求的超时，秒。
        gateway_port: 网关自己监听的端口。
        canvas_target_pixels: 按图片比例推画布时的总像素预算。
        upload_subfolder: 上传的图在 ComfyUI `input/` 下的子目录名。
        h3_unet / h3_clip / h3_turbo_lora: MiniMax H3 的模型文件名，
            构造 workflow 时填进节点。
    """

    model_config = SettingsConfigDict(env_prefix="GROKGEN_", extra="ignore")

    comfy_base_url: str = "http://127.0.0.1:8188"
    comfy_output_dir: Path = Path("~/workspace/ComfyUI/output")
    comfy_input_dir: Path = Path("~/workspace/ComfyUI/input")
    comfy_timeout_seconds: float = 2.0

    gateway_port: int = 7869

    # 按首帧图比例推画布时的总像素预算。
    # 736x416 = 306176，是 M1 实测跑通过的尺寸。
    # ⚠️ 官方 i2v 模板用的是 0.9 MP（约 1344x768），那是三倍多的像素，
    # 显存和耗时都会明显上去 —— 要调大先在服务器上实测。
    canvas_target_pixels: int = 736 * 416

    # 手机上传的图放 ComfyUI input/ 下的这个子目录。
    # 与用户自己的文件分开：LoadImage 的下拉框只列顶层文件，
    # 放子目录就不会被手机传上去的图塞满。
    upload_subfolder: str = "grokgen"

    h3_unet: str = "h3ErosMax_beta5_fp8.safetensors"
    h3_clip: str = "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
    h3_turbo_lora: str = "minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors"

    def resolved_input_dir(self) -> Path:
        """把 `comfy_input_dir` 里的 `~` 展开成绝对路径。

        Returns:
            展开后的绝对路径。
        """
        return self.comfy_input_dir.expanduser()

    def resolved_output_dir(self) -> Path:
        """把 `comfy_output_dir` 里的 `~` 展开成绝对路径。

        Returns:
            展开后的绝对路径。配置值本身不变。
        """
        return self.comfy_output_dir.expanduser()


settings = Settings()
