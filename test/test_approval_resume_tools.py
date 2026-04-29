# 本文件验证审批恢复工具对审批完成态、任务状态和恢复 token 的最小处理。

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dutyflow.agent.state import create_initial_agent_state  # noqa: E402
from dutyflow.agent.tools.context import ToolUseContext  # noqa: E402
from dutyflow.agent.tools.logic.approval_tools.resume_after_approval import ResumeAfterApprovalTool  # noqa: E402
from dutyflow.agent.tools.registry import create_runtime_tool_registry  # noqa: E402
from dutyflow.agent.tools.types import ToolCall  # noqa: E402
from dutyflow.approval import ApprovalRequestIntakeService, ApprovalStore  # noqa: E402
from dutyflow.tasks import TaskStore  # noqa: E402


class TestApprovalResumeTools(unittest.TestCase):
    """验证审批恢复工具的状态流转边界。"""

    def test_approved_decision_moves_task_back_to_queued(self) -> None:
        """审批通过后应完成审批记录，并把任务放回 queued 等待后台 worker。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            task, approval_id, resume_token = _create_waiting_approval(root)
            result = ResumeAfterApprovalTool().handle(
                _resume_call(approval_id, "approved", resume_token=resume_token),
                _context(root),
            )
            payload = _json_content(result)
            loaded_task = TaskStore(root).read_task(task.task_id)
            completed = ApprovalStore(root).read_approval(approval_id)
            pending_path = root / "data" / "approvals" / "pending" / f"{approval_id}.md"
        self.assertTrue(result.ok)
        self.assertTrue(payload["resumed"])
        self.assertEqual(payload["task_status"], "queued")
        self.assertIsNotNone(loaded_task)
        assert loaded_task is not None
        self.assertEqual(loaded_task.status, "queued")
        self.assertEqual(loaded_task.approval_status, "approved")
        self.assertIsNotNone(completed)
        assert completed is not None
        self.assertEqual(completed.status, "approved")
        self.assertFalse(pending_path.exists())

    def test_rejected_decision_cancels_task_without_resuming(self) -> None:
        """审批拒绝后应完成审批记录，并取消任务恢复。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            task, approval_id, resume_token = _create_waiting_approval(root)
            result = ResumeAfterApprovalTool().handle(
                _resume_call(approval_id, "rejected", resume_token=resume_token),
                _context(root),
            )
            payload = _json_content(result)
            loaded_task = TaskStore(root).read_task(task.task_id)
        self.assertTrue(result.ok)
        self.assertFalse(payload["resumed"])
        self.assertEqual(payload["task_status"], "cancelled")
        self.assertIsNotNone(loaded_task)
        assert loaded_task is not None
        self.assertEqual(loaded_task.status, "cancelled")
        self.assertEqual(loaded_task.approval_status, "rejected")

    def test_expired_decision_marks_task_expired(self) -> None:
        """审批超时后应把任务标记为 expired，等待后续交互再确认。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            task, approval_id, resume_token = _create_waiting_approval(root)
            result = ResumeAfterApprovalTool().handle(
                _resume_call(approval_id, "expired", resume_token=resume_token),
                _context(root),
            )
            payload = _json_content(result)
            loaded_task = TaskStore(root).read_task(task.task_id)
        self.assertTrue(result.ok)
        self.assertFalse(payload["resumed"])
        self.assertEqual(payload["task_status"], "expired")
        self.assertIsNotNone(loaded_task)
        assert loaded_task is not None
        self.assertEqual(loaded_task.status, "expired")
        self.assertEqual(loaded_task.approval_status, "expired")

    def test_resume_token_mismatch_is_rejected(self) -> None:
        """传入错误 resume_token 时不应完成审批。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _, approval_id, _ = _create_waiting_approval(root)
            result = ResumeAfterApprovalTool().handle(
                _resume_call(approval_id, "approved", resume_token="resume_wrong"),
                _context(root),
            )
            approval = ApprovalStore(root).read_approval(approval_id)
        self.assertFalse(result.ok)
        self.assertEqual(result.error_kind, "invalid_approval_resume_input")
        self.assertIn("resume_token does not match approval", result.content)
        self.assertIsNotNone(approval)
        assert approval is not None
        self.assertEqual(approval.status, "waiting")


def _create_waiting_approval(root: Path):
    """创建一条处于 waiting_approval 状态的任务和对应审批记录。"""
    task_store = TaskStore(root)
    task = task_store.create_task(
        title="补充联系人资料",
        status="queued",
        run_mode="async_now",
        resume_payload="goal=补充资料; success_criteria=生成结论",
    )
    created = ApprovalRequestIntakeService(root, task_store=task_store).create_request(
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
    loaded = task_store.read_task(task.task_id)
    assert loaded is not None
    return loaded, created.approval_id, created.resume_token


def _resume_call(approval_id: str, decision_result: str, *, resume_token: str = "") -> ToolCall:
    """构造 resume_after_approval 调用。"""
    tool_input = {
        "approval_id": approval_id,
        "decision_result": decision_result,
        "decided_by": "owner_open_id",
        "comment": "manual decision",
    }
    if resume_token:
        tool_input["resume_token"] = resume_token
    return ToolCall("tool_approval_resume_001", "resume_after_approval", tool_input, 0, 0)


def _context(root: Path) -> ToolUseContext:
    """构造审批恢复工具的测试上下文。"""
    return ToolUseContext(
        "query_approval_resume_001",
        root,
        create_initial_agent_state("query_approval_resume_001", "hello"),
        create_runtime_tool_registry(),
    )


def _json_content(result) -> dict[str, object]:
    """把工具 JSON 内容转换为字典。"""
    return json.loads(result.content)


def _self_test() -> None:
    """运行本文件单元测试。"""
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestApprovalResumeTools)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    _self_test()
