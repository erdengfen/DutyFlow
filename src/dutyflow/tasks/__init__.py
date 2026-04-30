# 本文件负责导出 Step 7 的任务状态、调度与后台任务入口能力。

from pathlib import Path
import sys

if __package__ in {None, ""}:
    _SRC_ROOT = Path(__file__).resolve().parents[2]
    if str(_SRC_ROOT) not in sys.path:
        sys.path.insert(0, str(_SRC_ROOT))

from dutyflow.tasks.background_task_intake import (
    BackgroundTaskIntakeService,
    BackgroundTaskToolResult,
)
from dutyflow.tasks.task_scheduler import TaskDispatchItem, TaskSchedulerService
from dutyflow.tasks.task_result import TaskResultRecord, TaskResultStore
from dutyflow.tasks.task_state import TaskRecord, TaskStore

__all__ = [
    "BackgroundTaskIntakeService",
    "BackgroundTaskToolResult",
    "TaskDispatchItem",
    "TaskRecord",
    "TaskResultRecord",
    "TaskResultStore",
    "TaskSchedulerService",
    "TaskStore",
]


def _self_test() -> None:
    """验证 tasks 包能正常导出核心对象。"""
    assert BackgroundTaskIntakeService is not None
    assert BackgroundTaskToolResult is not None
    assert TaskDispatchItem is not None
    assert TaskRecord is not None
    assert TaskResultRecord is not None
    assert TaskResultStore is not None
    assert TaskSchedulerService is not None
    assert TaskStore is not None


if __name__ == "__main__":
    _self_test()
    print("dutyflow tasks package self-test passed")
