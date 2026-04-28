# 本文件负责导出 Step 7 的审批记录存储能力。

from dutyflow.approval.approval_flow import ApprovalRecord, ApprovalStore

__all__ = ["ApprovalRecord", "ApprovalStore"]


def _self_test() -> None:
    """验证 approval 包能正常导出核心对象。"""
    assert ApprovalRecord is not None
    assert ApprovalStore is not None


if __name__ == "__main__":
    _self_test()
    print("dutyflow approval package self-test passed")
