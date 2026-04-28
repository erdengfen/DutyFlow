# 本文件负责导出 Step 7 的审批记录与任务中断记录存储能力。

from dutyflow.approval.approval_flow import ApprovalRecord, ApprovalStore
from dutyflow.approval.task_interrupt import TaskInterruptRecord, TaskInterruptStore

__all__ = ["ApprovalRecord", "ApprovalStore", "TaskInterruptRecord", "TaskInterruptStore"]


def _self_test() -> None:
    """验证 approval 包能正常导出核心对象。"""
    assert ApprovalRecord is not None
    assert ApprovalStore is not None
    assert TaskInterruptRecord is not None
    assert TaskInterruptStore is not None


if __name__ == "__main__":
    _self_test()
    print("dutyflow approval package self-test passed")
