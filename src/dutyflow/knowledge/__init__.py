# 本文件定义知识服务包的最小导出入口。

from __future__ import annotations


def _self_test() -> None:
    """验证知识服务包可被正常导入。"""
    assert __name__ == "dutyflow.knowledge"


if __name__ == "__main__":
    _self_test()
    print("dutyflow knowledge package self-test passed")
