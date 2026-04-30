# 本文件负责 Step 7 后台任务 worker 的独立队列、状态流转和 subagent 执行面。

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from queue import Empty, Queue
import sys
import threading
import time
from typing import Callable
from uuid import uuid4

if __package__ in {None, ""}:
    _SRC_ROOT = Path(__file__).resolve().parents[2]
    if str(_SRC_ROOT) not in sys.path:
        sys.path.insert(0, str(_SRC_ROOT))

from dutyflow.agent.background_subagent_executor import (
    BackgroundSubagentExecutor,
    BackgroundSubagentResult,
)
from dutyflow.agent.control_state_store import AgentControlStateStore
from dutyflow.agent.model_client import ModelClient
from dutyflow.tasks.task_state import TaskRecord, TaskStore


def _now() -> str:
    """返回当前 UTC ISO 时间字符串，供 worker 状态快照使用。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class BackgroundTaskWorkItem:
    """表示进入后台任务 worker 队列的一条任务信号。"""

    work_id: str
    task_id: str
    task_file: str
    source: str
    enqueued_at: str


@dataclass(frozen=True)
class BackgroundTaskExecutionResult:
    """表示后台任务执行器对任务文件给出的最终状态更新。"""

    status: str
    retry_status: str
    last_result_summary: str
    next_action: str


@dataclass(frozen=True)
class BackgroundTaskWorkerState:
    """表示后台任务 worker 的最小可观察状态。"""

    status: str = "initialized"
    worker_started: bool = False
    worker_alive: bool = False
    queue_size: int = 0
    accepted_count: int = 0
    processed_count: int = 0
    failed_count: int = 0
    latest_work_id: str = ""
    latest_task_id: str = ""
    latest_action: str = ""
    latest_error: str = ""
    updated_at: str = field(default_factory=_now)


class BackgroundTaskWorker:
    """维护正式 runtime 之外的后台任务独立执行面。"""

    def __init__(
        self,
        task_store: TaskStore,
        task_handler: Callable[[TaskRecord], BackgroundTaskExecutionResult] | None = None,
        *,
        model_client: ModelClient | None = None,
        task_executor: BackgroundSubagentExecutor | None = None,
        queue_poll_seconds: float = 0.1,
        ready_scan_interval_seconds: float = 1.0,
        control_state_store: AgentControlStateStore | None = None,
    ) -> None:
        """绑定任务存储、执行器和线程控制对象。"""
        self.task_store = task_store
        self._task_handler = task_handler or _build_subagent_task_handler(
            task_store,
            model_client,
            task_executor,
        )
        self.control_state_store = control_state_store or AgentControlStateStore(
            task_store.project_root,
            task_store=task_store,
        )
        self._queue: Queue[BackgroundTaskWorkItem | None] = Queue()
        # 关键开关：队列空轮询间隔为 0.1 秒，保证 stop 和测试等待不会长时间阻塞。
        self._queue_poll_seconds = queue_poll_seconds
        # 关键开关：每 1 秒扫描一次 queued 任务，避免频繁扫盘影响本地运行。
        self._ready_scan_interval_seconds = ready_scan_interval_seconds
        self._queued_by_task_id: dict[str, BackgroundTaskWorkItem] = {}
        self._last_ready_scan_at = 0.0
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._worker_thread: threading.Thread | None = None
        self._state = BackgroundTaskWorkerState()

    def start(self) -> BackgroundTaskWorkerState:
        """启动后台任务 worker；已运行时直接返回当前状态。"""
        with self._lock:
            if self._worker_is_alive():
                return self._snapshot_locked(status="running")
            self._stop_event.clear()
            self._worker_thread = threading.Thread(
                target=self._run_worker,
                name="dutyflow-background-task-worker",
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

    def stop(self, timeout_seconds: float = 2.0) -> BackgroundTaskWorkerState:
        """停止后台任务 worker，并返回停止后的状态快照。"""
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

    def enqueue_task(self, task_id: str, *, source: str = "manual") -> BackgroundTaskWorkItem:
        """把指定任务 ID 放入后台 worker 队列，并做同任务去重。"""
        clean_task_id = task_id.strip()
        if not clean_task_id:
            raise ValueError("task_id is required")
        task = self.task_store.read_task(clean_task_id)
        if task is None:
            raise FileNotFoundError(f"task not found: {clean_task_id}")
        return self._enqueue_existing_task(task, source=source)

    def enqueue_ready_tasks(self) -> tuple[BackgroundTaskWorkItem, ...]:
        """扫描当前 `queued` 任务并入后台 worker 队列。"""
        queued_items: list[BackgroundTaskWorkItem] = []
        for task in self.task_store.list_tasks():
            if task.status == "queued":
                queued_items.append(self._enqueue_existing_task(task, source="ready_scan"))
        return tuple(queued_items)

    def get_state(self) -> BackgroundTaskWorkerState:
        """返回当前后台任务 worker 状态快照。"""
        with self._lock:
            return self._snapshot_locked()

    def _enqueue_existing_task(self, task: TaskRecord, *, source: str) -> BackgroundTaskWorkItem:
        """把已确认存在的任务放入队列；同一任务未消费前只保留一条。"""
        with self._lock:
            if not self._worker_is_alive():
                raise RuntimeError("background task worker is not running")
            existing = self._queued_by_task_id.get(task.task_id)
            if existing is not None:
                return existing
            work_item = _build_work_item(task, source)
            self._queued_by_task_id[task.task_id] = work_item
            self._queue.put(work_item)
            self._mark_enqueued_locked(work_item)
            return work_item

    def _run_worker(self) -> None:
        """持续扫描并消费后台任务，直到收到停止信号。"""
        while not self._stop_event.is_set():
            self._scan_ready_tasks_if_due()
            work_item = self._next_work_item()
            if work_item is None:
                continue
            if work_item is _QUEUE_STOP:
                break
            self._process_work_item(work_item)
        self._mark_worker_exited()

    def _scan_ready_tasks_if_due(self) -> None:
        """按固定间隔扫描 queued 任务，避免后台任务入口必须持有 worker 引用。"""
        now = time.monotonic()
        if now - self._last_ready_scan_at < self._ready_scan_interval_seconds:
            return
        self._last_ready_scan_at = now
        try:
            self.enqueue_ready_tasks()
        except Exception as exc:  # noqa: BLE001
            self._mark_worker_error(str(exc))

    def _next_work_item(self) -> BackgroundTaskWorkItem | object | None:
        """从队列取下一条任务；超时则刷新运行状态。"""
        try:
            item = self._queue.get(timeout=self._queue_poll_seconds)
        except Empty:
            self._refresh_running_state()
            return None
        if item is None:
            self._queue.task_done()
            return _QUEUE_STOP
        return item

    def _process_work_item(self, work_item: BackgroundTaskWorkItem) -> None:
        """执行单条后台任务，并把任务状态写回 Markdown。"""
        self._mark_work_started(work_item)
        try:
            task = self._mark_task_running(work_item)
            result = self._task_handler(task)
            self._apply_execution_result(task.task_id, result)
        except Exception as exc:  # noqa: BLE001
            self._mark_task_failed(work_item, str(exc))
            self._queue.task_done()
            return
        self._mark_work_finished(work_item)
        self._queue.task_done()

    def _mark_task_running(self, work_item: BackgroundTaskWorkItem) -> TaskRecord:
        """把任务文件切到 running，并返回最新任务记录。"""
        task = self.task_store.read_task(work_item.task_id)
        if task is None:
            raise FileNotFoundError(f"task not found: {work_item.task_id}")
        if task.status != "queued":
            raise RuntimeError(f"task is not queued: {work_item.task_id}")
        updated = self.task_store.update_task(
            task.task_id,
            frontmatter_updates={"status": "running"},
            state_updates={
                "attempt_count": str(_next_attempt_count(task.attempt_count)),
                "retry_status": "running",
                "last_result_summary": "后台任务 worker 已开始处理该任务。",
            },
            section_updates={"next_action": "后台任务正在独立执行面中处理。"},
        )
        self.control_state_store.sync()
        return updated

    def _apply_execution_result(self, task_id: str, result: BackgroundTaskExecutionResult) -> None:
        """把执行器返回的状态写回任务 Markdown。"""
        self.task_store.update_task(
            task_id,
            frontmatter_updates={"status": result.status},
            state_updates={
                "retry_status": result.retry_status,
                "last_result_summary": result.last_result_summary,
            },
            section_updates={"next_action": result.next_action},
        )
        self.control_state_store.sync()

    def _mark_task_failed(self, work_item: BackgroundTaskWorkItem, error_message: str) -> None:
        """在执行异常时把任务标记为 failed，同时保留 worker 可继续运行。"""
        self.task_store.update_task(
            work_item.task_id,
            frontmatter_updates={"status": "failed"},
            state_updates={"retry_status": "failed", "last_result_summary": error_message},
            section_updates={"next_action": "后台任务执行失败，等待后续人工检查或恢复策略。"},
        )
        self.control_state_store.sync()
        self._mark_work_failed(work_item, error_message)

    def _mark_stopping(self) -> threading.Thread | None:
        """把 worker 标记为 stopping，并返回当前活动线程。"""
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

    def _mark_enqueued_locked(self, work_item: BackgroundTaskWorkItem) -> None:
        """在持锁状态下写入任务入队状态。"""
        self._state = replace(
            self._state,
            queue_size=self._queue.qsize(),
            accepted_count=self._state.accepted_count + 1,
            latest_work_id=work_item.work_id,
            latest_task_id=work_item.task_id,
            latest_action="enqueued",
            updated_at=_now(),
        )

    def _mark_work_started(self, work_item: BackgroundTaskWorkItem) -> None:
        """写入当前后台任务开始处理的 worker 状态。"""
        with self._lock:
            self._state = replace(
                self._state,
                status="running",
                worker_alive=True,
                queue_size=self._queue.qsize(),
                latest_work_id=work_item.work_id,
                latest_task_id=work_item.task_id,
                latest_action="processing",
                latest_error="",
                updated_at=_now(),
            )

    def _mark_work_finished(self, work_item: BackgroundTaskWorkItem) -> None:
        """写入当前后台任务处理完成的 worker 状态。"""
        with self._lock:
            self._queued_by_task_id.pop(work_item.task_id, None)
            self._state = replace(
                self._state,
                status="running",
                worker_alive=True,
                queue_size=self._queue.qsize(),
                processed_count=self._state.processed_count + 1,
                latest_work_id=work_item.work_id,
                latest_task_id=work_item.task_id,
                latest_action="processed",
                latest_error="",
                updated_at=_now(),
            )

    def _mark_work_failed(self, work_item: BackgroundTaskWorkItem, error_message: str) -> None:
        """写入当前后台任务失败的 worker 状态。"""
        with self._lock:
            self._queued_by_task_id.pop(work_item.task_id, None)
            self._state = replace(
                self._state,
                status="running",
                worker_alive=True,
                queue_size=self._queue.qsize(),
                failed_count=self._state.failed_count + 1,
                latest_work_id=work_item.work_id,
                latest_task_id=work_item.task_id,
                latest_action="failed",
                latest_error=error_message,
                updated_at=_now(),
            )

    def _mark_worker_error(self, error_message: str) -> None:
        """记录 worker 扫描阶段异常，但不停止线程。"""
        with self._lock:
            self._state = replace(
                self._state,
                status="running",
                worker_alive=True,
                latest_action="scan_failed",
                latest_error=error_message,
                updated_at=_now(),
            )

    def _refresh_running_state(self) -> None:
        """在空轮询时刷新 worker 仍在运行的状态。"""
        with self._lock:
            if self._state.status == "running":
                self._state = replace(
                    self._state,
                    worker_alive=self._worker_is_alive(),
                    queue_size=self._queue.qsize(),
                    updated_at=_now(),
                )

    def _mark_worker_exited(self) -> None:
        """在 worker 线程退出时更新最终状态。"""
        with self._lock:
            self._state = replace(
                self._state,
                status="stopped",
                worker_alive=False,
                queue_size=self._queue.qsize(),
                latest_action="worker_exited",
                updated_at=_now(),
            )

    def _snapshot_locked(self, *, status: str = "") -> BackgroundTaskWorkerState:
        """在持锁状态下返回当前状态快照。"""
        return replace(
            self._state,
            status=status or self._state.status,
            worker_alive=self._worker_is_alive(),
            queue_size=self._queue.qsize(),
            updated_at=_now(),
        )

    def _worker_is_alive(self) -> bool:
        """判断当前后台任务 worker 线程是否仍存活。"""
        return self._worker_thread is not None and self._worker_thread.is_alive()


def _build_work_item(task: TaskRecord, source: str) -> BackgroundTaskWorkItem:
    """把任务记录转换为后台 worker 队列项。"""
    return BackgroundTaskWorkItem(
        work_id=f"bg_{uuid4().hex}",
        task_id=task.task_id,
        task_file=str(task.path),
        source=source.strip() or "unknown",
        enqueued_at=_now(),
    )


def _next_attempt_count(raw_value: str) -> int:
    """根据任务记录中已有 attempt_count 生成下一次尝试次数。"""
    try:
        current = int(raw_value.strip() or "0")
    except ValueError:
        return 1
    return current + 1


def _build_subagent_task_handler(
    task_store: TaskStore,
    model_client: ModelClient | None,
    task_executor: BackgroundSubagentExecutor | None,
) -> Callable[[TaskRecord], BackgroundTaskExecutionResult]:
    """构造默认后台任务执行器，正式路径不再使用占位 handler。"""
    executor = task_executor or _create_subagent_executor(task_store, model_client)
    return _SubagentTaskHandler(executor)


def _create_subagent_executor(
    task_store: TaskStore,
    model_client: ModelClient | None,
) -> BackgroundSubagentExecutor:
    """根据 worker 依赖创建后台 subagent executor。"""
    if model_client is None:
        raise ValueError("model_client is required when task_handler is not provided")
    return BackgroundSubagentExecutor(task_store.project_root, model_client)


class _SubagentTaskHandler:
    """把 BackgroundSubagentExecutor 适配为 worker 可消费的任务 handler。"""

    def __init__(self, executor: BackgroundSubagentExecutor) -> None:
        """保存后台 subagent executor。"""
        self.executor = executor

    def __call__(self, task: TaskRecord) -> BackgroundTaskExecutionResult:
        """执行任务并转换为 worker 状态写回结构。"""
        return _to_worker_execution_result(self.executor.execute_task(task))


def _to_worker_execution_result(result: BackgroundSubagentResult) -> BackgroundTaskExecutionResult:
    """把 subagent 执行结果转换成 worker 任务状态更新。"""
    return BackgroundTaskExecutionResult(
        status=result.status,
        retry_status=result.retry_status,
        last_result_summary=result.last_result_summary,
        next_action=result.next_action,
    )


_QUEUE_STOP = object()


def _self_test() -> None:
    """验证后台任务 worker 可独立消费 queued 任务并更新任务文件。"""
    import tempfile

    event = threading.Event()

    def _handler(task: TaskRecord) -> BackgroundTaskExecutionResult:
        if task.task_id == "task_bg_selftest":
            event.set()
        return BackgroundTaskExecutionResult(
            status="completed",
            retry_status="done",
            last_result_summary="self-test completed",
            next_action="无。",
        )

    with tempfile.TemporaryDirectory() as temp_dir:
        store = TaskStore(Path(temp_dir))
        store.create_task(title="self test", task_id="task_bg_selftest", status="queued")
        worker = BackgroundTaskWorker(store, _handler, queue_poll_seconds=0.01, ready_scan_interval_seconds=0.01)
        worker.start()
        if not event.wait(timeout=1.0):
            raise AssertionError("background task worker did not process queued task")
        _wait_until_processed(worker)
        loaded = store.read_task("task_bg_selftest")
        worker.stop()
    assert loaded is not None
    assert loaded.status == "completed"


def _wait_until_processed(worker: BackgroundTaskWorker) -> None:
    """等待自测 worker 完成状态写回，避免只等 handler 事件造成竞态。"""
    deadline = time.time() + 1.0
    while time.time() < deadline:
        if worker.get_state().processed_count >= 1:
            return
        time.sleep(0.01)
    raise AssertionError("background task worker did not mark work as processed")


if __name__ == "__main__":
    _self_test()
    print("dutyflow background task worker self-test passed")
