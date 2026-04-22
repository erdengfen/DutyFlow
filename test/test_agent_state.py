# 本文件验证 Agent State 的多轮更新、工具结果回写和序列化行为。

from pathlib import Path
import sys
import tempfile
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
    from_dict,
    load_agent_state,
    record_recovery_attempt,
    resolve_recovery_scope,
    save_agent_state,
    to_dict,
    upsert_recovery_scope,
)
from dutyflow.agent.recovery import RecoveryScope  # noqa: E402


class TestAgentState(unittest.TestCase):
    """验证 Step 2.1 Agent State 的基础不变量。"""

    def test_initial_state_has_user_message(self) -> None:
        """初始化后应包含用户消息和 start 转移原因。"""
        state = create_initial_agent_state("query_001", "处理这个消息")
        self.assertEqual(state.turn_count, 1)
        self.assertEqual(state.transition_reason, "start")
        self.assertEqual(state.messages[0].role, "user")
        self.assertEqual(state.messages[0].content[0].text, "处理这个消息")

    def test_append_assistant_text_does_not_create_pending_tool(self) -> None:
        """追加纯文本 assistant 回复不应产生待完成工具。"""
        state = create_initial_agent_state("query_001", "hello")
        state = append_assistant_message(
            state,
            (AgentContentBlock(type="text", text="收到"),),
        )
        self.assertEqual(len(state.messages), 2)
        self.assertEqual(state.pending_tool_use_ids, ())

    def test_tool_use_and_tool_result_advance_turn(self) -> None:
        """工具结果写回后应清理 pending 并进入下一轮。"""
        state = create_initial_agent_state("query_001", "读取上下文")
        state = append_assistant_message(state, (_tool_use("tool_001"),))
        self.assertEqual(state.pending_tool_use_ids, ("tool_001",))
        state = append_tool_results(state, (_tool_result("tool_001", "ok"),))
        self.assertEqual(state.pending_tool_use_ids, ())
        self.assertEqual(state.transition_reason, "tool_result_continuation")
        self.assertEqual(state.turn_count, 2)
        self.assertEqual(state.messages[-1].role, "user")
        self.assertEqual(state.messages[-1].content[0].content, "ok")

    def test_tool_result_requires_tool_use_id(self) -> None:
        """缺少 tool_use_id 的工具结果必须失败。"""
        state = create_initial_agent_state("query_001", "读取上下文")
        with self.assertRaises(ValueError):
            append_tool_results(state, (AgentContentBlock(type="tool_result"),))

    def test_tool_result_must_match_pending_tool(self) -> None:
        """未匹配 pending tool 的结果必须失败。"""
        state = create_initial_agent_state("query_001", "读取上下文")
        state = append_assistant_message(state, (_tool_use("tool_001"),))
        with self.assertRaises(ValueError):
            append_tool_results(state, (_tool_result("tool_404", "missing"),))

    def test_to_dict_and_from_dict_keep_core_fields(self) -> None:
        """序列化再恢复后关键字段应保持一致。"""
        state = create_initial_agent_state("query_001", "读取上下文")
        state = append_assistant_message(state, (_tool_use("tool_001"),))
        restored = from_dict(to_dict(state))
        self.assertEqual(restored.query_id, state.query_id)
        self.assertEqual(restored.pending_tool_use_ids, ("tool_001",))
        self.assertEqual(restored.messages[-1].content[0].tool_name, "demo_tool")

    def test_load_and_save_are_dict_operations(self) -> None:
        """load/save 封装只处理字典，不执行磁盘读写。"""
        state = create_initial_agent_state("query_001", "读取上下文")
        restored = load_agent_state(save_agent_state(state))
        self.assertEqual(restored.query_id, "query_001")

    def test_record_recovery_attempt_updates_aggregate_fields(self) -> None:
        """恢复尝试应更新聚合计数和最近恢复摘要。"""
        state = create_initial_agent_state("query_001", "读取上下文")
        state = record_recovery_attempt(
            state,
            "tool_timeout",
            interruption_reason="wait_next_retry_window",
            resume_point="before_tool_execute",
        )
        self.assertEqual(state.recovery.tool_error_attempts, 1)
        self.assertEqual(state.recovery.latest_interruption_reason, "wait_next_retry_window")
        self.assertEqual(state.recovery.latest_resume_point, "before_tool_execute")
        self.assertEqual(state.task_control.attempt_count, 1)
        self.assertEqual(state.task_control.retry_status, "retrying")
        self.assertEqual(state.task_control.next_action, "retry_tool_call_later")

    def test_approval_waiting_updates_task_control_summary(self) -> None:
        """审批等待应同步写回任务控制中的审批摘要。"""
        state = create_initial_agent_state("query_001", "读取上下文")
        state = record_recovery_attempt(
            state,
            "approval_waiting",
            interruption_reason="waiting_approval",
            resume_point="after_approval",
        )
        self.assertEqual(state.task_control.attempt_count, 1)
        self.assertEqual(state.task_control.approval_status, "waiting")
        self.assertEqual(state.task_control.next_action, "wait_for_approval")

    def test_upsert_recovery_scope_can_roundtrip(self) -> None:
        """recovery scope 应可写入状态并随序列化稳定恢复。"""
        state = create_initial_agent_state("query_001", "读取上下文")
        scope = _recovery_scope()
        state = upsert_recovery_scope(state, scope)
        restored = from_dict(to_dict(state))
        self.assertEqual(len(restored.recovery.recovery_scopes), 1)
        self.assertEqual(restored.recovery.recovery_scopes[0].recovery_id, "rec_001")

    def test_resolve_recovery_scope_updates_status(self) -> None:
        """已存在的 recovery scope 应能被标记为 resolved。"""
        state = create_initial_agent_state("query_001", "读取上下文")
        state = upsert_recovery_scope(state, _recovery_scope())
        state = resolve_recovery_scope(state, "rec_001")
        self.assertEqual(state.recovery.recovery_scopes[0].status, "resolved")
        self.assertEqual(state.task_control.retry_status, "none")
        self.assertEqual(state.task_control.next_action, "")

    def test_resolve_approval_scope_marks_task_control_approved(self) -> None:
        """审批等待 scope 在 resolved 后应把审批摘要标记为 approved。"""
        state = create_initial_agent_state("query_001", "读取上下文")
        state = record_recovery_attempt(
            state,
            "approval_waiting",
            interruption_reason="waiting_approval",
            resume_point="after_approval",
        )
        scope = RecoveryScope(
            recovery_id="rec_approval",
            scope_type="tool_call",
            scope_id="tool_001",
            status="waiting",
            failure_kind="approval_waiting",
            interruption_reason="waiting_approval",
            strategy="wait_approval",
            resume_point="after_approval",
        )
        state = upsert_recovery_scope(state, scope)
        state = resolve_recovery_scope(state, "rec_approval", status="resolved", last_error="approved in CLI")
        self.assertEqual(state.task_control.approval_status, "approved")
        self.assertEqual(state.task_control.retry_status, "none")
        self.assertEqual(state.task_control.next_action, "resume_after_approval")

    def test_state_does_not_touch_control_snapshot_file(self) -> None:
        """Agent State 初始化和更新不得创建本地快照文件。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            state = create_initial_agent_state("query_001", "读取上下文")
            state = append_assistant_message(state, (_tool_use("tool_001"),))
            append_tool_results(state, (_tool_result("tool_001", "ok"),))
            snapshot = root / "data/state/agent_control_state.md"
        self.assertFalse(snapshot.exists())


def _tool_use(tool_use_id: str) -> AgentContentBlock:
    """构造测试用工具调用块。"""
    return AgentContentBlock(
        type="tool_use",
        tool_use_id=tool_use_id,
        tool_name="demo_tool",
        tool_input={"name": "demo"},
    )


def _tool_result(tool_use_id: str, content: str) -> AgentContentBlock:
    """构造测试用工具结果块。"""
    return AgentContentBlock(
        type="tool_result",
        tool_use_id=tool_use_id,
        content=content,
    )


def _recovery_scope() -> RecoveryScope:
    """构造测试用 recovery scope。"""
    return RecoveryScope(
        recovery_id="rec_001",
        scope_type="tool_call",
        scope_id="tool_001",
        status="waiting",
        failure_kind="tool_retry_exhausted",
        interruption_reason="wait_next_retry_window",
        strategy="retry_later",
        attempt_count=1,
        max_attempts=3,
        resume_point="before_tool_execute",
        resume_payload={"tool_name": "demo_tool"},
    )


def _self_test() -> None:
    """运行本文件单元测试。"""
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestAgentState)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    _self_test()
