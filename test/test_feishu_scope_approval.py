# 本文件验证飞书 scope 启用审批能复用现有审批卡片链，并在通过后启用 candidate。

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dutyflow.approval.approval_card_action import ApprovalCardActionService  # noqa: E402
from dutyflow.feedback.gateway import FeedbackResult  # noqa: E402
from dutyflow.feishu.scope_approval import FeishuScopeApprovalService  # noqa: E402
from dutyflow.feishu.scope_registry import (  # noqa: E402
    GROUP_CHAT_SCOPE,
    GROUP_MESSAGE_COLLECTOR,
    FeishuScopeRecord,
    FeishuScopeRegistry,
)
from dutyflow.tasks.task_state import TaskStore  # noqa: E402


class TestFeishuScopeApproval(unittest.TestCase):
    """验证 candidate scope 的飞书端审批确认和 enabled 落盘。"""

    def test_request_enable_scope_creates_approval_card_without_enabling(self) -> None:
        """发起请求时只创建审批和卡片，不应直接启用 scope。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry = FeishuScopeRegistry(root)
            record = registry.upsert_candidate(_group_scope())
            feedback = _FakeFeedbackGateway()

            result = FeishuScopeApprovalService(
                root,
                registry=registry,
                feedback_gateway=feedback,
            ).request_enable_scope(record, expires_at="2026-05-08T10:00:00+08:00")
            stored = registry.read(record.account_id, record.scope_type, record.scope_id)
            task = TaskStore(root).read_task(result.task_id)

        self.assertTrue(result.ok)
        self.assertEqual(result.status, "approval_requested")
        self.assertEqual(result.approval_card_status, "sent")
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored.status, "candidate")
        self.assertIsNotNone(task)
        assert task is not None
        self.assertEqual(task.status, "waiting_approval")
        self.assertEqual(task.resume_point, "enable_feishu_scope")
        self.assertIn("DutyFlow向您请求*群聊 oc_group*阅读权限", feedback.sent_cards[0]["request"])

    def test_approved_card_enables_scope_and_completes_task(self) -> None:
        """用户在飞书卡片点批准后，scope 应从 candidate 变成 enabled。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry = FeishuScopeRegistry(root)
            record = registry.upsert_candidate(_group_scope())
            request = FeishuScopeApprovalService(
                root,
                registry=registry,
                feedback_gateway=_FakeFeedbackGateway(),
            ).request_enable_scope(record, expires_at="2026-05-08T10:00:00+08:00")

            result = ApprovalCardActionService(root).handle_raw_event(
                _card_event(request.approval_id, request.resume_token, "approved")
            )
            enabled = registry.read(record.account_id, record.scope_type, record.scope_id)
            task = TaskStore(root).read_task(request.task_id)

        self.assertTrue(result.ok)
        self.assertEqual(result.status, "approval_scope_enabled")
        self.assertEqual(result.toast_content, "审批已通过，阅读范围已启用。")
        self.assertEqual(result.payload["post_approval_action"]["status"], "scope_enabled")
        self.assertIsNotNone(enabled)
        assert enabled is not None
        self.assertEqual(enabled.status, "enabled")
        self.assertEqual(enabled.approved_by, "ou_owner")
        self.assertIsNotNone(task)
        assert task is not None
        self.assertEqual(task.status, "completed")

    def test_rejected_card_does_not_enable_scope(self) -> None:
        """用户拒绝后，scope 应保持 candidate，审批任务应取消。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry = FeishuScopeRegistry(root)
            record = registry.upsert_candidate(_group_scope())
            request = FeishuScopeApprovalService(
                root,
                registry=registry,
                feedback_gateway=_FakeFeedbackGateway(),
            ).request_enable_scope(record, expires_at="2026-05-08T10:00:00+08:00")

            result = ApprovalCardActionService(root).handle_raw_event(
                _card_event(request.approval_id, request.resume_token, "rejected")
            )
            stored = registry.read(record.account_id, record.scope_type, record.scope_id)
            task = TaskStore(root).read_task(request.task_id)

        self.assertTrue(result.ok)
        self.assertEqual(result.decision_result, "rejected")
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored.status, "candidate")
        self.assertIsNotNone(task)
        assert task is not None
        self.assertEqual(task.status, "cancelled")


class _FakeFeedbackGateway:
    """记录审批卡片发送请求，避免测试访问真实飞书。"""

    def __init__(self) -> None:
        """初始化已发送卡片列表。"""
        self.sent_cards: list[dict[str, str]] = []

    def send_owner_approval_card(self, approval: dict[str, str]) -> FeedbackResult:
        """记录卡片载荷并返回发送成功。"""
        self.sent_cards.append(dict(approval))
        return FeedbackResult(True, "sent", "ok", {"message_id": "om_test"})


def _group_scope() -> FeishuScopeRecord:
    """构造测试用群聊 candidate scope。"""
    return FeishuScopeRecord(
        account_id="tenant_1_ou_1",
        scope_type=GROUP_CHAT_SCOPE,
        scope_id="oc_group",
        collector_names=(GROUP_MESSAGE_COLLECTOR,),
        discovered_from="oauth_chat_list",
        source_id="oc_group",
        source_chat_id="oc_group",
    )


def _card_event(approval_id: str, resume_token: str, decision_result: str) -> dict[str, object]:
    """构造飞书卡片按钮回调 fixture。"""
    return {
        "schema": "2.0",
        "header": {"event_id": f"evt_{decision_result}", "event_type": "card.action.trigger"},
        "event": {
            "operator": {"open_id": "ou_owner"},
            "action": {
                "value": {
                    "dutyflow_action": "approval_decision",
                    "approval_id": approval_id,
                    "resume_token": resume_token,
                    "decision_result": decision_result,
                }
            },
        },
    }


def _self_test() -> None:
    """运行本文件单元测试。"""
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestFeishuScopeApproval)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    _self_test()
