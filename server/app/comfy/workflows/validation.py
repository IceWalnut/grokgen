"""提交之前，拿 ComfyUI 的节点定义把 workflow 对一遍。

**为什么需要这一层。**
`POST /prompt` 没有「只校验不执行」的模式：校验一过任务就排进队列，
而 MiniMax H3 光加载模型就是分钟级的固定开销。所以「提交一次看报错」这个动作
本身不便宜 —— 一次拼写错误要付几分钟。

这个模块把「节点名写错、漏了必填输入、多给了不存在的输入、COMBO 值不在候选里」
这一整类错误提前抓出来。它需要 ComfyUI 在线（要拉 `/object_info`），
**但完全不占 GPU**。

⚠️ **它不能替代真正的提交。** 它只检查形状，检查不了语义 ——
比如两个 VAE 接反了、prompt 写错了、连线接到了错误的输出槽位，
这些形状上都是合法的。真正的判据仍然是跑出来的画面。
"""

from dataclasses import dataclass

# 节点定义里这些类型的输入是「连线」而不是「填值」，
# 图里给成 [节点id, 槽位] 是正常的，不该拿去比对 COMBO 候选。
_LINK_TYPES = {
    "MODEL", "CLIP", "VAE", "CONDITIONING", "LATENT", "IMAGE", "MASK",
    "AUDIO", "VIDEO", "NOISE", "GUIDER", "SAMPLER", "SIGMAS",
}


@dataclass(frozen=True)
class Violation:
    """一条预检违例。

    Attributes:
        node_id: 图里的节点 id（网关用的是有意义的名字，不是数字）。
        class_type: 该节点的 ComfyUI 类型名。
        problem: 出了什么问题，中文一句话。
    """

    node_id: str
    class_type: str
    problem: str

    def __str__(self) -> str:
        return f"{self.node_id}({self.class_type}): {self.problem}"


def _is_link(value) -> bool:
    """判断一个输入值是不是连线。

    ComfyUI 的 API 格式里，连线写成 `[节点id, 输出槽位]`。

    Args:
        value: 输入值。

    Returns:
        是连线则 `True`。
    """
    return isinstance(value, list) and len(value) == 2 and isinstance(value[1], int)


def check_workflow(workflow: dict, object_info: dict) -> list[Violation]:
    """把一张 workflow 对着节点定义检查一遍。

    流程说明：
        1. 节点的 `class_type` 必须在 `object_info` 里存在；
        2. 该节点声明的**必填**输入必须都给了；
        3. 图里给的输入不能是节点根本没声明的；
        4. 连线指向的节点必须存在于图里；
        5. 填的是字面量而定义是 COMBO 时，值必须在候选列表里 ——
           模型文件名写错就是在这一条被抓住的。

    Args:
        workflow: API 格式的节点图。
        object_info: `/object_info` 的响应。

    Returns:
        违例列表，空列表表示形状上没问题。**不抛异常** ——
        调用方通常想一次看到全部问题，而不是修一个跑一次。
    """
    violations: list[Violation] = []

    for node_id, node in workflow.items():
        class_type = node.get("class_type", "<缺 class_type>")
        definition = object_info.get(class_type)
        if definition is None:
            violations.append(
                Violation(node_id, class_type, "ComfyUI 里没有这个节点类型")
            )
            continue

        declared = definition.get("input", {})
        required = declared.get("required", {})
        optional = declared.get("optional", {})
        known = {**required, **optional}
        given = node.get("inputs", {})

        for name in required:
            if name not in given:
                violations.append(Violation(node_id, class_type, f"缺必填输入 `{name}`"))

        for name, value in given.items():
            spec = known.get(name)
            if spec is None:
                violations.append(
                    Violation(node_id, class_type, f"给了未声明的输入 `{name}`")
                )
                continue

            if _is_link(value):
                target = value[0]
                if target not in workflow:
                    violations.append(
                        Violation(node_id, class_type, f"输入 `{name}` 连到了不存在的节点 `{target}`")
                    )
                continue

            violations.extend(_check_literal(node_id, class_type, name, value, spec))

    return violations


def _check_literal(node_id: str, class_type: str, name: str, value, spec) -> list[Violation]:
    """检查一个字面量输入。

    只查两件事：该连线的地方填了字面量、以及 COMBO 的取值是否在候选里。
    数值范围不查 —— ComfyUI 自己会夹紧，而且那不是「拼错」类的错误。

    Args:
        node_id: 节点 id。
        class_type: 节点类型。
        name: 输入名。
        value: 图里填的值。
        spec: 节点定义里该输入的声明。

    Returns:
        违例列表，通常为空。
    """
    declared_type = spec[0] if isinstance(spec, list) and spec else None

    if isinstance(declared_type, str) and declared_type in _LINK_TYPES:
        return [
            Violation(node_id, class_type, f"输入 `{name}` 应当是连线，却填了字面量 {value!r}")
        ]

    # COMBO 有两种写法：类型位直接是候选列表，或类型位是 "COMBO" 而候选在 options 里。
    options = None
    if isinstance(declared_type, list):
        options = declared_type
    elif declared_type == "COMBO" and isinstance(spec[-1], dict):
        options = spec[-1].get("options")

    if options and value not in options:
        preview = ", ".join(repr(o) for o in list(options)[:4])
        return [
            Violation(
                node_id,
                class_type,
                f"输入 `{name}` 的值 {value!r} 不在候选里（候选 {len(options)} 个，如 {preview}）",
            )
        ]

    return []
