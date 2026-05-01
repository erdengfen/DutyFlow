# 本文件标识运行时上下文子包，具体投影逻辑位于 runtime_context.py。

from dutyflow.context.runtime_context import RuntimeContextManager

__all__ = ["RuntimeContextManager"]


def _self_test() -> None:
    """验证上下文子包可被导入。"""
    assert RuntimeContextManager is not None


if __name__ == "__main__":
    _self_test()
    print("dutyflow context package self-test passed")
