# 本文件验证 Step 7 后台任务 worker 的独立队列、扫盘入队和状态流转。

from __future__ import annotations

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

from dutyflow.agent.background_task_worker import (  # noqa: E402
    BackgroundTaskExecutionResult,
    BackgroundTaskWorker,
)
from dutyflow.agent.model_client import ModelResponse  # noqa: E402
from dutyflow.agent.state import AgentContentBlock  # noqa: E402
from dutyflow.feedback.gateway import FeedbackResult  # noqa: E402
from dutyflow.tasks.task_result import TaskResultStore  # noqa: E402
from dutyflow.tasks.task_state import TaskRecord, TaskStore  # noqa: E402


class TestBackgroundTaskWorker(unittest.TestCase):
    """验证后台任务 worker 与正式 runtime 队列互不耦合。"""

    def test_start_exposes_running_worker_state(self) -> None:
        """启动后应暴露后台 worker 已运行的状态快照。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            worker = BackgroundTaskWorker(
                TaskStore(Path(temp_dir)),
                _completed_handler(threading.Event()),
                queue_poll_seconds=0.01,
            )
            state = worker.start()
            worker.stop()
        self.assertEqual(state.status, "running")
        self.assertTrue(state.worker_started)
        self.assertTrue(state.worker_alive)

    def test_enqueue_task_runs_handler_and_updates_task(self) -> None:
        """显式入队后，worker 应调用注入执行器并写回任务状态。"""
        done = threading.Event()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = TaskStore(root)
            store.create_task(title="background", task_id="task_bg_001", status="queued")
            worker = BackgroundTaskWorker(store, _completed_handler(done), queue_poll_seconds=0.01)
            worker.start()
            work_item = worker.enqueue_task("task_bg_001", source="test")
            state = _wait_for_state(worker, lambda item: item.processed_count == 1)
            loaded = store.read_task("task_bg_001")
            worker.stop()
        self.assertTrue(done.is_set())
        self.assertEqual(work_item.task_id, "task_bg_001")
        self.assertEqual(state.latest_action, "processed")
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.status, "completed")
        self.assertEqual(loaded.retry_status, "done")

    def test_worker_scans_existing_queued_task(self) -> None:
        """即使任务不是通过正式 runtime 创建，worker 也能扫到 queued 任务。"""
        done = threading.Event()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = TaskStore(root)
            store.create_task(title="scan", task_id="task_scan", status="queued")
            worker = BackgroundTaskWorker(
                store,
                _completed_handler(done),
                queue_poll_seconds=0.01,
                ready_scan_interval_seconds=0.01,
            )
            worker.start()
            state = _wait_for_state(worker, lambda item: item.processed_count == 1)
            loaded = store.read_task("task_scan")
            worker.stop()
        self.assertTrue(done.is_set())
        self.assertEqual(state.latest_task_id, "task_scan")
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.status, "completed")

    def test_default_worker_runs_background_subagent_executor(self) -> None:
        """未注入测试 handler 时，worker 应通过后台 subagent executor 执行任务。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = TaskStore(root)
            store.create_task(title="subagent", task_id="task_subagent", status="queued")
            model = _FakeModelClient("后台 subagent 已完成任务。")
            feedback = _FakeFeedbackGateway()
            worker = BackgroundTaskWorker(
                store,
                model_client=model,
                feedback_gateway=feedback,
                queue_poll_seconds=0.01,
            )
            worker.start()
            worker.enqueue_task("task_subagent", source="test")
            state = _wait_for_state(worker, lambda item: item.processed_count == 1)
            loaded = store.read_task("task_subagent")
            result = TaskResultStore(root).read_result("task_subagent")
            worker.stop()
        self.assertEqual(state.latest_action, "processed")
        self.assertEqual(model.call_count, 1)
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.status, "completed")
        self.assertEqual(loaded.retry_status, "done")
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.user_visible_final_text, "后台 subagent 已完成任务。")
        self.assertEqual(feedback.sent_texts, [("owner", "后台 subagent 已完成任务。")])

    def test_worker_sends_completion_feedback_to_task_source_chat(self) -> None:
        """任务带 source_id 时，完成结果应回推到原始飞书会话。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = TaskStore(root)
            store.create_task(
                title="source chat",
                task_id="task_source_chat",
                status="queued",
                source_id="oc_task_source",
            )
            feedback = _FakeFeedbackGateway()
            worker = BackgroundTaskWorker(
                store,
                model_client=_FakeModelClient("这是后台任务结果。"),
                feedback_gateway=feedback,
                queue_poll_seconds=0.01,
            )
            worker.start()
            worker.enqueue_task("task_source_chat", source="test")
            _wait_for_state(worker, lambda item: item.processed_count == 1)
            loaded = store.read_task("task_source_chat")
            worker.stop()
        self.assertEqual(feedback.sent_texts, [("oc_task_source", "这是后台任务结果。")])
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertIn("已通过飞书回推", loaded.next_action)

    def test_feedback_failure_keeps_task_completed(self) -> None:
        """结果回推失败时，不应把已完成任务改成执行失败。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = TaskStore(root)
            store.create_task(title="feedback fail", task_id="task_feedback_fail", status="queued")
            worker = BackgroundTaskWorker(
                store,
                model_client=_FakeModelClient("完成但发送失败。"),
                feedback_gateway=_FailingFeedbackGateway(),
                queue_poll_seconds=0.01,
            )
            worker.start()
            worker.enqueue_task("task_feedback_fail", source="test")
            state = _wait_for_state(worker, lambda item: item.processed_count == 1)
            loaded = store.read_task("task_feedback_fail")
            worker.stop()
        self.assertEqual(state.failed_count, 0)
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.status, "completed")
        self.assertIn("结果回推失败", loaded.next_action)

    def test_default_worker_requires_model_client(self) -> None:
        """正式默认执行面缺少模型客户端时应显式失败，而不是退回占位 handler。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(ValueError):
                BackgroundTaskWorker(TaskStore(Path(temp_dir)))

    def test_handler_failure_marks_task_failed_but_keeps_worker_alive(self) -> None:
        """执行器异常时任务应变为 failed，worker 线程不应退出。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = TaskStore(root)
            store.create_task(title="fail", task_id="task_fail", status="queued")
            worker = BackgroundTaskWorker(store, _failing_handler, queue_poll_seconds=0.01)
            worker.start()
            worker.enqueue_task("task_fail", source="test")
            state = _wait_for_state(worker, lambda item: item.failed_count == 1)
            loaded = store.read_task("task_fail")
            worker.stop()
        self.assertTrue(state.worker_alive)
        self.assertEqual(state.latest_action, "failed")
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.status, "failed")
        self.assertEqual(loaded.retry_status, "failed")


def _completed_handler(done: threading.Event):
    """构造测试用执行器，返回 completed 状态。"""

    def _handler(task: TaskRecord) -> BackgroundTaskExecutionResult:
        done.set()
        return BackgroundTaskExecutionResult(
            status="completed",
            retry_status="done",
            last_result_summary=f"completed {task.task_id}",
            next_action="无。",
        )

    return _handler


def _failing_handler(task: TaskRecord) -> BackgroundTaskExecutionResult:
    """构造测试用失败执行器。"""
    raise RuntimeError(f"boom {task.task_id}")


class _FakeModelClient:
    """为默认 worker 路径提供最小模型响应。"""

    def __init__(self, final_text: str) -> None:
        """保存固定最终文本。"""
        self.final_text = final_text
        self.call_count = 0

    def call_model(self, state, tools) -> ModelResponse:
        """返回固定文本并记录调用次数。"""
        del state, tools
        self.call_count += 1
        return ModelResponse((AgentContentBlock(type="text", text=self.final_text),), "stop")


class _FakeFeedbackGateway:
    """记录后台任务 worker 的系统层飞书回推动作。"""

    def __init__(self) -> None:
        """初始化发送记录。"""
        self.sent_texts: list[tuple[str, str]] = []

    def send_text(self, chat_id: str, text: str) -> FeedbackResult:
        """记录指定会话文本回推。"""
        self.sent_texts.append((chat_id, text))
        return FeedbackResult(ok=True, status="sent", detail="fake", payload={"chat_id": chat_id})

    def send_owner_text(self, text: str) -> FeedbackResult:
        """记录 owner 默认会话回推。"""
        return self.send_text("owner", text)


class _FailingFeedbackGateway:
    """模拟飞书回推异常。"""

    def send_text(self, chat_id: str, text: str) -> FeedbackResult:
        """模拟指定会话发送失败。"""
        del chat_id, text
        raise RuntimeError("feishu unavailable")

    def send_owner_text(self, text: str) -> FeedbackResult:
        """模拟 owner 会话发送失败。"""
        del text
        raise RuntimeError("feishu unavailable")


def _wait_for_state(worker: BackgroundTaskWorker, predicate) -> object:
    """轮询等待后台 worker 状态满足断言。"""
    deadline = time.time() + 1.0
    latest = worker.get_state()
    while time.time() < deadline:
        latest = worker.get_state()
        if predicate(latest):
            return latest
        time.sleep(0.02)
    raise AssertionError(f"background task worker state did not satisfy predicate: {latest}")


def _self_test() -> None:
    """运行本文件单元测试。"""
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestBackgroundTaskWorker)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    _self_test()
