# 本文件验证 Step 7 第一版审批记录存储的创建、读取、列举和完成态写入。

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dutyflow.approval.approval_flow import ApprovalStore  # noqa: E402


class TestApprovalStore(unittest.TestCase):
    """验证审批 Markdown 存储的最小不变量。"""

    def test_create_approval_writes_pending_markdown(self) -> None:
        """创建审批后应写入 pending 目录并包含标准 frontmatter。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ApprovalStore(Path(temp_dir))
            record = store.create_approval(
                task_id="task_001",
                requested_action="knowledge_write",
                risk_level="high",
                request="需要把联系人写入知识库。",
                reason="该动作会修改本地知识记录。",
                risk="可能写入错误的联系人信息。",
                approval_id="approval_001",
            )
            saved = (
                Path(temp_dir) / "data/approvals/pending/approval_001.md"
            ).read_text(encoding="utf-8")
        self.assertEqual(record.status, "waiting")
        self.assertIn("schema: dutyflow.approval_record.v1", saved)
        self.assertIn("status: waiting", saved)
        self.assertIn("## Request", saved)
        self.assertIn("需要把联系人写入知识库。", saved)

    def test_read_approval_restores_resume_and_decision_fields(self) -> None:
        """读取审批时应恢复 Resume Context 和 User Decision 字段。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ApprovalStore(Path(temp_dir))
            store.create_approval(
                task_id="task_002",
                requested_action="document_write",
                risk_level="high",
                request="需要改写文档。",
                reason="该动作会写入飞书文档。",
                risk="可能造成内容覆盖。",
                approval_id="approval_002",
                original_action="rewrite_doc",
                original_tool_name="write_feishu_doc",
                original_tool_input_preview="doc=weekly_report",
                context_id="ctx_001",
                trace_id="trace_001",
            )
            loaded = store.read_approval("approval_002")
        assert loaded is not None
        self.assertEqual(loaded.original_action, "rewrite_doc")
        self.assertEqual(loaded.original_tool_name, "write_feishu_doc")
        self.assertEqual(loaded.context_id, "ctx_001")
        self.assertEqual(loaded.trace_id, "trace_001")
        self.assertEqual(loaded.decision_result, "")

    def test_resolve_approval_moves_record_to_completed_directory(self) -> None:
        """审批完成后应写入 completed 目录并移除 pending 文件。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = ApprovalStore(root)
            store.create_approval(
                task_id="task_003",
                requested_action="send_message",
                risk_level="medium",
                request="需要向外发送消息。",
                reason="代表用户表达立场。",
                risk="可能发送错误内容。",
                approval_id="approval_003",
            )
            resolved = store.resolve_approval(
                "approval_003",
                result="approved",
                decided_by="user_001",
                comment="可以发送",
            )
            loaded = store.read_approval("approval_003")
            pending_exists = (root / "data/approvals/pending/approval_003.md").exists()
            completed_exists = (root / "data/approvals/completed/approval_003.md").exists()
        assert loaded is not None
        self.assertEqual(resolved.status, "approved")
        self.assertEqual(loaded.path.parent.name, "completed")
        self.assertEqual(loaded.decision_result, "approved")
        self.assertEqual(loaded.decided_by, "user_001")
        self.assertEqual(loaded.comment, "可以发送")
        self.assertFalse(pending_exists)
        self.assertTrue(completed_exists)

    def test_list_pending_and_completed_approvals_are_separated(self) -> None:
        """待审批和已完成审批应分开列举。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ApprovalStore(Path(temp_dir))
            store.create_approval(
                task_id="task_010",
                requested_action="knowledge_write",
                risk_level="high",
                request="A",
                reason="A",
                risk="A",
                approval_id="approval_010",
            )
            store.create_approval(
                task_id="task_011",
                requested_action="document_write",
                risk_level="high",
                request="B",
                reason="B",
                risk="B",
                approval_id="approval_011",
            )
            store.resolve_approval(
                "approval_011",
                result="rejected",
                decided_by="user_002",
            )
            pending = store.list_pending_approvals()
            completed = store.list_completed_approvals()
        self.assertEqual([item.approval_id for item in pending], ["approval_010"])
        self.assertEqual([item.approval_id for item in completed], ["approval_011"])


def _self_test() -> None:
    """运行本文件单元测试。"""
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestApprovalStore)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    _self_test()
