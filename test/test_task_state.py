# 本文件验证 Step 7 第一版任务状态存储的创建、更新和枚举行为。

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dutyflow.tasks.task_state import TaskStore  # noqa: E402


class TestTaskStore(unittest.TestCase):
    """验证任务 Markdown 存储的最小不变量。"""

    def test_create_task_writes_expected_markdown(self) -> None:
        """创建任务后应写出稳定 frontmatter 和正文 sections。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            store = TaskStore(Path(temp_dir))
            record = store.create_task(
                title="跟进新人入职资料",
                task_id="task_001",
                status="scheduled",
                run_mode="run_at",
                scheduled_for="2026-04-29T09:00:00+08:00",
                summary="等待指定时间后再发送提醒。",
                next_action="等待调度器到时入队。",
            )
            saved = (Path(temp_dir) / "data/tasks/task_001.md").read_text(encoding="utf-8")
        self.assertEqual(record.task_id, "task_001")
        self.assertIn("schema: dutyflow.task_state.v1", saved)
        self.assertIn("status: scheduled", saved)
        self.assertIn("run_mode: run_at", saved)
        self.assertIn("## Summary", saved)
        self.assertIn("等待指定时间后再发送提醒。", saved)
        self.assertIn("## Next Action", saved)
        self.assertIn("等待调度器到时入队。", saved)

    def test_read_task_restores_current_state_fields(self) -> None:
        """读取任务时应恢复 frontmatter 和 Current State 字段。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            store = TaskStore(Path(temp_dir))
            store.create_task(
                title="审批中的文档修改",
                task_id="task_002",
                status="waiting_approval",
                attempt_count="2",
                retry_status="none",
                approval_status="waiting",
                last_result_summary="等待用户确认后继续。",
            )
            loaded = store.read_task("task_002")
        assert loaded is not None
        self.assertEqual(loaded.status, "waiting_approval")
        self.assertEqual(loaded.attempt_count, "2")
        self.assertEqual(loaded.approval_status, "waiting")
        self.assertEqual(loaded.last_result_summary, "等待用户确认后继续。")

    def test_update_task_rewrites_frontmatter_and_sections(self) -> None:
        """更新任务时应同时刷新状态字段和正文 section。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            store = TaskStore(Path(temp_dir))
            store.create_task(title="异步网络调研", task_id="task_003")
            updated = store.update_task(
                "task_003",
                frontmatter_updates={"status": "running", "execution_profile": "background_research"},
                state_updates={"attempt_count": "1", "last_result_summary": "已开始执行。"},
                section_updates={"next_action": "继续读取网页与整理结果。"},
            )
            loaded = store.read_task("task_003")
        assert loaded is not None
        self.assertEqual(updated.status, "running")
        self.assertEqual(updated.execution_profile, "background_research")
        self.assertEqual(loaded.attempt_count, "1")
        self.assertEqual(loaded.last_result_summary, "已开始执行。")
        self.assertEqual(loaded.next_action, "继续读取网页与整理结果。")

    def test_list_tasks_returns_all_records_sorted(self) -> None:
        """枚举任务时应返回全部任务记录。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            store = TaskStore(Path(temp_dir))
            store.create_task(title="任务 A", task_id="task_010")
            store.create_task(title="任务 B", task_id="task_011")
            records = store.list_tasks()
        self.assertEqual([item.task_id for item in records], ["task_010", "task_011"])


def _self_test() -> None:
    """运行本文件单元测试。"""
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestTaskStore)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    _self_test()
