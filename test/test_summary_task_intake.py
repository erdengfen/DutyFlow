# 本文件验证 SummaryTaskIntakeService 的总结任务创建、去重和冷却行为。

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dutyflow.feishu.summary_task_intake import (  # noqa: E402
    SUMMARY_COOLDOWN_HOURS,
    SummaryTaskIntakeService,
    _cooldown_expired,
)
from dutyflow.feishu.ambient_context import (  # noqa: E402
    AmbientContextRecord,
    AmbientContextStore,
    AmbientDocLink,
)
from dutyflow.tasks.task_state import TaskStore  # noqa: E402


class TestSummaryTaskIntakeService(unittest.TestCase):
    """验证系统预制总结任务的创建和冷却去重。"""

    def test_creates_all_four_summary_types_on_first_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = SummaryTaskIntakeService(Path(tmp))
            result = service.create_due_summary_tasks(lookback_hours=1)

        self.assertTrue(result.ok)
        self.assertEqual(result.tasks_created, 4)

    def test_tasks_are_written_to_task_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_store = TaskStore(root)
            service = SummaryTaskIntakeService(root, task_store=task_store)
            service.create_due_summary_tasks(lookback_hours=1)
            tasks = task_store.list_tasks()

        self.assertEqual(len(tasks), 4)

    def test_task_status_is_queued(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_store = TaskStore(root)
            service = SummaryTaskIntakeService(root, task_store=task_store)
            service.create_due_summary_tasks(lookback_hours=1)
            tasks = task_store.list_tasks()

        self.assertTrue(all(t.status == "queued" for t in tasks))

    def test_task_resolved_tools_contains_read_context_ref(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_store = TaskStore(root)
            service = SummaryTaskIntakeService(root, task_store=task_store)
            service.create_due_summary_tasks(lookback_hours=1)
            tasks = task_store.list_tasks()

        for task in tasks:
            self.assertIn("read_context_ref", task.resolved_tools)

    def test_doc_summary_task_includes_feishu_read_doc(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_store = TaskStore(root)
            service = SummaryTaskIntakeService(root, task_store=task_store)
            service.create_due_summary_tasks(lookback_hours=1)
            tasks = task_store.list_tasks()

        doc_tasks = [t for t in tasks if "文档" in t.title or "doc" in t.source_id]
        self.assertTrue(any("feishu_read_doc" in t.resolved_tools for t in doc_tasks))

    def test_second_run_within_cooldown_skips_all_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = SummaryTaskIntakeService(Path(tmp))
            service.create_due_summary_tasks(lookback_hours=1)
            result2 = service.create_due_summary_tasks(lookback_hours=1)

        self.assertEqual(result2.tasks_created, 0)
        for r in result2.results:
            self.assertEqual(r.skipped_reason, "cooldown_active")

    def test_watermark_persists_across_instances(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            service1 = SummaryTaskIntakeService(root)
            service1.create_due_summary_tasks(lookback_hours=1)

            service2 = SummaryTaskIntakeService(root)
            result2 = service2.create_due_summary_tasks(lookback_hours=1)

        self.assertEqual(result2.tasks_created, 0)

    def test_context_refs_included_when_ambient_records_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ambient_store = AmbientContextStore(root)
            ambient_store.write(AmbientContextRecord(
                record_id="dm_ref_1",
                source_type="direct_message",
                collector_name="direct_message_collector",
                source_id="msg_1",
                sync_scope_id="oc_1",
                created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                fetched_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                text="重要会议通知",
            ))
            task_store = TaskStore(root)
            service = SummaryTaskIntakeService(root, task_store=task_store, ambient_store=ambient_store)
            service.create_due_summary_tasks(
                summary_types=("dm_summary",), lookback_hours=24
            )
            tasks = task_store.list_tasks()

        self.assertEqual(len(tasks), 1)
        self.assertIn("dm_ref_1", tasks[0].resume_payload)
        self.assertIn("context_ref_type=ambient_context", tasks[0].resume_payload)
        self.assertIn("ref_type=ambient_context", tasks[0].resume_payload)

    def test_empty_context_refs_are_not_a_blocking_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_store = TaskStore(root)
            service = SummaryTaskIntakeService(root, task_store=task_store)
            service.create_due_summary_tasks(
                summary_types=("group_summary",), lookback_hours=1
            )
            tasks = task_store.list_tasks()

        self.assertEqual(len(tasks), 1)
        self.assertIn("context_ref_count=0", tasks[0].resume_payload)
        self.assertIn("不要要求用户补充上下文", tasks[0].resume_payload)

    def test_specific_summary_types_subset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = SummaryTaskIntakeService(Path(tmp))
            result = service.create_due_summary_tasks(
                summary_types=("dm_summary", "group_summary"), lookback_hours=1
            )

        self.assertEqual(result.tasks_created, 2)

    def test_get_last_created_at_returns_empty_before_first_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = SummaryTaskIntakeService(Path(tmp))
            last = service.get_last_created_at("dm_summary")

        self.assertEqual(last, "")

    def test_get_last_created_at_returns_timestamp_after_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = SummaryTaskIntakeService(Path(tmp))
            service.create_due_summary_tasks(summary_types=("dm_summary",), lookback_hours=1)
            last = service.get_last_created_at("dm_summary")

        self.assertNotEqual(last, "")


class TestCooldownExpired(unittest.TestCase):
    """验证冷却判断辅助函数的边界行为。"""

    def test_empty_timestamp_is_expired(self) -> None:
        self.assertTrue(_cooldown_expired("", SUMMARY_COOLDOWN_HOURS))

    def test_recent_timestamp_is_not_expired(self) -> None:
        recent = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.assertFalse(_cooldown_expired(recent, SUMMARY_COOLDOWN_HOURS))

    def test_old_timestamp_is_expired(self) -> None:
        self.assertTrue(_cooldown_expired("2020-01-01T00:00:00+00:00", SUMMARY_COOLDOWN_HOURS))

    def test_invalid_timestamp_is_expired(self) -> None:
        self.assertTrue(_cooldown_expired("not-a-date", SUMMARY_COOLDOWN_HOURS))


if __name__ == "__main__":
    unittest.main()
