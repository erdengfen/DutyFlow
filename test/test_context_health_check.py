# 本文件验证 Context Health Check 的各项检查逻辑和与 RuntimeContextManager 的集成。

from __future__ import annotations

from dataclasses import replace
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
    AgentTaskControl,
    AgentState,
    create_initial_agent_state,
    append_assistant_message,
    append_tool_results,
    append_user_message,
)
from dutyflow.context.runtime_context import (  # noqa: E402
    ContextHealthCheck,
    ContextHealthCheckItem,
    RuntimeContextManager,
    run_context_health_check,
)


def _make_text_message(role: str, text: str) -> AgentMessage:
    return AgentMessage(
        role=role,
        content=(AgentContentBlock(type="text", text=text),),
    )


def _make_tool_result_message(tool_use_id: str, content: str) -> AgentMessage:
    return AgentMessage(
        role="user",
        content=(
            AgentContentBlock(
                type="tool_result",
                tool_use_id=tool_use_id,
                tool_name="sample_tool",
                content=content,
            ),
        ),
    )


def _make_tool_use_message(tool_use_id: str) -> AgentMessage:
    return AgentMessage(
        role="assistant",
        content=(
            AgentContentBlock(
                type="tool_use",
                tool_use_id=tool_use_id,
                tool_name="sample_tool",
            ),
        ),
    )


def _make_state(
    query_id: str = "query_hc",
    task_id: str = "",
    event_id: str = "",
    messages: tuple[AgentMessage, ...] = (),
    pending_tool_use_ids: tuple[str, ...] = (),
) -> AgentState:
    base = create_initial_agent_state(query_id, "test input", current_event_id=event_id)
    state = replace(
        base,
        current_task_id=task_id,
        task_control=AgentTaskControl(task_id=task_id),
        pending_tool_use_ids=pending_tool_use_ids,
        messages=base.messages + messages,
    )
    return state


class TestContextHealthCheckResult(unittest.TestCase):
    """验证 ContextHealthCheck 和 ContextHealthCheckItem 的基本结构。"""

    def test_passed_when_all_checks_pass(self) -> None:
        """所有子项通过时 ContextHealthCheck.passed 应为 True。"""
        checks = (
            ContextHealthCheckItem(name="check_a", passed=True, reason=""),
            ContextHealthCheckItem(name="check_b", passed=True, reason=""),
        )
        result = ContextHealthCheck(passed=True, checks=checks, failed_checks=())
        self.assertTrue(result.passed)
        self.assertEqual(result.failed_checks, ())

    def test_failed_when_any_check_fails(self) -> None:
        """任意子项失败时 ContextHealthCheck.passed 应为 False。"""
        checks = (
            ContextHealthCheckItem(name="check_a", passed=True, reason=""),
            ContextHealthCheckItem(name="check_b", passed=False, reason="missing"),
        )
        result = ContextHealthCheck(passed=False, checks=checks, failed_checks=("check_b",))
        self.assertFalse(result.passed)
        self.assertIn("check_b", result.failed_checks)

    def test_to_dict_serializable(self) -> None:
        """to_dict() 应返回可序列化的结构。"""
        checks = (ContextHealthCheckItem(name="check_a", passed=True, reason=""),)
        result = ContextHealthCheck(passed=True, checks=checks, failed_checks=())
        d = result.to_dict()
        self.assertIn("passed", d)
        self.assertIn("failed_checks", d)
        self.assertIn("checks", d)
        self.assertIsInstance(d["checks"], list)


class TestRunContextHealthCheck(unittest.TestCase):
    """验证 run_context_health_check 各条目的通过和失败逻辑。"""

    def test_passes_when_no_active_ids_and_messages_unchanged(self) -> None:
        """无 task_id/event_id/pending_tool_ids 且 messages 未减少时应全部通过。"""
        state = _make_state()
        result = run_context_health_check(state, state.messages)
        self.assertTrue(result.passed)

    def test_fails_message_count_when_projected_shorter(self) -> None:
        """projected_messages 比 source 短时 message_count_preserved 应失败。"""
        state = _make_state(messages=(_make_text_message("assistant", "hello"),))
        # projected_messages 只保留初始 system 消息，比 state.messages 短
        projected = state.messages[:1]
        result = run_context_health_check(state, projected)
        self.assertFalse(result.passed)
        self.assertIn("message_count_preserved", result.failed_checks)

    def test_passes_when_task_id_visible_in_projected_text(self) -> None:
        """task_id 在 projected_messages 文本中可见时 task_id_preserved 应通过。"""
        state = _make_state(task_id="task_abc")
        msg = _make_text_message("assistant", "当前任务 task_abc 处理中。")
        projected = state.messages + (msg,)
        result = run_context_health_check(state, projected)
        failed = result.failed_checks
        self.assertNotIn("task_id_preserved", failed)

    def test_fails_when_task_id_not_visible_in_projected(self) -> None:
        """task_id 在 projected_messages 中完全不出现时 task_id_preserved 应失败。"""
        state = _make_state(task_id="task_xyz")
        # projected 只有不含 task_xyz 的初始消息
        result = run_context_health_check(state, state.messages)
        self.assertIn("task_id_preserved", result.failed_checks)

    def test_passes_when_no_task_id(self) -> None:
        """state 没有 task_id 时 task_id_preserved 应直接通过（无需查找）。"""
        state = _make_state(task_id="")
        result = run_context_health_check(state, state.messages)
        self.assertNotIn("task_id_preserved", result.failed_checks)

    def test_fails_when_event_id_not_visible(self) -> None:
        """event_id 在 projected_messages 中不可见时 event_id_preserved 应失败。"""
        state = _make_state(event_id="evt_999")
        result = run_context_health_check(state, state.messages)
        self.assertIn("event_id_preserved", result.failed_checks)

    def test_passes_when_event_id_visible(self) -> None:
        """event_id 在 projected_messages 文本中可见时应通过。"""
        state = _make_state(event_id="evt_vis")
        msg = _make_text_message("user", "事件 evt_vis 已触发。")
        projected = state.messages + (msg,)
        result = run_context_health_check(state, projected)
        self.assertNotIn("event_id_preserved", result.failed_checks)

    def test_fails_when_pending_tool_id_missing_from_projected(self) -> None:
        """pending_tool_use_ids 中的 ID 在 projected 中不存在时应失败。"""
        state = _make_state(pending_tool_use_ids=("tool_missing",))
        result = run_context_health_check(state, state.messages)
        self.assertIn("pending_tool_ids_preserved", result.failed_checks)

    def test_passes_when_pending_tool_id_present_in_projected(self) -> None:
        """pending_tool_use_ids 在 projected tool_use block 中可见时应通过。"""
        state = _make_state(pending_tool_use_ids=("tool_present",))
        tool_msg = _make_tool_use_message("tool_present")
        projected = state.messages + (tool_msg,)
        result = run_context_health_check(state, projected)
        self.assertNotIn("pending_tool_ids_preserved", result.failed_checks)

    def test_passes_when_no_pending_tool_ids(self) -> None:
        """没有 pending_tool_use_ids 时 pending_tool_ids_preserved 应直接通过。"""
        state = _make_state()
        result = run_context_health_check(state, state.messages)
        self.assertNotIn("pending_tool_ids_preserved", result.failed_checks)

    def test_pending_tool_id_visible_in_tool_result_block(self) -> None:
        """pending tool_use_id 出现在 projected tool_result block 中也应通过。"""
        state = _make_state(pending_tool_use_ids=("tool_result_check",))
        result_msg = _make_tool_result_message("tool_result_check", "done")
        projected = state.messages + (result_msg,)
        result = run_context_health_check(state, projected)
        self.assertNotIn("pending_tool_ids_preserved", result.failed_checks)


class TestRuntimeContextManagerHealthCheck(unittest.TestCase):
    """验证 RuntimeContextManager.project() 时 latest_health_check 被正确设置。"""

    def test_health_check_set_after_project(self) -> None:
        """project() 调用后 latest_health_check 应非 None。"""
        state = create_initial_agent_state("query_hc_mgr", "hello")
        manager = RuntimeContextManager()
        manager.project(state)
        self.assertIsNotNone(manager.latest_health_check)

    def test_health_check_passes_for_clean_state(self) -> None:
        """无活跃 ID 的干净状态，health check 应通过。"""
        state = create_initial_agent_state("query_clean", "hello")
        manager = RuntimeContextManager()
        manager.project(state)
        assert manager.latest_health_check is not None
        self.assertTrue(manager.latest_health_check.passed)

    def test_health_check_exposed_via_project_state_for_model(self) -> None:
        """project_state_for_model() 也应更新 latest_health_check。"""
        state = create_initial_agent_state("query_psm", "hello")
        manager = RuntimeContextManager()
        manager.project_state_for_model(state)
        self.assertIsNotNone(manager.latest_health_check)

    def test_health_check_passes_after_micro_compact(self) -> None:
        """micro-compact 后 projected_messages 长度不变，health check 应仍通过。"""
        state = create_initial_agent_state("query_mc_hc", "hello")
        state = append_assistant_message(
            state,
            (AgentContentBlock(type="tool_use", tool_use_id="tool_hc1", tool_name="sample_tool"),),
        )
        state = append_tool_results(
            state,
            (AgentContentBlock(type="tool_result", tool_use_id="tool_hc1", tool_name="sample_tool", content="raw data"),),
        )
        state = append_user_message(state, "continue")
        manager = RuntimeContextManager()
        manager.project_state_for_model(state)
        assert manager.latest_health_check is not None
        self.assertTrue(manager.latest_health_check.passed)

    def test_health_check_to_dict_is_serializable(self) -> None:
        """to_dict() 应返回可 JSON 序列化的结构。"""
        import json

        state = create_initial_agent_state("query_serial", "test")
        manager = RuntimeContextManager()
        manager.project(state)
        assert manager.latest_health_check is not None
        payload = manager.latest_health_check.to_dict()
        json.dumps(payload)  # 不抛出即通过


def _self_test() -> None:
    """运行本文件单元测试。"""
    suite = unittest.defaultTestLoader.loadTestsFromName(__name__)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    _self_test()
