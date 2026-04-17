# 本文件定义工具控制层稳定公共类型导出入口，不承载执行逻辑。

from __future__ import annotations

import sys

_THIS_DIR = __file__.rsplit("/", 1)[0]
if sys.path and sys.path[0] == _THIS_DIR:
    sys.path.pop(0)

from importlib import import_module
from typing import Any

_TYPE_EXPORTS = {
    "TOOL_SOURCES",
    "ToolCall",
    "ToolResultEnvelope",
    "ToolSpec",
    "error_envelope",
}

__all__ = [
    "TOOL_SOURCES",
    "ToolCall",
    "ToolResultEnvelope",
    "ToolSpec",
    "error_envelope",
]


def __getattr__(name: str) -> Any:
    """按需导出工具协议类型，避免包导入时加载执行层。"""
    if name not in _TYPE_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(import_module("dutyflow.agent.tools.types"), name)


def _self_test() -> None:
    """验证工具控制层公共导出可用。"""
    spec_cls = __getattr__("ToolSpec")
    spec = spec_cls("placeholder_tool", "demo", source="placeholder")
    assert spec.name == "placeholder_tool"


if __name__ == "__main__":
    _self_test()
    print("dutyflow agent tools package self-test passed")
