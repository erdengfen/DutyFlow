# 本文件验证审批创建工具的最小校验、审批落盘、中断留痕与任务状态联动。

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
from dutyflow.agent.tools.logic.approval_tools.create_approval_request import (  # noqa: E402
    CreateApprovalRequestTool,
)
from dutyflow.agent.tools.registry import create_runtime_tool_registry  # noqa: E402
from dutyflow.agent.tools.types import ToolCall  # noqa: E402
from dutyflow.approval import ApprovalStore, TaskInterruptStore  # noqa: E402
from dutyflow.tasks import TaskStore  # noqa: E402


class TestApprovalRequestTools(unittest.TestCase):
    """验证审批创建工具对任务、审批和中断三层存储的联动。"""

    def test_create_approval_request_writes_records_and_updates_task(self) -> None:
        """审批创建工具应同时写审批记录、中断记录，并把任务切到 waiting_approval。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            task = TaskStore(root).create_task(
                title="补充联系人资料",
                status="queued",
                run_mode="async_now",
                resume_payload="goal=补充资料; success_criteria=生成结论",
            )
            result = CreateApprovalRequestTool().handle(_create_call(task.task_id), _context(root))
            payload = _json_content(result)
            loaded_task = TaskStore(root).read_task(task.task_id)
            approval = ApprovalStore(root).read_approval(str(payload["approval_id"]))
            interrupt = TaskInterruptStore(root).read_interrupt(str(payload["interrupt_id"]))
        self.assertTrue(result.ok)
        self.assertEqual(payload["task_status"], "waiting_approval")
        self.assertEqual(payload["approval_status"], "waiting")
        self.assertTrue(str(payload["resume_token"]).startswith("resume_"))
        self.assertIsNotNone(loaded_task)
        assert loaded_task is not None
        self.assertEqual(loaded_task.status, "waiting_approval")
        self.assertEqual(loaded_task.approval_status, "waiting")
        self.assertEqual(loaded_task.approval_id, str(payload["approval_id"]))
        self.assertEqual(loaded_task.resume_point, "knowledge_write")
        self.assertIsNotNone(approval)
        assert approval is not None
        self.assertEqual(approval.status, "waiting")
        self.assertEqual(approval.resume_token, str(payload["resume_token"]))
        self.assertIsNotNone(interrupt)
        assert interrupt is not None
        self.assertEqual(interrupt.approval_id, approval.approval_id)
        self.assertEqual(interrupt.resume_token, str(payload["resume_token"]))

    def test_create_approval_request_rejects_unknown_task(self) -> None:
        """审批创建工具应拒绝不存在的 task_id。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            result = CreateApprovalRequestTool().handle(_create_call("task_missing"), _context(root))
        self.assertFalse(result.ok)
        self.assertEqual(result.error_kind, "invalid_approval_request_input")
        self.assertIn("task not found", result.content)

    def test_create_approval_request_rejects_invalid_expire_time(self) -> None:
        """审批创建工具应拒绝非 ISO-8601 的 expires_at。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            task = TaskStore(root).create_task(title="补充联系人资料")
            result = CreateApprovalRequestTool().handle(
                _create_call(task.task_id, expires_at="tomorrow"),
                _context(root),
            )
        self.assertFalse(result.ok)
        self.assertEqual(result.error_kind, "invalid_approval_request_input")
        self.assertIn("expires_at must be an ISO-8601 datetime", result.content)


def _create_call(task_id: str, *, expires_at: str = "2026-05-01T10:00:00+08:00") -> ToolCall:
    """构造 create_approval_request 调用。"""
    return ToolCall(
        "tool_approval_create_001",
        "create_approval_request",
        {
            "task_id": task_id,
            "requested_action": "knowledge_write",
            "risk_level": "high",
            "request": "需要把新人信息写入联系人知识库。",
            "reason": "该动作会修改本地知识记录。",
            "risk": "可能写入错误关系信息。",
            "original_action_kind": "knowledge_write",
            "original_tool_name": "add_contact_knowledge",
            "original_tool_input_preview": "contact_id=contact_001",
            "expires_at": expires_at,
            "context_id": "ctx_approval_001",
            "trace_id": "trace_approval_001",
        },
        0,
        0,
    )


def _context(root: Path) -> ToolUseContext:
    """构造审批创建工具的测试上下文。"""
    return ToolUseContext(
        "query_approval_request_001",
        root,
        create_initial_agent_state("query_approval_request_001", "hello"),
        create_runtime_tool_registry(),
    )


def _json_content(result) -> dict[str, object]:
    """把工具 JSON 内容转换为字典。"""
    return json.loads(result.content)


def _self_test() -> None:
    """运行本文件单元测试。"""
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestApprovalRequestTools)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    _self_test()
