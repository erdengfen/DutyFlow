# 本文件负责非阻塞 /chat 调试任务服务，只处理队列、单 worker 和最近结果。

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from queue import Empty, Queue
import threading
from typing import Callable
from uuid import uuid4


def _now() -> str:
    """返回当前 UTC ISO 时间字符串。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class ChatDebugTask:
    """表示一条提交到后台 worker 的 /chat 调试任务。"""

    task_id: str
    user_text: str
    enqueued_at: str


@dataclass(frozen=True)
class ChatDebugTaskResult:
    """表示一条 /chat 调试任务的最终执行结果。"""

    task_id: str
    user_text: str
    task_status: str
    result_text: str
    error_text: str
    completed_at: str


@dataclass(frozen=True)
class ChatDebugServiceState:
    """表示非阻塞 /chat 调试服务的最小可观察状态。"""

    status: str = "initialized"
    worker_started: bool = False
    worker_alive: bool = False
    queue_size: int = 0
    accepted_count: int = 0
    processed_count: int = 0
    failed_count: int = 0
    latest_task_id: str = ""
    latest_action: str = ""
    latest_error: str = ""
    updated_at: str = field(default_factory=_now)


class ChatDebugService:
    """维护 /chat 调试任务的单队列、单 worker 和最近结果。"""

    def __init__(
        self,
        task_handler: Callable[[ChatDebugTask], str],
        *,
        queue_poll_seconds: float = 0.1,
    ) -> None:
        """绑定任务处理器并初始化 worker 控制对象。"""
        self._task_handler = task_handler
        self._queue: Queue[ChatDebugTask | None] = Queue()
        self._queue_poll_seconds = queue_poll_seconds
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._worker_thread: threading.Thread | None = None
        self._latest_result: ChatDebugTaskResult | None = None
        self._state = ChatDebugServiceState()

    def start(self) -> ChatDebugServiceState:
        """启动后台调试 worker；已运行时直接返回状态快照。"""
        with self._lock:
            if self._worker_is_alive():
                return self._snapshot_locked(status="running")
            self._stop_event.clear()
            self._worker_thread = threading.Thread(
                target=self._run_worker,
                name="dutyflow-chat-debug-worker",
                daemon=True,
            )
            self._worker_thread.start()
            self._state = replace(
                self._state,
                status="running",
                worker_started=True,
                worker_alive=True,
                latest_action="worker_started",
                updated_at=_now(),
            )
            return self._snapshot_locked()

    def stop(self, timeout_seconds: float = 2.0) -> ChatDebugServiceState:
        """停止后台调试 worker，并返回最终状态快照。"""
        thread = self._mark_stopping()
        if thread is None:
            return self.get_state()
        self._stop_event.set()
        self._queue.put(None)
        thread.join(timeout=timeout_seconds)
        with self._lock:
            self._state = replace(
                self._state,
                status="stopped",
                worker_alive=False,
                queue_size=self._queue.qsize(),
                latest_action="worker_stopped",
                updated_at=_now(),
            )
            return self._snapshot_locked()

    def enqueue(self, user_text: str) -> ChatDebugTask:
        """把一条 /chat 文本输入放入后台调试队列。"""
        clean_text = user_text.strip()
        if not clean_text:
            raise ValueError("chat debug text is required")
        task = ChatDebugTask(
            task_id=f"chat_{uuid4().hex}",
            user_text=clean_text,
            enqueued_at=_now(),
        )
        with self._lock:
            if not self._worker_is_alive():
                raise RuntimeError("chat debug worker is not running")
            self._queue.put(task)
            self._state = replace(
                self._state,
                queue_size=self._queue.qsize(),
                accepted_count=self._state.accepted_count + 1,
                latest_task_id=task.task_id,
                latest_action="enqueued",
                updated_at=_now(),
            )
            return task

    def get_state(self) -> ChatDebugServiceState:
        """返回当前 /chat 调试服务的状态快照。"""
        with self._lock:
            return self._snapshot_locked()

    def get_latest_result(self) -> ChatDebugTaskResult | None:
        """返回最近一条已完成或失败的调试任务结果。"""
        with self._lock:
            return self._latest_result

    def _mark_stopping(self) -> threading.Thread | None:
        """把服务状态切到 stopping，并返回当前 worker 线程。"""
        with self._lock:
            if not self._worker_is_alive():
                self._state = replace(
                    self._state,
                    status="stopped",
                    worker_alive=False,
                    latest_action="worker_already_stopped",
                    updated_at=_now(),
                )
                return None
            self._state = replace(
                self._state,
                status="stopping",
                worker_alive=True,
                latest_action="worker_stopping",
                updated_at=_now(),
            )
            return self._worker_thread

    def _run_worker(self) -> None:
        """持续消费 /chat 调试任务，直到收到停止信号。"""
        while not self._stop_event.is_set():
            task = self._next_task()
            if task is None:
                continue
            if task is _QUEUE_STOP:
                break
            self._process_task(task)
        self._mark_worker_exited()

    def _next_task(self) -> ChatDebugTask | object | None:
        """从队列获取下一条任务；超时则刷新运行状态。"""
        try:
            item = self._queue.get(timeout=self._queue_poll_seconds)
        except Empty:
            self._refresh_running_state()
            return None
        if item is None:
            self._queue.task_done()
            return _QUEUE_STOP
        return item

    def _process_task(self, task: ChatDebugTask) -> None:
        """执行单条后台调试任务，并记录成功或失败结果。"""
        self._mark_task_started(task)
        try:
            result_text = self._task_handler(task)
        except Exception as exc:  # noqa: BLE001
            self._mark_task_failed(task, str(exc))
            self._queue.task_done()
            return
        self._mark_task_finished(task, result_text)
        self._queue.task_done()

    def _refresh_running_state(self) -> None:
        """在空轮询时刷新 worker 仍然存活的状态。"""
        with self._lock:
            if self._state.status == "running":
                self._state = replace(
                    self._state,
                    worker_alive=self._worker_is_alive(),
                    queue_size=self._queue.qsize(),
                    updated_at=_now(),
                )

    def _mark_task_started(self, task: ChatDebugTask) -> None:
        """写入当前任务开始执行的状态。"""
        with self._lock:
            self._state = replace(
                self._state,
                status="running",
                worker_alive=True,
                queue_size=self._queue.qsize(),
                latest_task_id=task.task_id,
                latest_action="processing",
                latest_error="",
                updated_at=_now(),
            )

    def _mark_task_finished(self, task: ChatDebugTask, result_text: str) -> None:
        """写入当前任务执行成功后的状态与最近结果。"""
        with self._lock:
            self._latest_result = ChatDebugTaskResult(
                task_id=task.task_id,
                user_text=task.user_text,
                task_status="completed",
                result_text=result_text,
                error_text="",
                completed_at=_now(),
            )
            self._state = replace(
                self._state,
                status="running",
                worker_alive=True,
                queue_size=self._queue.qsize(),
                processed_count=self._state.processed_count + 1,
                latest_task_id=task.task_id,
                latest_action="processed",
                latest_error="",
                updated_at=_now(),
            )

    def _mark_task_failed(self, task: ChatDebugTask, error_text: str) -> None:
        """写入当前任务执行失败后的状态与最近结果。"""
        with self._lock:
            self._latest_result = ChatDebugTaskResult(
                task_id=task.task_id,
                user_text=task.user_text,
                task_status="failed",
                result_text="",
                error_text=error_text,
                completed_at=_now(),
            )
            self._state = replace(
                self._state,
                status="running",
                worker_alive=True,
                queue_size=self._queue.qsize(),
                failed_count=self._state.failed_count + 1,
                latest_task_id=task.task_id,
                latest_action="failed",
                latest_error=error_text,
                updated_at=_now(),
            )

    def _mark_worker_exited(self) -> None:
        """在 worker 退出时更新最终状态。"""
        with self._lock:
            self._state = replace(
                self._state,
                status="stopped",
                worker_alive=False,
                queue_size=self._queue.qsize(),
                latest_action="worker_exited",
                updated_at=_now(),
            )

    def _snapshot_locked(self, *, status: str = "") -> ChatDebugServiceState:
        """在持锁条件下返回状态快照。"""
        current_status = status or self._state.status
        return replace(
            self._state,
            status=current_status,
            worker_alive=self._worker_is_alive(),
            queue_size=self._queue.qsize(),
            updated_at=_now(),
        )

    def _worker_is_alive(self) -> bool:
        """判断当前后台调试 worker 是否仍存活。"""
        return self._worker_thread is not None and self._worker_thread.is_alive()


_QUEUE_STOP = object()


def _self_test() -> None:
    """验证后台调试任务可启动、入队并被消费。"""
    event = threading.Event()

    def _handler(task: ChatDebugTask) -> str:
        if task.user_text == "ping":
            event.set()
        return f"done: {task.user_text}"

    service = ChatDebugService(_handler, queue_poll_seconds=0.01)
    service.start()
    service.enqueue("ping")
    if not event.wait(timeout=1.0):
        raise AssertionError("chat debug worker did not process task")


if __name__ == "__main__":
    _self_test()
    print("dutyflow debug chat service self-test passed")
