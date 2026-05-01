# 本文件验证 Runtime Context 第一版只做模型调用前 messages 投影。

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sys
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dutyflow.agent.core_loop import AgentLoop  # noqa: E402
from dutyflow.agent.model_client import ModelResponse  # noqa: E402
from dutyflow.agent.recovery import RecoveryScope  # noqa: E402
from dutyflow.agent.state import (  # noqa: E402
    AgentContentBlock,
    AgentRecoveryState,
    AgentTaskControl,
    AgentState,
    append_assistant_message,
    append_tool_results,
    create_initial_agent_state,
)
from dutyflow.agent.tools.registry import ToolRegistry  # noqa: E402
from dutyflow.context.runtime_context import RuntimeContextManager  # noqa: E402


class TestRuntimeContextManager(unittest.TestCase):
    """验证 ModelContextView 概念层不新增独立数据结构。"""

    def test_first_version_projects_existing_messages(self) -> None:
        """第一版投影应直接返回现有 AgentMessage 序列。"""
        state = create_initial_agent_state("ctx_001", "hello")
        manager = RuntimeContextManager()
        self.assertIs(manager.project(state), state.messages)
        self.assertIs(manager.project_messages(state), state.messages)
        self.assertIs(manager.project_state_for_model(state), state)

    def test_agent_loop_calls_model_with_projected_state(self) -> None:
        """AgentLoop 调模型前应使用 RuntimeContextManager 输出的投影 state。"""
        manager = _MarkerRuntimeContextManager()
        client = _CapturingModelClient()
        loop = AgentLoop(
            client,
            ToolRegistry(),
            PROJECT_ROOT,
            runtime_context_manager=manager,
        )
        result = loop.run_until_stop("hello", query_id="ctx_projection")
        self.assertEqual(manager.project_count, 1)
        self.assertIn(_PROJECTION_MARKER, client.first_system_text)
        self.assertNotIn(_PROJECTION_MARKER, _first_system_text(result.state))
        self.assertEqual(result.final_text, "ok")

    def test_build_working_set_extracts_runtime_focus(self) -> None:
        """Working Set 应从 AgentState 中提取当前目标、工具锚点和控制状态。"""
        state = _working_set_state()
        working_set = RuntimeContextManager().build_working_set(state)
        self.assertEqual(working_set.query_id, "ctx_working")
        self.assertEqual(working_set.latest_user_text, "请处理这个任务")
        self.assertEqual(working_set.latest_assistant_text, "工具结果已收到")
        self.assertEqual(working_set.current_task_id, "task_001")
        self.assertEqual(working_set.pending_tool_use_ids, ())
        self.assertEqual(working_set.last_tool_result_ids, ("tool_1",))
        self.assertEqual(working_set.recent_tool_use_ids, ("tool_1",))
        self.assertEqual(working_set.recent_tool_names, ("sample_tool",))
        self.assertEqual(working_set.task_weight_level, "high")
        self.assertEqual(working_set.approval_status, "waiting")
        self.assertEqual(working_set.retry_status, "retrying")
        self.assertEqual(working_set.waiting_recovery_scope_ids, ("tool_1",))
        self.assertEqual(working_set.to_dict()["query_id"], "ctx_working")
        self.assertEqual(working_set.to_dict()["recent_tool_use_ids"], ["tool_1"])

    def test_project_refreshes_latest_working_set(self) -> None:
        """每次 project 都应刷新最近一次 Working Set 快照。"""
        state = create_initial_agent_state("ctx_latest", "hello")
        manager = RuntimeContextManager()
        manager.project(state)
        self.assertIsNotNone(manager.latest_working_set)
        self.assertIsNotNone(manager.latest_state_delta)
        assert manager.latest_working_set is not None
        assert manager.latest_state_delta is not None
        self.assertEqual(manager.latest_working_set.latest_user_text, "hello")
        self.assertEqual(manager.latest_state_delta.new_user_text, "hello")

    def test_build_state_delta_detects_runtime_changes(self) -> None:
        """State Delta 应只描述两次 Working Set 之间的新增和变化。"""
        manager = RuntimeContextManager()
        previous = manager.build_working_set(create_initial_agent_state("ctx_working", "旧任务"))
        current = manager.build_working_set(_working_set_state())
        delta = manager.build_state_delta(previous, current)
        self.assertEqual(delta.previous_turn_count, 1)
        self.assertEqual(delta.current_turn_count, 2)
        self.assertTrue(delta.turn_advanced)
        self.assertEqual(delta.new_user_text, "请处理这个任务")
        self.assertEqual(delta.new_assistant_text, "工具结果已收到")
        self.assertEqual(delta.new_tool_result_ids, ("tool_1",))
        self.assertEqual(delta.new_recent_tool_use_ids, ("tool_1",))
        self.assertEqual(delta.new_recent_tool_names, ("sample_tool",))
        self.assertEqual(
            delta.task_control_changed_fields,
            ("task_weight_level", "approval_status", "retry_status", "next_action"),
        )
        self.assertEqual(delta.recovery_changed_fields, ("latest_interruption_reason", "latest_resume_point"))
        self.assertEqual(delta.new_waiting_recovery_scope_ids, ("tool_1",))
        self.assertEqual(delta.to_dict()["new_recent_tool_names"], ["sample_tool"])

    def test_build_state_delta_detects_resolved_tool_calls(self) -> None:
        """State Delta 应能识别上轮等待、本轮已完成的工具调用。"""
        manager = RuntimeContextManager()
        pending_state = _pending_tool_state()
        resolved_state = append_tool_results(
            pending_state,
            (
                AgentContentBlock(
                    type="tool_result",
                    tool_use_id="tool_1",
                    tool_name="sample_tool",
                    content="ok",
                ),
            ),
        )
        previous = manager.build_working_set(pending_state)
        current = manager.build_working_set(resolved_state)
        delta = manager.build_state_delta(previous, current)
        self.assertEqual(delta.resolved_tool_use_ids, ("tool_1",))
        self.assertEqual(delta.new_tool_result_ids, ("tool_1",))
        self.assertEqual(delta.new_pending_tool_use_ids, ())


class _MarkerRuntimeContextManager(RuntimeContextManager):
    """测试用投影器：只改模型可见 system message，不改源 state。"""

    def __init__(self) -> None:
        """记录投影调用次数。"""
        super().__init__()
        self.project_count = 0

    def project_state_for_model(self, state: AgentState) -> AgentState:
        """返回带测试标记的投影 state。"""
        self.project_count += 1
        first = state.messages[0]
        block = first.content[0]
        marked_block = replace(block, text=block.text + "\n" + _PROJECTION_MARKER)
        marked_message = replace(first, content=(marked_block,))
        return replace(state, messages=(marked_message,) + state.messages[1:])


class _CapturingModelClient:
    """保存模型调用时实际收到的 state。"""

    def __init__(self) -> None:
        """初始化捕获字段。"""
        self.first_system_text = ""

    def call_model(self, state, tools) -> ModelResponse:
        """捕获 system prompt 并返回固定文本。"""
        del tools
        self.first_system_text = _first_system_text(state)
        return ModelResponse((AgentContentBlock(type="text", text="ok"),), "stop")


def _first_system_text(state: AgentState) -> str:
    """提取 state 第一条 system message 的文本。"""
    if not state.messages or state.messages[0].role != "system":
        return ""
    return "\n".join(block.text for block in state.messages[0].content if block.type == "text")


def _working_set_state() -> AgentState:
    """构造包含工具、任务控制和恢复 scope 的 Working Set 测试状态。"""
    state = create_initial_agent_state("ctx_working", "请处理这个任务")
    state = _append_tool_exchange(state)
    state = append_assistant_message(state, (AgentContentBlock(type="text", text="工具结果已收到"),))
    return _attach_control_state(state)


def _pending_tool_state() -> AgentState:
    """构造仍有未完成工具调用的测试状态。"""
    state = create_initial_agent_state("ctx_pending", "请处理这个任务")
    return append_assistant_message(
        state,
        (
            AgentContentBlock(
                type="tool_use",
                tool_use_id="tool_1",
                tool_name="sample_tool",
                tool_input={"text": "hello"},
            ),
        ),
    )


def _append_tool_exchange(state: AgentState) -> AgentState:
    """给测试状态追加一次工具调用和工具结果。"""
    state = append_assistant_message(
        state,
        (
            AgentContentBlock(
                type="tool_use",
                tool_use_id="tool_1",
                tool_name="sample_tool",
                tool_input={"text": "hello"},
            ),
        ),
    )
    state = append_tool_results(
        state,
        (
            AgentContentBlock(
                type="tool_result",
                tool_use_id="tool_1",
                tool_name="sample_tool",
                content="工具返回内容不应覆盖用户输入",
            ),
        ),
    )
    return state


def _attach_control_state(state: AgentState) -> AgentState:
    """给测试状态补充任务控制和等待恢复 scope。"""
    return replace(
        state,
        current_task_id="task_001",
        task_control=AgentTaskControl(
            task_id="task_001",
            weight_level="high",
            approval_status="waiting",
            retry_status="retrying",
            next_action="wait_approval",
        ),
        recovery=AgentRecoveryState(
            latest_interruption_reason="waiting_approval",
            latest_resume_point="before_tool_execute",
            recovery_scopes=(
                RecoveryScope(
                    recovery_id="rec_001",
                    scope_type="tool_call",
                    scope_id="tool_1",
                    status="waiting",
                    failure_kind="approval_waiting",
                    interruption_reason="waiting_approval",
                    resume_point="before_tool_execute",
                ),
            ),
        ),
    )


_PROJECTION_MARKER = "runtime-context-projection-marker"


if __name__ == "__main__":
    unittest.main()
