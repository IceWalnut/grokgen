"""golden 回归：三种模式各钉一份完整的 workflow 期望值。

单元测试断言的是「我想到要检查的地方」，golden 抓的是想不到的地方 ——
某个 widget 改了值、某条连线接错槽位、多了或少了一个节点。

⚠️ **没有自动覆盖开关。** 有了它，「改坏了」和「改好了」就是同一个动作。
期望文件变化时要人工看过 diff 再手动更新，更新方式写在 `_assert_matches_golden`
失败信息里。
"""

import json
from pathlib import Path

import pytest

from app.comfy.workflows.h3_video import build_workflow
from app.models.video_job import PromptParts, VideoJobRequest, VideoMode

GOLDEN_DIR = Path(__file__).parent / "golden"

# 固定 seed，否则每次生成的图都不一样，golden 无从比对。
FIXED_SEED = 42

PROMPT = PromptParts(
    description="A red sports car drives slowly on a coastal road at sunset.",
    soundscape="Gentle ocean waves, soft wind, distant engine sound.",
    music="Warm cinematic ambient music.",
)

CASES = {
    "h3_t2va": VideoJobRequest(
        mode=VideoMode.T2VA, prompt=PROMPT, width=736, height=416,
        duration_seconds=5.0, turbo=True, seed=FIXED_SEED,
    ),
    "h3_i2va": VideoJobRequest(
        mode=VideoMode.I2VA, prompt=PROMPT, first_frame="start.png",
        width=736, height=416, duration_seconds=5.0, turbo=True, seed=FIXED_SEED,
    ),
    "h3_fl2va": VideoJobRequest(
        mode=VideoMode.FL2VA, prompt=PROMPT, first_frame="start.png",
        last_frame="end.png", width=736, height=416, duration_seconds=8.0,
        turbo=False, seed=FIXED_SEED,
    ),
}


def _assert_matches_golden(name: str, actual: dict) -> None:
    """把生成的图与期望文件逐字节比对。

    Args:
        name: 期望文件名（不含扩展名）。
        actual: 本次生成的 workflow。

    Raises:
        AssertionError: 期望文件不存在，或内容不一致。失败信息里给出更新方法。
    """
    path = GOLDEN_DIR / f"{name}.json"
    rendered = json.dumps(actual, indent=2, ensure_ascii=False, sort_keys=True) + "\n"

    if not path.exists():
        pytest.fail(
            f"期望文件不存在：{path}\n"
            f"确认下面的内容正确之后再手动创建它：\n{rendered}"
        )

    expected = path.read_text(encoding="utf-8")
    assert rendered == expected, (
        f"{path.name} 与当前实现不一致。\n"
        "⚠️ 先看懂 diff：是实现改坏了，还是期望该更新？\n"
        "确认是后者再手动改这个文件，不要写脚本批量覆盖。"
    )


@pytest.mark.parametrize("name", sorted(CASES))
def test_workflow_matches_golden(name):
    workflow, _ = build_workflow(CASES[name])
    _assert_matches_golden(name, workflow)
