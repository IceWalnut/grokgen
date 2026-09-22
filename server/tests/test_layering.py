"""VS-13：分层约束的可执行形式。

架构文档 §3 那条唯一的硬约束是：**所有对 ComfyUI 的调用都经过 comfy/ 下的接口**。

它的检验方式本来是「在开发机上、不连服务器，除 http_client 外的测试全通过」——
但那是个消极判据：有人在 core/ 里加了 httpx 调用而测试恰好没走到那条路，它照样是绿的。

所以这里直接扫源码。发现违例时报出的是**具体哪个文件的哪一行**，
而不是一个语焉不详的失败。
"""

import ast
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1] / "app"

# 唯一允许说 HTTP 的模块。
HTTP_SPEAKING_MODULES = {APP_ROOT / "comfy" / "http_client.py"}

FORBIDDEN_IMPORTS = {"httpx", "requests", "aiohttp", "urllib.request", "websockets"}


def _python_files_outside_http_layer() -> list[Path]:
    return sorted(p for p in APP_ROOT.rglob("*.py") if p not in HTTP_SPEAKING_MODULES)


def test_no_http_library_outside_the_comfy_http_client():
    """除 http_client 外，任何模块都不许 import HTTP 库。"""
    violations = []
    for path in _python_files_outside_http_layer():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                root = name.split(".")[0]
                if name in FORBIDDEN_IMPORTS or root in FORBIDDEN_IMPORTS:
                    violations.append(f"{path.relative_to(APP_ROOT.parent)}:{node.lineno} import {name}")

    assert not violations, (
        "这些文件直接说了 HTTP，绕过了 comfy/ 那层接口（架构文档 §3）：\n  "
        + "\n  ".join(violations)
    )


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """收集所有 docstring 字符串节点的 id。

    docstring 与注释一样只是文字，拿它当地址用不了 ——
    所以硬编码检查要把它们排除，否则「在文档里解释默认地址是什么」都会被判违例。

    Args:
        tree: 已解析的模块 AST。

    Returns:
        docstring 对应的 `ast.Constant` 节点的 `id()` 集合。
    """
    ids = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
            if isinstance(first.value.value, str):
                ids.add(id(first.value))
    return ids


def test_no_hardcoded_comfy_address_outside_config():
    """ComfyUI 的地址只能来自 config，不能以字面量散落在各处。

    只看**代码里的字符串字面量**，不看注释和 docstring —— 那两者拿不来发请求。
    """
    config_path = APP_ROOT / "core" / "config.py"
    violations = []
    for path in APP_ROOT.rglob("*.py"):
        if path == config_path:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        doc_ids = _docstring_nodes(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            if id(node) in doc_ids:
                continue
            if ":8188" in node.value:
                violations.append(f"{path.relative_to(APP_ROOT.parent)}:{node.lineno}")

    assert not violations, "ComfyUI 的地址被硬编码在：\n  " + "\n  ".join(violations)


def test_the_http_client_is_actually_where_we_think_it_is():
    """守住上面两个测试的前提。

    如果 http_client.py 被改名或移动，上面的豁免名单会指向一个不存在的文件，
    两个测试会**静默地变松**——豁免一个不存在的路径不会报错。
    """
    for path in HTTP_SPEAKING_MODULES:
        assert path.exists(), f"豁免名单里的 {path} 不存在，分层测试已经失去意义"
