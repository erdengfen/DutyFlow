# 本文件验证 Runtime Context Budget 的 token 粗估和 lane 聚合。

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
    AgentMessage,
    append_assistant_message,
    append_tool_results,
    append_user_message,
    create_initial_agent_state,
)
from dutyflow.context.context_budget import ContextBudgetEstimator, estimate_text_tokens  # noqa: E402
from dutyflow.context.runtime_context import RuntimeContextManager  # noqa: E402


class TestContextBudget(unittest.TestCase):
    """验证 ContextBudgetEstimator 的确定性输出。"""

    def test_estimate_text_tokens_handles_cjk_and_ascii(self) -> None:
        """估算器应区分 CJK 和非 CJK 字符。"""
        self.assertEqual(estimate_text_tokens("测试"), 2)
        self.assertEqual(estimate_text_tokens("abcd"), 1)
        self.assertEqual(estimate_text_tokens("测试abcd"), 3)

    def test_report_groups_messages_by_lanes(self) -> None:
        """预算报告应按 system、用户输入、工具结果和 assistant 聚合。"""
        messages = (
            AgentMessage(role="system", content=(AgentContentBlock(type="text", text="system rule"),)),
            AgentMessage(role="user", content=(AgentContentBlock(type="text", text="旧消息"),)),
            AgentMessage(
                role="assistant",
                content=(
                    AgentContentBlock(
                        type="tool_use",
                        tool_use_id="tool_1",
                        tool_name="sample_tool",
                        tool_input={"query": "张三"},
                    ),
                ),
            ),
            AgentMessage(
                role="user",
                content=(
                    AgentContentBlock(
                        type="tool_result",
                        tool_use_id="tool_1",
                        tool_name="sample_tool",
                        content="ToolReceipt(tool=sample_tool, tool_use_id=tool_1, status=success, summary=ok, ref=x)",
                    ),
                ),
            ),
            AgentMessage(role="user", content=(AgentContentBlock(type="text", text="最新问题"),)),
        )
        report = ContextBudgetEstimator(largest_item_limit=2).estimate_messages(messages)
        lanes = {item.lane: item for item in report.lane_usages}
        self.assertGreater(report.total_estimated_tokens, 0)
        self.assertEqual(report.message_count, 5)
        self.assertEqual(report.block_count, 5)
        self.assertIn("system_instructions", lanes)
        self.assertIn("latest_user_input", lanes)
        self.assertIn("tool_receipt", lanes)
        self.assertIn("assistant_context", lanes)
        self.assertIn("history", lanes)
        self.assertEqual(len(report.largest_items), 2)
        self.assertEqual(report.to_dict()["estimator_version"], "heuristic_cjk_v1")

    def test_active_tool_result_and_receipt_are_separate_lanes(self) -> None:
        """原始工具结果和 Tool Receipt 应分到不同 lane。"""
        messages = (
            AgentMessage(
                role="user",
                content=(AgentContentBlock(type="tool_result", tool_use_id="raw_1", tool_name="tool", content="raw"),),
            ),
            AgentMessage(
                role="user",
                content=(
                    AgentContentBlock(
                        type="tool_result",
                        tool_use_id="receipt_1",
                        tool_name="tool",
                        content="ToolReceipt(tool=tool, tool_use_id=receipt_1, status=success, summary=ok, ref=x)",
                    ),
                ),
            ),
        )
        report = ContextBudgetEstimator().estimate_messages(messages)
        lanes = {item.lane for item in report.lane_usages}
        self.assertIn("active_tool_result", lanes)
        self.assertIn("tool_receipt", lanes)

    def test_runtime_context_records_latest_budget_report(self) -> None:
        """RuntimeContextManager project 后应记录投影 messages 的预算报告。"""
        state = create_initial_agent_state("ctx_budget", "请处理")
        state = append_assistant_message(
            state,
            (AgentContentBlock(type="tool_use", tool_use_id="tool_1", tool_name="sample_tool"),),
        )
        state = append_tool_results(
            state,
            (
                AgentContentBlock(
                    type="tool_result",
                    tool_use_id="tool_1",
                    tool_name="sample_tool",
                    content="旧工具结果" * 100,
                ),
            ),
        )
        state = append_user_message(state, "继续")
        manager = RuntimeContextManager()
        projected = manager.project_state_for_model(state)
        self.assertIsNotNone(manager.latest_budget_report)
        assert manager.latest_budget_report is not None
        self.assertGreater(manager.latest_budget_report.total_estimated_tokens, 0)
        self.assertTrue(projected.messages[-2].content[0].content.startswith("ToolReceipt("))
        lanes = {item.lane for item in manager.latest_budget_report.lane_usages}
        self.assertIn("tool_receipt", lanes)


def _self_test() -> None:
    """运行本文件单元测试。"""
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestContextBudget)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    _self_test()
