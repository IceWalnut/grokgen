"""预检模块的判据（VS-14）。

预检回答的是「ComfyUI 会不会因为形状问题拒绝这张图」，
而它不占 GPU —— 这是它存在的全部理由：
`POST /prompt` 没有「只校验不执行」的模式，校验一过任务就排队跑了，
而 MiniMax H3 光加载模型就是分钟级的开销。

这里用手写的最小 `object_info` 而不是真实响应：真实响应两三兆，
而且里面的模型文件列表会随机器变化，拿它当断言基准是不稳的。
"""

from app.comfy.workflows.validation import check_workflow

# 一份够用的最小节点定义，形状与 ComfyUI 的 /object_info 一致。
OBJECT_INFO = {
    "UNETLoader": {
        "input": {
            "required": {
                "unet_name": [["good.safetensors", "other.safetensors"], {}],
                "weight_dtype": [["default", "fp8_e4m3fn"], {}],
            }
        }
    },
    "MiniMaxH3SigmaShift": {
        "input": {
            "required": {
                "model": ["MODEL", {}],
                "shift_video": ["FLOAT", {"default": 12.0}],
                "shift_audio": ["FLOAT", {"default": 3.0}],
            }
        }
    },
    "SaveVideo": {
        "input": {
            "required": {"video": ["VIDEO", {}], "filename_prefix": ["STRING", {}]},
            "optional": {"codec": ["COMBO", {"options": ["auto", "h264", "av1"]}]},
        }
    },
}


def minimal_graph() -> dict:
    """一张形状完全正确的小图，各测试在它基础上植入问题。"""
    return {
        "unet": {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": "good.safetensors", "weight_dtype": "default"},
        },
        "shift": {
            "class_type": "MiniMaxH3SigmaShift",
            "inputs": {"model": ["unet", 0], "shift_video": 6.0, "shift_audio": 3.0},
        },
    }


def test_clean_graph_has_no_violations():
    """正向判据：好图必须是零违例。

    ⚠️ 没有这一条，下面每个测试都可能因为「预检永远报错」而假通过。
    """
    assert check_workflow(minimal_graph(), OBJECT_INFO) == []


def test_unknown_node_type_is_reported():
    graph = minimal_graph()
    graph["typo"] = {"class_type": "MiniMaxH3SigmaShif", "inputs": {}}

    problems = [str(v) for v in check_workflow(graph, OBJECT_INFO)]
    assert any("没有这个节点类型" in p and "typo" in p for p in problems)


def test_missing_required_input_is_reported():
    graph = minimal_graph()
    del graph["shift"]["inputs"]["shift_audio"]

    problems = [str(v) for v in check_workflow(graph, OBJECT_INFO)]
    assert any("缺必填输入" in p and "shift_audio" in p for p in problems)


def test_undeclared_input_is_reported():
    """多给一个节点没声明的输入 —— 改节点版本后最常见的错法。"""
    graph = minimal_graph()
    graph["shift"]["inputs"]["shift_everything"] = 1.0

    problems = [str(v) for v in check_workflow(graph, OBJECT_INFO)]
    assert any("未声明的输入" in p and "shift_everything" in p for p in problems)


def test_link_to_missing_node_is_reported():
    graph = minimal_graph()
    graph["shift"]["inputs"]["model"] = ["does_not_exist", 0]

    problems = [str(v) for v in check_workflow(graph, OBJECT_INFO)]
    assert any("不存在的节点" in p for p in problems)


def test_combo_value_not_in_options_is_reported():
    """模型文件名写错就是在这一条被抓住的。"""
    graph = minimal_graph()
    graph["unet"]["inputs"]["unet_name"] = "typo.safetensors"

    problems = [str(v) for v in check_workflow(graph, OBJECT_INFO)]
    assert any("不在候选里" in p and "typo.safetensors" in p for p in problems)


def test_combo_with_options_dict_form_is_checked():
    """COMBO 有两种写法，`options` 在末尾 dict 里的那种也要认。"""
    graph = minimal_graph()
    graph["save"] = {
        "class_type": "SaveVideo",
        "inputs": {"video": ["shift", 0], "filename_prefix": "x", "codec": "h265"},
    }

    problems = [str(v) for v in check_workflow(graph, OBJECT_INFO)]
    assert any("不在候选里" in p and "h265" in p for p in problems)


def test_literal_where_a_link_is_required_is_reported():
    """该连线的地方填了字面量。"""
    graph = minimal_graph()
    graph["shift"]["inputs"]["model"] = "unet"

    problems = [str(v) for v in check_workflow(graph, OBJECT_INFO)]
    assert any("应当是连线" in p for p in problems)


def test_all_violations_are_collected_not_just_the_first():
    """一次报全部问题，而不是修一个跑一次。"""
    graph = minimal_graph()
    graph["unet"]["inputs"]["unet_name"] = "typo.safetensors"
    del graph["shift"]["inputs"]["shift_audio"]

    assert len(check_workflow(graph, OBJECT_INFO)) >= 2
