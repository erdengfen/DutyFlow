# 本文件负责导出 Step 7 的任务状态存储能力。

from dutyflow.tasks.task_state import TaskRecord, TaskStore

__all__ = ["TaskRecord", "TaskStore"]


def _self_test() -> None:
    """验证 tasks 包能正常导出核心对象。"""
    assert TaskRecord is not None
    assert TaskStore is not None


if __name__ == "__main__":
    _self_test()
    print("dutyflow tasks package self-test passed")
