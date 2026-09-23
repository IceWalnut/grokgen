"""视频生成任务的请求与归一化结果。

字段与 `Docs/contract/gateway_api_v0.1.md` §2 对齐 —— 那份文档是 App 与网关之间
唯一的约定，改这里必须同时改它。
"""

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# MiniMax H3 的采样参数分两套 profile，取自用户在用的 DaSiWa V23 模板里
# "Settings & Post-Processing" 那段注释（`Docs/knowledge/` 下的 JSON）。
#
# ⚠️ 两套不能混着用：8 步属于 Turbo 区间，而 res_multistep + shift 11 是非 Turbo
# 那一套的值。需求文档 §8.2 最初就混了，本轮已更正。
TURBO_PROFILE = {"sampler": "euler", "steps": 8, "shift_video": 6.0, "shift_audio": 3.0}
FULL_PROFILE = {"sampler": "res_multistep", "steps": 25, "shift_video": 11.0, "shift_audio": 3.0}

# 分辨率必须是 32 的倍数：节点声明了 step=32，latent 的空间尺寸是 height//16, width//16。
RESOLUTION_MULTIPLE = 32


class VideoMode(str, Enum):
    """生成模式。

    三种模式在 ComfyUI 那侧走的是**同一个** `MiniMaxH3ImageToVideo` 节点，
    区别只是接几张图。UI 上仍按三种呈现，因为用户心里是三件事（需求 F2）。
    """

    T2VA = "T2VA"
    I2VA = "I2VA"
    FL2VA = "FL2VA"


class PromptParts(BaseModel):
    """Director 的三段式 prompt。

    Attributes:
        description: 画面描述，对应 `integrated_multimodal_description`。
        soundscape: 环境声，对应 `overall_soundscape`。
        music: 背景音乐，对应 `non_diegetic_music`。
            ⚠️ 留空时要填 `N/A` 而不是空字符串 —— 这是 Director 的自动规则，
            绕开 Director 之后必须自己复制（V23 模板的 Quick Start 注释）。
    """

    description: str
    soundscape: str = ""
    music: str = ""


class VideoJobRequest(BaseModel):
    """App 提交的一次视频生成请求。

    ⚠️ **字段名与线上的 JSON 名不完全一致**：契约（`Docs/contract/gateway_api_v0.1.md` §2）
    里叫 `first_frame_asset_id` / `last_frame_asset_id`，这里叫 `first_frame` /
    `last_frame`。用 pydantic 的 alias 把两者对上，**线上以契约为准**，
    而代码里保留短名 —— 否则 M1R2 已经验证稳定的 workflow 构造器和它的 golden
    文件都要跟着改一遍，那是没必要的回归风险。
    `populate_by_name=True` 让两种名字都能构造，现有测试不受影响。

    Attributes:
        type: 任务类型判别位。目前只接受 `"h3_video"`。
            M4 接入 Stable Diffusion 后会有第二种取值，现在留着，
            免得那时要改一次请求体形状、两端一起动。
        mode: 生成模式。
        prompt: 三段式 prompt。
        first_frame: 首帧图在 ComfyUI `input/` 下的路径（就是上传接口返回的
            `asset_id`）；`T2VA` 时为 `None`。线上字段名是 `first_frame_asset_id`。
        last_frame: 尾帧图，只有 `FL2VA` 用。线上字段名是 `last_frame_asset_id`。
        width / height: 期望画布尺寸，像素。**可选** ——
            留空且有首帧图时，网关按图片宽高比推算画布（契约 §2）。
            **默认就应该留空**：首帧是拉伸不是裁剪，比例不符会变形且不报错。
        duration_seconds: 期望时长，秒。**会被换算成模型接受的帧数**，实际时长可能变长。
        turbo: 是否用 Turbo LoRA。决定采样 profile，见 `TURBO_PROFILE`。
        steps / seed: 留空时取 profile 默认值 / 随机。
    """

    model_config = ConfigDict(populate_by_name=True)

    type: Literal["h3_video"] = "h3_video"
    mode: VideoMode
    prompt: PromptParts
    first_frame: str | None = Field(default=None, alias="first_frame_asset_id")
    last_frame: str | None = Field(default=None, alias="last_frame_asset_id")
    width: int | None = Field(default=None, ge=32)
    height: int | None = Field(default=None, ge=32)
    duration_seconds: float = Field(default=5.0, gt=0)
    turbo: bool = True
    steps: int | None = None
    seed: int | None = None


class NormalizedParams(BaseModel):
    """归一化之后真正送去生成的参数。

    ⚠️ 这个对象必须原样回给 App。里面有三个值会与用户填的不一样
    （尺寸、实际时长、seed），不显示的话用户会以为是模型不准。

    Attributes:
        width / height: 取整到 32 的倍数之后的画布尺寸。
        length_frames: 模型接受的帧数，落在 `17k+5` 的格子上。
        actual_duration_seconds: `length_frames / fps`，即真实时长。
        seed: 实际使用的随机种子。用户没填时这里是网关生成的值。
        sampler / steps / shift_video / shift_audio: 实际采样参数。
        notices: 给用户看的中文提示，说明「你要的」和「你会得到的」差在哪。
    """

    width: int
    height: int
    length_frames: int
    actual_duration_seconds: float
    seed: int
    sampler: str
    steps: int
    shift_video: float
    shift_audio: float
    notices: list[str] = Field(default_factory=list)
