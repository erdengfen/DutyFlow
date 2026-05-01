# 本文件标识运行时上下文子包，并导出投影、收据和证据存储能力。

from dutyflow.context.evidence_store import EvidenceRecord, EvidenceStore
from dutyflow.context.runtime_context import RuntimeContextManager, StateDelta, WorkingSet
from dutyflow.context.tool_receipt import ToolReceipt, ToolReceiptBuilder

__all__ = [
    "EvidenceRecord",
    "EvidenceStore",
    "RuntimeContextManager",
    "StateDelta",
    "ToolReceipt",
    "ToolReceiptBuilder",
    "WorkingSet",
]


def _self_test() -> None:
    """验证上下文子包可被导入。"""
    assert EvidenceRecord is not None
    assert EvidenceStore is not None
    assert RuntimeContextManager is not None
    assert StateDelta is not None
    assert ToolReceipt is not None
    assert ToolReceiptBuilder is not None
    assert WorkingSet is not None


if __name__ == "__main__":
    _self_test()
    print("dutyflow context package self-test passed")
