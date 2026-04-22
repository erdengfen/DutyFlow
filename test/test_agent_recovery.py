# 本文件验证 RecoveryManager 的事件、决策和 scope 结构。

from pathlib import Path
import sys
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dutyflow.agent.recovery import (  # noqa: E402
    RecoveryDecision,
    RecoveryEvent,
    RecoveryManager,
    RecoveryScope,
)


class TestAgentRecovery(unittest.TestCase):
    """验证 RecoveryManager 的第一版纯内存行为。"""

    def test_approval_waiting_becomes_wait_approval(self) -> None:
        """审批等待事件应转换为 wait_approval。"""
        decision = RecoveryManager().decide(
            RecoveryEvent(
                scope_type="tool_call",
                scope_id="tool_001",
                failure_kind="approval_waiting",
            )
        )
        self.assertEqual(decision.strategy, "wait_approval")
        self.assertEqual(decision.interruption_reason, "waiting_approval")
        self.assertEqual(decision.resume_point, "after_approval")

    def test_context_overflow_becomes_degrade(self) -> None:
        """上下文溢出事件应先进入 compact/degrade 路径。"""
        decision = RecoveryManager().decide(
            RecoveryEvent(
                scope_type="turn",
                scope_id="turn_001",
                failure_kind="context_overflow",
            )
        )
        self.assertEqual(decision.strategy, "degrade")
        self.assertEqual(decision.interruption_reason, "context_compaction_pending")

    def test_retryable_exhausted_event_becomes_retry_later(self) -> None:
        """已耗尽当前同步重试预算的可重试错误应进入 retry_later。"""
        decision = RecoveryManager().decide(
            RecoveryEvent(
                scope_type="tool_call",
                scope_id="tool_001",
                failure_kind="tool_timeout",
                attempt_count=3,
                max_attempts=3,
                retryable=True,
            )
        )
        self.assertEqual(decision.strategy, "retry_later")
        self.assertEqual(decision.interruption_reason, "wait_next_retry_window")

    def test_create_scope_uses_decision_fields(self) -> None:
        """RecoveryScope 应继承事件和恢复决策的关键字段。"""
        manager = RecoveryManager()
        event = RecoveryEvent(
            scope_type="tool_call",
            scope_id="tool_001",
            failure_kind="approval_waiting",
            metadata={"tool_name": "demo_tool"},
        )
        decision = manager.decide(event)
        scope = manager.create_scope("rec_001", event, decision)
        self.assertEqual(scope.recovery_id, "rec_001")
        self.assertEqual(scope.status, "waiting")
        self.assertEqual(scope.resume_payload["tool_name"], "demo_tool")

    def test_retry_later_scope_gets_current_process_next_retry_at(self) -> None:
        """retry_later 的 scope 应生成当前进程内下一次 restart 时间。"""
        manager = RecoveryManager()
        event = RecoveryEvent(
            scope_type="tool_call",
            scope_id="tool_001",
            failure_kind="tool_timeout",
            attempt_count=3,
            max_attempts=3,
            retryable=True,
        )
        scope = manager.create_scope("rec_002", event, manager.decide(event))
        self.assertEqual(scope.status, "scheduled")
        self.assertNotEqual(scope.next_retry_at, "")

    def test_collect_restart_descriptions_exposes_resume_token(self) -> None:
        """waiting / scheduled scope 应能转换为 restart 描述。"""
        manager = RecoveryManager()
        waiting_scope = RecoveryScope(
            recovery_id="rec_wait",
            scope_type="tool_call",
            scope_id="tool_001",
            status="waiting",
            failure_kind="approval_waiting",
            interruption_reason="waiting_approval",
            strategy="wait_approval",
            resume_point="after_approval",
        )
        scheduled_scope = RecoveryScope(
            recovery_id="rec_schedule",
            scope_type="tool_call",
            scope_id="tool_002",
            status="scheduled",
            failure_kind="tool_timeout",
            interruption_reason="wait_next_retry_window",
            strategy="retry_later",
            next_retry_at="2999-01-01T00:00:00+00:00",
            resume_point="before_tool_execute",
        )
        descriptions = manager.collect_restart_descriptions((waiting_scope, scheduled_scope))
        self.assertEqual(len(descriptions), 2)
        self.assertEqual(descriptions[0].resume_token, "resume_rec_wait")
        self.assertEqual(descriptions[0].restart_action, "resume_after_approval")
        self.assertFalse(descriptions[0].can_restart_now)
        self.assertEqual(descriptions[1].restart_action, "restart_tool_call")
        self.assertFalse(descriptions[1].can_restart_now)
        self.assertEqual(manager.resolve_resume_token((waiting_scope, scheduled_scope), "resume_rec_wait"), waiting_scope)

    def test_unknown_failure_kind_is_rejected(self) -> None:
        """未知失败类型必须显式报错。"""
        with self.assertRaises(ValueError):
            RecoveryEvent(
                scope_type="tool_call",
                scope_id="tool_001",
                failure_kind="unknown_failure",
            )


def _self_test() -> None:
    """运行本文件单元测试。"""
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestAgentRecovery)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    _self_test()
