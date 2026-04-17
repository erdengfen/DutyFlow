# 本文件提供工具控制层包级自测入口。

import sys

_THIS_DIR = __file__.rsplit("/", 1)[0]
if sys.path and sys.path[0] == _THIS_DIR:
    sys.path.pop(0)

from dutyflow.agent.tools import ToolSpec


def _self_test() -> None:
    """验证工具控制层包可通过 python -m 运行。"""
    spec = ToolSpec("placeholder_tool", "demo", source="placeholder")
    assert spec.source == "placeholder"


if __name__ == "__main__":
    _self_test()
    print("dutyflow agent tools package self-test passed")
