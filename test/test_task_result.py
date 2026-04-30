# 本文件验证后台任务结果 Markdown 的占位创建、更新和读回。

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dutyflow.storage.file_store import FileStore  # noqa: E402
from dutyflow.storage.markdown_store import MarkdownStore  # noqa: E402
from dutyflow.tasks.task_result import TaskResultStore  # noqa: E402
from dutyflow.tasks.task_state import TaskStore  # noqa: E402


class TestTaskResultStore(unittest.TestCase):
    """验证 TaskResultStore 的稳定文件结构和幂等行为。"""

    def test_create_placeholder_writes_result_markdown(self) -> None:
        """创建任务后应能生成一条独立的结果占位文件。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            task = TaskStore(root).create_task(title="整理资料", task_id="task_result_001")
            result = TaskResultStore(root).create_placeholder(task)
            document = MarkdownStore(FileStore(root)).read_document(result.path)
        self.assertEqual(result.result_id, "result_task_result_001")
        self.assertEqual(result.status, "placeholder")
        self.assertEqual(result.task_status, "queued")
        self.assertEqual(result.source_task_file, "data/tasks/task_result_001.md")
        self.assertEqual(document.frontmatter["schema"], "dutyflow.task_result.v1")
        self.assertEqual(document.frontmatter["task_id"], "task_result_001")

    def test_update_result_can_be_read_back(self) -> None:
        """执行结果更新后，应能按任务 ID 读回用户可见文本和执行元信息。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            task = TaskStore(root).create_task(title="整理资料", task_id="task_result_002")
            store = TaskResultStore(root)
            updated = store.update_result(
                task,
                status="completed",
                summary="资料已经整理完成。",
                user_visible_final_text="可以回给用户的结果。",
                stop_reason="stop",
                tool_result_count=2,
                query_id="bg_task_task_result_002",
                raw_result="完整执行结果。",
            )
            loaded = store.read_result("task_result_002")
        self.assertEqual(updated.status, "completed")
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.summary, "资料已经整理完成。")
        self.assertEqual(loaded.user_visible_final_text, "可以回给用户的结果。")
        self.assertEqual(loaded.stop_reason, "stop")
        self.assertEqual(loaded.tool_result_count, "2")
        self.assertEqual(loaded.query_id, "bg_task_task_result_002")
        self.assertEqual(loaded.raw_result, "完整执行结果。")

    def test_create_placeholder_is_idempotent(self) -> None:
        """重复创建占位不应覆盖已有执行结果。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            task = TaskStore(root).create_task(title="整理资料", task_id="task_result_003")
            store = TaskResultStore(root)
            store.update_result(
                task,
                status="running",
                summary="正在执行。",
                user_visible_final_text="",
                stop_reason="",
                tool_result_count=0,
                query_id="bg_task_task_result_003",
                raw_result="",
            )
            result = store.create_placeholder(task)
        self.assertEqual(result.status, "running")
        self.assertEqual(result.summary, "正在执行。")
        self.assertEqual(result.query_id, "bg_task_task_result_003")


def _self_test() -> None:
    """运行本文件单元测试。"""
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestTaskResultStore)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    _self_test()
