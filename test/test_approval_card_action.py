# 本文件验证飞书审批卡片按钮回调能桥接到审批恢复链。

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
from dutyflow.approval.approval_request_intake import ApprovalRequestIntakeService  # noqa: E402
from dutyflow.tasks.task_state import TaskStore  # noqa: E402


class TestApprovalCardActionService(unittest.TestCase):
    """验证卡片按钮 value 到审批恢复输入的转换。"""

    def test_approved_button_resumes_waiting_approval_task(self) -> None:
        """点击批准按钮后，审批应完成，任务应回到 queued。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            task_store = TaskStore(root)
            created = _create_waiting_approval(root, task_store)
            result = ApprovalCardActionService(root).handle_raw_event(
                _card_event(created.approval_id, created.resume_token, "approved")
            )
            task = task_store.read_task(created.task_id)
        self.assertTrue(result.ok)
        self.assertEqual(result.status, "approval_resumed")
        self.assertEqual(result.decision_result, "approved")
        self.assertEqual(result.toast_type, "success")
        self.assertIsNotNone(task)
        assert task is not None
        self.assertEqual(task.status, "queued")
        self.assertEqual(task.approval_status, "approved")

    def test_rejected_button_cancels_waiting_approval_task(self) -> None:
        """点击拒绝按钮后，任务不应恢复执行。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            task_store = TaskStore(root)
            created = _create_waiting_approval(root, task_store)
            result = ApprovalCardActionService(root).handle_raw_event(
                _card_event(created.approval_id, created.resume_token, "rejected")
            )
            task = task_store.read_task(created.task_id)
        self.assertTrue(result.ok)
        self.assertEqual(result.decision_result, "rejected")
        self.assertIsNotNone(task)
        assert task is not None
        self.assertEqual(task.status, "cancelled")
        self.assertEqual(task.approval_status, "rejected")

    def test_bad_resume_token_returns_error_toast(self) -> None:
        """错误 resume_token 不应完成审批，应返回错误 toast。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            task_store = TaskStore(root)
            created = _create_waiting_approval(root, task_store)
            result = ApprovalCardActionService(root).handle_raw_event(
                _card_event(created.approval_id, "resume_wrong", "approved")
            )
            task = task_store.read_task(created.task_id)
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "approval_resume_failed")
        self.assertEqual(result.toast_type, "error")
        self.assertIsNotNone(task)
        assert task is not None
        self.assertEqual(task.status, "waiting_approval")


def _create_waiting_approval(root: Path, task_store: TaskStore):
    """创建一条等待审批的任务和审批记录。"""
    task = task_store.create_task(
        title="补充联系人资料",
        status="queued",
        resume_payload="goal=补充资料; success_criteria=生成结论",
    )
    return ApprovalRequestIntakeService(root, task_store=task_store).create_request(
        {
            "task_id": task.task_id,
            "requested_action": "knowledge_write",
            "risk_level": "high",
            "request": "需要把新人信息写入联系人知识库。",
            "reason": "该动作会修改本地知识记录。",
            "risk": "可能写入错误关系信息。",
            "original_action_kind": "knowledge_write",
            "original_tool_name": "add_contact_knowledge",
            "original_tool_input_preview": "contact_id=contact_001",
            "expires_at": "2026-05-01T10:00:00+08:00",
        }
    )


def _card_event(approval_id: str, resume_token: str, decision_result: str) -> dict[str, object]:
    """构造飞书卡片按钮回调 fixture。"""
    return {
        "schema": "2.0",
        "header": {
            "event_id": f"evt_card_{decision_result}",
            "event_type": "card.action.trigger",
            "tenant_key": "tenant_demo",
            "app_id": "app_demo",
        },
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
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestApprovalCardActionService)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    _self_test()
