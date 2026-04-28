# 本文件验证 Step 7 第一版后台任务调度器的扫描、调度和线程行为。

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dutyflow.tasks.task_scheduler import TaskSchedulerService  # noqa: E402
from dutyflow.tasks.task_state import TaskStore  # noqa: E402


class TestTaskSchedulerService(unittest.TestCase):
    """验证后台任务调度器的到时扫描与调度不变量。"""

    def test_scan_due_tasks_returns_only_due_scheduled_tasks(self) -> None:
        """只有已到时的 `scheduled + run_at` 任务应被扫描命中。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            store = TaskStore(Path(temp_dir))
            store.create_task(
                title="past",
                task_id="task_past",
                status="scheduled",
                run_mode="run_at",
                scheduled_for="2026-04-29T09:00:00+08:00",
            )
            store.create_task(
                title="future",
                task_id="task_future",
                status="scheduled",
                run_mode="run_at",
                scheduled_for="2026-04-29T18:00:00+08:00",
            )
            store.create_task(
                title="queued",
                task_id="task_queued",
                status="queued",
                run_mode="async_now",
            )
            service = TaskSchedulerService(
                store,
                time_provider=lambda: datetime.fromisoformat("2026-04-29T10:00:00+08:00"),
            )
            due = service.scan_due_tasks()
        self.assertEqual([item.task_id for item in due], ["task_past"])

    def test_run_once_dispatches_task_and_updates_status(self) -> None:
        """调度一次后，应发出 dispatch 并把任务状态切到 queued。"""
        dispatched: list[str] = []
        with tempfile.TemporaryDirectory() as temp_dir:
            store = TaskStore(Path(temp_dir))
            store.create_task(
                title="needs dispatch",
                task_id="task_001",
                status="scheduled",
                run_mode="run_at",
                scheduled_for="2026-04-29T09:00:00+08:00",
            )
            service = TaskSchedulerService(
                store,
                lambda item: dispatched.append(item.task_id),
                time_provider=lambda: datetime.fromisoformat("2026-04-29T10:00:00+08:00"),
            )
            result = service.run_once()
            loaded = store.read_task("task_001")
        assert loaded is not None
        self.assertEqual([item.task_id for item in result], ["task_001"])
        self.assertEqual(dispatched, ["task_001"])
        self.assertEqual(loaded.status, "queued")
        self.assertEqual(loaded.last_result_summary, "任务已到时，等待后台 worker 执行。")
        self.assertEqual(loaded.next_action, "等待后台 worker 拉起执行。")

    def test_start_runs_background_scheduler_thread(self) -> None:
        """启动后线程应可周期调度一条已到时任务。"""
        done = threading.Event()
        with tempfile.TemporaryDirectory() as temp_dir:
            store = TaskStore(Path(temp_dir))
            store.create_task(
                title="thread dispatch",
                task_id="task_thread",
                status="scheduled",
                run_mode="run_at",
                scheduled_for="2026-04-29T09:00:00+08:00",
            )
            service = TaskSchedulerService(
                store,
                lambda item: done.set(),
                scan_interval_seconds=0.01,
                time_provider=lambda: datetime.fromisoformat("2026-04-29T10:00:00+08:00"),
            )
            state = service.start()
            self.assertEqual(state.status, "running")
            self.assertTrue(done.wait(timeout=1.0))
            state = _wait_for_state(service, lambda item: item.dispatched_count == 1)
            service.stop()
        self.assertEqual(state.latest_action, "dispatched")
        self.assertEqual(state.latest_task_id, "task_thread")

    def test_invalid_scheduled_for_is_ignored(self) -> None:
        """非法时间格式不应打崩扫描，也不应误调度。"""
        dispatched: list[str] = []
        with tempfile.TemporaryDirectory() as temp_dir:
            store = TaskStore(Path(temp_dir))
            store.create_task(
                title="bad time",
                task_id="task_bad_time",
                status="scheduled",
                run_mode="run_at",
                scheduled_for="tomorrow morning",
            )
            service = TaskSchedulerService(
                store,
                lambda item: dispatched.append(item.task_id),
                time_provider=lambda: datetime.fromisoformat("2026-04-29T10:00:00+08:00"),
            )
            due = service.scan_due_tasks()
        self.assertEqual(due, ())
        self.assertEqual(dispatched, [])


def _wait_for_state(service: TaskSchedulerService, predicate) -> object:
    """轮询等待调度器状态满足断言。"""
    deadline = time.time() + 1.0
    latest = service.get_state()
    while time.time() < deadline:
        latest = service.get_state()
        if predicate(latest):
            return latest
        time.sleep(0.02)
    raise AssertionError(f"scheduler state did not satisfy predicate: {latest}")


def _self_test() -> None:
    """运行本文件单元测试。"""
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestTaskSchedulerService)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    _self_test()
