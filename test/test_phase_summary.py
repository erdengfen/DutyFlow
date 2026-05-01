from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dutyflow.agent.core_loop import AgentLoop  # noqa: E402
from dutyflow.agent.model_client import ModelResponse  # noqa: E402
from dutyflow.agent.state import (  # noqa: E402
    AgentContentBlock,
    AgentMessage,
    append_assistant_message,
    append_tool_results,
    append_user_message,
    create_initial_agent_state,
)
from dutyflow.agent.tools.registry import ToolRegistry  # noqa: E402
from dutyflow.context.context_budget import ContextBudgetEstimator  # noqa: E402
from dutyflow.context.phase_summary import (  # noqa: E402
    PhaseSummaryPolicy,
    PhaseSummaryService,
    PhaseSummaryStore,
)
from dutyflow.context.runtime_context import RuntimeContextManager  # noqa: E402


class TestPhaseSummary(unittest.TestCase):
    """验证 Step 8 阶段摘要触发和 AgentLoop 接入。"""

    def test_phase_boundary_below_soft_limit_records_only(self) -> None:
        """普通阶段边界低于软阈值时只记录边界，不调用 LLM。"""
        state = create_initial_agent_state("query_phase", "hello")
        manager = RuntimeContextManager()
        messages = manager.project(state)
        budget = ContextBudgetEstimator().estimate_messages(messages)
        trigger = PhaseSummaryPolicy(soft_token_limit=9999, hard_token_limit=10000).evaluate(
            state=state,
            working_set=manager.latest_working_set,
            delta=manager.latest_state_delta,
            budget=budget,
        )
        self.assertEqual(trigger.reason, "phase_boundary_only")
        self.assertFalse(trigger.requires_llm)
        self.assertTrue(trigger.should_record_boundary)

    def test_phase_boundary_with_soft_limit_triggers_llm(self) -> None:
        """阶段边界且预算达到软阈值时触发 LLM 阶段摘要。"""
        state = create_initial_agent_state("query_phase", "hello")
        manager = RuntimeContextManager()
        messages = manager.project(state)
        budget = ContextBudgetEstimator().estimate_messages(messages)
        trigger = PhaseSummaryPolicy(soft_token_limit=1, hard_token_limit=10000).evaluate(
            state=state,
            working_set=manager.latest_working_set,
            delta=manager.latest_state_delta,
            budget=budget,
        )
        self.assertEqual(trigger.reason, "phase_boundary_budget")
        self.assertTrue(trigger.requires_llm)
        self.assertEqual(trigger.mode, "normal")

    def test_hard_budget_triggers_without_phase_boundary(self) -> None:
        """预算达到硬阈值时不依赖阶段边界也应触发。"""
        state = append_user_message(create_initial_agent_state("query_phase", "hello"), "continue")
        manager = RuntimeContextManager()
        messages = manager.project(state)
        budget = ContextBudgetEstimator().estimate_messages(messages)
        trigger = PhaseSummaryPolicy(soft_token_limit=1, hard_token_limit=1).evaluate(
            state=state,
            working_set=manager.latest_working_set,
            delta=manager.latest_state_delta,
            budget=budget,
        )
        self.assertEqual(trigger.reason, "budget_hard_limit")
        self.assertTrue(trigger.requires_llm)

    def test_service_generates_and_persists_phase_summary(self) -> None:
        """PhaseSummaryService 应调用模型生成摘要并写入 ctx Markdown。"""
        state = _state_after_context_lookup()
        manager = RuntimeContextManager()
        projected = manager.project_state_for_model(state)
        with tempfile.TemporaryDirectory() as temp_dir:
            service = PhaseSummaryService(
                policy=PhaseSummaryPolicy(soft_token_limit=1, hard_token_limit=10000),
                store=PhaseSummaryStore(Path(temp_dir)),
            )
            trigger, record = service.maybe_create_summary(
                model_client=_SummaryModelClient(),
                state=state,
                projected_messages=projected.messages,
                working_set=manager.latest_working_set,
                delta=manager.latest_state_delta,
                budget=manager.latest_budget_report,
            )
            self.assertEqual(trigger.reason, "phase_boundary_budget")
            self.assertIsNotNone(record)
            assert record is not None
            self.assertTrue(record.relative_path.startswith("data/contexts/ctx_"))
            self.assertTrue(record.path.exists())
            self.assertIn("阶段摘要", record.summary_text)
            loaded = PhaseSummaryStore(Path(temp_dir)).read_summary(record.summary_id)
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(loaded.trigger_reason, "phase_boundary_budget")
            self.assertIn("tool_lookup", loaded.anchor_tool_use_ids)

    def test_agent_loop_runs_phase_summary_before_normal_model_call(self) -> None:
        """AgentLoop 应在普通模型调用前接入阶段摘要生成。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            client = _TwoCallModelClient()
            manager = RuntimeContextManager()
            service = PhaseSummaryService(
                policy=PhaseSummaryPolicy(soft_token_limit=1, hard_token_limit=10000),
                store=PhaseSummaryStore(Path(temp_dir)),
            )
            loop = AgentLoop(
                client,
                ToolRegistry(),
                Path(temp_dir),
                runtime_context_manager=manager,
                phase_summary_service=service,
            )
            result = loop.run_until_stop("hello", query_id="query_loop_phase")
            self.assertEqual(result.final_text, "ok")
            self.assertEqual(client.query_ids, ["query_loop_phase_phase_summary", "query_loop_phase"])
            self.assertIsNotNone(manager.latest_phase_summary_record)
            assert manager.latest_phase_summary_record is not None
            self.assertTrue(manager.latest_phase_summary_record.path.exists())

    def test_context_overflow_forces_emergency_phase_summary(self) -> None:
        """模型调用出现 context_overflow 时应尝试生成 emergency 阶段摘要记录。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            client = _OverflowThenSummaryModelClient()
            manager = RuntimeContextManager()
            service = PhaseSummaryService(
                policy=PhaseSummaryPolicy(soft_token_limit=9999, hard_token_limit=10000),
                store=PhaseSummaryStore(Path(temp_dir)),
            )
            loop = AgentLoop(
                client,
                ToolRegistry(),
                Path(temp_dir),
                max_model_recovery_attempts=0,
                runtime_context_manager=manager,
                phase_summary_service=service,
            )
            result = loop.run_until_stop("hello", query_id="query_overflow")
            self.assertEqual(result.stop_reason, "context_overflow")
            self.assertIsNotNone(manager.latest_phase_summary_record)
            assert manager.latest_phase_summary_record is not None
            self.assertEqual(manager.latest_phase_summary_record.trigger_reason, "context_overflow")
            self.assertEqual(manager.latest_phase_summary_record.trigger_mode, "emergency")


def _state_after_context_lookup():
    """构造完成身份查询后的阶段边界状态。"""
    state = create_initial_agent_state("query_lookup", "识别张三")
    state = append_assistant_message(
        state,
        (
            AgentContentBlock(
                type="tool_use",
                tool_use_id="tool_lookup",
                tool_name="lookup_contact_identity",
                tool_input={"name": "张三", "task_id": "task_001"},
            ),
        ),
    )
    state = append_tool_results(
        state,
        (
            AgentContentBlock(
                type="tool_result",
                tool_use_id="tool_lookup",
                tool_name="lookup_contact_identity",
                content='{"contact_id":"contact_001","task_id":"task_001"}',
            ),
        ),
    )
    return state


class _SummaryModelClient:
    """只返回阶段摘要文本。"""

    def call_model(self, state, tools) -> ModelResponse:
        del state, tools
        return ModelResponse((AgentContentBlock(type="text", text="阶段摘要：已完成身份查询。"),), "stop")


class _TwoCallModelClient:
    """第一次用于阶段摘要，第二次用于正式回复。"""

    def __init__(self) -> None:
        self.query_ids: list[str] = []

    def call_model(self, state, tools) -> ModelResponse:
        del tools
        self.query_ids.append(state.query_id)
        if state.query_id.endswith("_phase_summary"):
            return ModelResponse((AgentContentBlock(type="text", text="阶段摘要：初始请求。"),), "stop")
        return ModelResponse((AgentContentBlock(type="text", text="ok"),), "stop")


class _OverflowThenSummaryModelClient:
    """普通模型调用失败，emergency summary 调用成功。"""

    def call_model(self, state, tools) -> ModelResponse:
        del tools
        if state.query_id.endswith("_phase_summary"):
            return ModelResponse((AgentContentBlock(type="text", text="阶段摘要：上下文过长。"),), "stop")
        raise RuntimeError("context length exceeded")


if __name__ == "__main__":
    unittest.main()
