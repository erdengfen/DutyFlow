# 本文件验证正式 runtime service 骨架的启动、入队、消费和停止行为。

from __future__ import annotations

from pathlib import Path
import sys
import threading
import time
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dutyflow.agent.runtime_service import RuntimeService, RuntimeWorkItem  # noqa: E402


class TestRuntimeService(unittest.TestCase):
    """验证正式 runtime service 第一版骨架的核心不变量。"""

    def test_start_exposes_running_worker_state(self) -> None:
        """启动后应暴露 worker 已运行的状态快照。"""
        service = RuntimeService(queue_poll_seconds=0.01)
        state = service.start()
        self.assertEqual(state.status, "running")
        self.assertTrue(state.worker_started)
        self.assertTrue(state.worker_alive)
        stopped = service.stop()
        self.assertEqual(stopped.status, "stopped")

    def test_enqueue_runs_single_work_item(self) -> None:
        """入队后单 worker 应消费任务并累计 processed_count。"""
        done = threading.Event()
        handled: list[str] = []

        def _handler(work_item: RuntimeWorkItem) -> None:
            handled.append(work_item.perception_id)
            done.set()

        service = RuntimeService(_handler, queue_poll_seconds=0.01)
        service.start()
        work_item = service.enqueue_perception(
            {
                "perception_id": "per_001",
                "perception_file": "data/perception/2026-04-28/per_001.md",
                "trigger_kind": "p2p_text",
            }
        )
        self.assertEqual(work_item.perception_id, "per_001")
        self.assertTrue(done.wait(timeout=1.0))
        state = _wait_for_state(service, lambda item: item.processed_count == 1)
        self.assertEqual(handled, ["per_001"])
        self.assertEqual(state.latest_action, "processed")
        service.stop()

    def test_handler_failure_increments_failed_count(self) -> None:
        """handler 抛异常时应累加 failed_count，而不是杀死 worker。"""

        def _handler(work_item: RuntimeWorkItem) -> None:
            raise RuntimeError(f"failed {work_item.perception_id}")

        service = RuntimeService(_handler, queue_poll_seconds=0.01)
        service.start()
        service.enqueue_perception({"perception_id": "per_fail", "trigger_kind": "p2p_text"})
        state = _wait_for_state(service, lambda item: item.failed_count == 1)
        self.assertEqual(state.latest_action, "failed")
        self.assertIn("per_fail", state.latest_error)
        self.assertTrue(state.worker_alive)
        service.stop()

    def test_enqueue_requires_running_worker(self) -> None:
        """未启动 worker 时不允许把任务直接塞进队列。"""
        service = RuntimeService(queue_poll_seconds=0.01)
        with self.assertRaises(RuntimeError):
            service.enqueue_perception({"perception_id": "per_001", "trigger_kind": "p2p_text"})


def _wait_for_state(service: RuntimeService, predicate) -> object:
    """轮询等待状态满足断言，避免测试直接依赖 sleep。"""
    deadline = time.time() + 1.0
    latest = service.get_state()
    while time.time() < deadline:
        latest = service.get_state()
        if predicate(latest):
            return latest
        time.sleep(0.02)
    raise AssertionError(f"runtime state did not satisfy predicate: {latest}")


def _self_test() -> None:
    """运行本文件单元测试。"""
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestRuntimeService)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    _self_test()
