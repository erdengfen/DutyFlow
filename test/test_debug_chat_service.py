# 本文件验证非阻塞 /chat 调试服务的启动、入队、消费和失败留痕。

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

from dutyflow.agent.debug_chat_service import ChatDebugService  # noqa: E402


class TestDebugChatService(unittest.TestCase):
    """验证非阻塞 /chat 调试服务的核心不变量。"""

    def test_start_exposes_running_worker_state(self) -> None:
        """启动后应暴露 worker 已运行的状态快照。"""
        service = ChatDebugService(lambda task: task.user_text, queue_poll_seconds=0.01)
        state = service.start()
        self.assertEqual(state.status, "running")
        self.assertTrue(state.worker_started)
        self.assertTrue(state.worker_alive)
        stopped = service.stop()
        self.assertEqual(stopped.status, "stopped")

    def test_enqueue_runs_single_debug_task(self) -> None:
        """入队后单 worker 应消费任务并产出最近结果。"""
        done = threading.Event()

        def _handler(task) -> str:
            done.set()
            return f"done: {task.user_text}"

        service = ChatDebugService(_handler, queue_poll_seconds=0.01)
        service.start()
        task = service.enqueue("ping")
        self.assertTrue(done.wait(timeout=1.0))
        state = _wait_for_state(service, lambda item: item.processed_count == 1)
        latest = service.get_latest_result()
        self.assertEqual(task.user_text, "ping")
        self.assertEqual(state.latest_action, "processed")
        self.assertIsNotNone(latest)
        self.assertEqual(latest.task_status, "completed")
        self.assertIn("done: ping", latest.result_text)
        service.stop()

    def test_handler_failure_increments_failed_count(self) -> None:
        """handler 抛异常时应累计 failed_count 并保留最近错误。"""

        def _handler(task) -> str:
            raise RuntimeError(f"boom {task.user_text}")

        service = ChatDebugService(_handler, queue_poll_seconds=0.01)
        service.start()
        service.enqueue("fail")
        state = _wait_for_state(service, lambda item: item.failed_count == 1)
        latest = service.get_latest_result()
        self.assertEqual(state.latest_action, "failed")
        self.assertIn("boom fail", state.latest_error)
        self.assertIsNotNone(latest)
        self.assertEqual(latest.task_status, "failed")
        self.assertIn("boom fail", latest.error_text)
        self.assertTrue(state.worker_alive)
        service.stop()

    def test_enqueue_requires_running_worker(self) -> None:
        """未启动 worker 时不允许直接提交调试任务。"""
        service = ChatDebugService(lambda task: task.user_text, queue_poll_seconds=0.01)
        with self.assertRaises(RuntimeError):
            service.enqueue("ping")


def _wait_for_state(service: ChatDebugService, predicate) -> object:
    """轮询等待状态满足断言，避免测试直接依赖固定 sleep。"""
    deadline = time.time() + 1.0
    latest = service.get_state()
    while time.time() < deadline:
        latest = service.get_state()
        if predicate(latest):
            return latest
        time.sleep(0.02)
    raise AssertionError(f"chat debug state did not satisfy predicate: {latest}")


def _self_test() -> None:
    """运行本文件单元测试。"""
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestDebugChatService)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    _self_test()
