# 本文件验证 Step 7 第一版任务中断记录的创建、读取、查找和枚举行为。

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dutyflow.approval.task_interrupt import TaskInterruptStore  # noqa: E402


class TestTaskInterruptStore(unittest.TestCase):
    """验证任务中断记录 Markdown 存储的最小不变量。"""

    def test_create_interrupt_writes_expected_markdown(self) -> None:
        """创建中断记录后应写出稳定 frontmatter 和 Resume Context。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            store = TaskInterruptStore(Path(temp_dir))
            record = store.create_interrupt(
                approval_id="approval_001",
                task_id="task_001",
                original_tool_name="write_doc",
                original_tool_input_preview="doc=weekly_report",
                original_action_kind="document_write",
                context_id="ctx_001",
                trace_id="trace_001",
                resume_token="resume_001",
                expires_at="2026-04-30T12:00:00+08:00",
                interrupt_id="interrupt_001",
                summary="等待审批完成后恢复原文档写入动作。",
            )
            saved = (
                Path(temp_dir) / "data/approvals/interrupts/interrupt_001.md"
            ).read_text(encoding="utf-8")
        self.assertEqual(record.interrupt_id, "interrupt_001")
        self.assertIn("schema: dutyflow.task_interrupt.v1", saved)
        self.assertIn("approval_id: approval_001", saved)
        self.assertIn("resume_token: resume_001", saved)
        self.assertIn("等待审批完成后恢复原文档写入动作。", saved)

    def test_read_interrupt_restores_resume_context(self) -> None:
        """读取中断记录时应恢复核心恢复字段。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            store = TaskInterruptStore(Path(temp_dir))
            store.create_interrupt(
                approval_id="approval_002",
                task_id="task_002",
                original_tool_name="send_message",
                original_tool_input_preview="text=hello",
                original_action_kind="feishu_feedback",
                context_id="ctx_002",
                trace_id="trace_002",
                resume_token="resume_002",
                expires_at="2026-05-01T10:00:00+08:00",
                interrupt_id="interrupt_002",
            )
            loaded = store.read_interrupt("interrupt_002")
        assert loaded is not None
        self.assertEqual(loaded.approval_id, "approval_002")
        self.assertEqual(loaded.task_id, "task_002")
        self.assertEqual(loaded.original_tool_name, "send_message")
        self.assertEqual(loaded.original_tool_input_preview, "text=hello")
        self.assertEqual(loaded.resume_token, "resume_002")

    def test_find_by_approval_id_and_resume_token(self) -> None:
        """应可按 approval_id 和 resume_token 查回唯一记录。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            store = TaskInterruptStore(Path(temp_dir))
            store.create_interrupt(
                approval_id="approval_010",
                task_id="task_010",
                original_tool_name="knowledge_write",
                original_tool_input_preview="contact=zhangsan",
                original_action_kind="knowledge_write",
                context_id="ctx_010",
                trace_id="trace_010",
                resume_token="resume_010",
                expires_at="2026-05-02T09:00:00+08:00",
                interrupt_id="interrupt_010",
            )
            by_approval = store.find_by_approval_id("approval_010")
            by_token = store.find_by_resume_token("resume_010")
        assert by_approval is not None
        assert by_token is not None
        self.assertEqual(by_approval.interrupt_id, "interrupt_010")
        self.assertEqual(by_token.interrupt_id, "interrupt_010")

    def test_list_interrupts_returns_all_records(self) -> None:
        """枚举中断记录时应返回全部已落盘记录。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            store = TaskInterruptStore(Path(temp_dir))
            store.create_interrupt(
                approval_id="approval_020",
                task_id="task_020",
                original_tool_name="tool_a",
                original_tool_input_preview="a",
                original_action_kind="action_a",
                context_id="ctx_020",
                trace_id="trace_020",
                resume_token="resume_020",
                expires_at="2026-05-03T09:00:00+08:00",
                interrupt_id="interrupt_020",
            )
            store.create_interrupt(
                approval_id="approval_021",
                task_id="task_021",
                original_tool_name="tool_b",
                original_tool_input_preview="b",
                original_action_kind="action_b",
                context_id="ctx_021",
                trace_id="trace_021",
                resume_token="resume_021",
                expires_at="2026-05-03T10:00:00+08:00",
                interrupt_id="interrupt_021",
            )
            records = store.list_interrupts()
        self.assertEqual([item.interrupt_id for item in records], ["interrupt_020", "interrupt_021"])


def _self_test() -> None:
    """运行本文件单元测试。"""
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestTaskInterruptStore)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    _self_test()
