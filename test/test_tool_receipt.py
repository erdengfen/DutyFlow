# 本文件验证 Tool Receipt 数据结构和确定性构造器。

from __future__ import annotations

from pathlib import Path
import sys
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dutyflow.agent.state import (  # noqa: E402
    AgentContentBlock,
    append_assistant_message,
    append_tool_results,
    create_initial_agent_state,
)
from dutyflow.agent.tools.types import ToolResultEnvelope  # noqa: E402
from dutyflow.context.runtime_context import RuntimeContextManager  # noqa: E402
from dutyflow.context.tool_receipt import ToolReceiptBuilder  # noqa: E402


class TestToolReceipt(unittest.TestCase):
    """验证工具结果可被收据化并保留关键锚点。"""

    def test_builder_creates_success_receipt_from_envelope(self) -> None:
        """成功工具结果应保留工具 ID、任务 ID、审批 ID 和文件路径。"""
        result = ToolResultEnvelope(
            "tool_1",
            "create_approval_request",
            True,
            (
                '{"task_id":"task_001","approval_id":"approval_001",'
                '"perception_id":"per_001","file_path":"data/approvals/approval_001.md"}'
            ),
            attachments=("data/approvals/approval_001.md",),
            context_modifiers=({"type": "permission_decision", "approval_id": "approval_002"},),
        )
        receipt = ToolReceiptBuilder().from_envelope(
            result,
            working_set=RuntimeContextManager().build_working_set(_state_with_recent_tool_result()),
        )
        self.assertEqual(receipt.status, "success")
        self.assertEqual(receipt.tool_use_id, "tool_1")
        self.assertEqual(receipt.task_id, "task_001")
        self.assertEqual(receipt.approval_ids, ("approval_001", "approval_002"))
        self.assertEqual(receipt.perception_ids, ("per_001",))
        self.assertEqual(receipt.file_paths, ("data/approvals/approval_001.md",))
        self.assertEqual(receipt.context_modifier_types, ("permission_decision",))
        self.assertTrue(receipt.impacts_current_decision)
        self.assertEqual(receipt.to_dict()["approval_ids"], ["approval_001", "approval_002"])

    def test_builder_maps_approval_waiting_error_status(self) -> None:
        """审批等待类错误应映射为 waiting_approval，方便后续压缩层保留。"""
        result = ToolResultEnvelope(
            "tool_2",
            "sample_tool",
            False,
            "waiting approval",
            is_error=True,
            error_kind="approval_waiting",
        )
        receipt = ToolReceiptBuilder().from_envelope(result)
        self.assertEqual(receipt.status, "waiting_approval")
        self.assertEqual(receipt.error_kind, "approval_waiting")
        self.assertTrue(receipt.impacts_current_decision)

    def test_builder_creates_basic_receipt_from_agent_block(self) -> None:
        """已写回 AgentState 的 tool_result block 也能生成基础收据。"""
        block = AgentContentBlock(
            type="tool_result",
            tool_use_id="tool_3",
            tool_name="sample_tool",
            content='{"event_id":"evt_001","result_file":"data/result.md"}',
        )
        receipt = ToolReceiptBuilder(summary_max_chars=80).from_agent_block(block)
        self.assertEqual(receipt.status, "success")
        self.assertEqual(receipt.event_id, "evt_001")
        self.assertEqual(receipt.file_paths, ("data/result.md",))
        self.assertIn("ToolReceipt(tool=sample_tool", receipt.to_context_text())

    def test_builder_rejects_non_tool_result_block(self) -> None:
        """构造器必须拒绝非 tool_result block，避免误收据化普通文本。"""
        block = AgentContentBlock(type="text", text="hello")
        with self.assertRaises(ValueError):
            ToolReceiptBuilder().from_agent_block(block)


def _state_with_recent_tool_result():
    """构造带最近工具结果的最小 AgentState。"""
    state = create_initial_agent_state("receipt_test", "run")
    state = append_assistant_message(
        state,
        (
            AgentContentBlock(
                type="tool_use",
                tool_use_id="tool_1",
                tool_name="create_approval_request",
                tool_input={},
            ),
        ),
    )
    return append_tool_results(
        state,
        (
            AgentContentBlock(
                type="tool_result",
                tool_use_id="tool_1",
                tool_name="create_approval_request",
                content="ok",
            ),
        ),
    )


if __name__ == "__main__":
    unittest.main()
