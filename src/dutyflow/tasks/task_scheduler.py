# 本文件负责 Step 7 第一版后台任务调度器，只处理到时任务扫描和入队信号发出。

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
import threading
import time
from typing import Callable

from dutyflow.tasks.task_state import TaskRecord, TaskStore


@dataclass(frozen=True)
class TaskDispatchItem:
    """表示一条已被调度器判定可入后台队列的任务。"""

    task_id: str
    task_file: str
    scheduled_for: str
    run_mode: str
    execution_profile: str


@dataclass(frozen=True)
class TaskSchedulerState:
    """表示后台任务调度器的最小可观察状态。"""

    status: str = "initialized"
    thread_started: bool = False
    thread_alive: bool = False
    scanned_count: int = 0
    dispatched_count: int = 0
    latest_task_id: str = ""
    latest_action: str = ""
    latest_error: str = ""
    updated_at: str = field(default_factory=lambda: _now_iso())


class TaskSchedulerService:
    """周期扫描 `scheduled` 任务，并在到时后发出入队信号。"""

    def __init__(
        self,
        task_store: TaskStore,
        dispatch_handler: Callable[[TaskDispatchItem], None] | None = None,
        *,
        scan_interval_seconds: float = 5.0,
        time_provider: Callable[[], datetime] | None = None,
    ) -> None:
        """绑定任务存储、调度回调和最小线程控制。"""
        self.task_store = task_store
        self.dispatch_handler = dispatch_handler or _noop_dispatch_handler
        self.scan_interval_seconds = scan_interval_seconds
        self.time_provider = time_provider or _local_now
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._state = TaskSchedulerState()

    def start(self) -> TaskSchedulerState:
        """启动调度线程；已运行时只返回当前状态。"""
        with self._lock:
            if self._thread_is_alive():
                return self._snapshot_locked(status="running")
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run_loop,
                name="dutyflow-task-scheduler",
                daemon=True,
            )
            self._thread.start()
            self._state = replace(
                self._state,
                status="running",
                thread_started=True,
                thread_alive=True,
                latest_action="scheduler_started",
                updated_at=_now_iso(),
            )
            return self._snapshot_locked()

    def stop(self, timeout_seconds: float = 2.0) -> TaskSchedulerState:
        """停止调度线程，并返回停止后的状态。"""
        thread = self._mark_stopping()
        if thread is None:
            return self.get_state()
        self._stop_event.set()
        thread.join(timeout=timeout_seconds)
        with self._lock:
            self._state = replace(
                self._state,
                status="stopped",
                thread_alive=False,
                latest_action="scheduler_stopped",
                updated_at=_now_iso(),
            )
            return self._snapshot_locked()

    def get_state(self) -> TaskSchedulerState:
        """返回调度器的状态快照。"""
        with self._lock:
            return self._snapshot_locked()

    def scan_due_tasks(self) -> tuple[TaskRecord, ...]:
        """扫描当前已到时的 `scheduled` 任务，但不修改状态。"""
        now = self.time_provider()
        due_tasks: list[TaskRecord] = []
        for task in self.task_store.list_tasks():
            if _is_due_scheduled_task(task, now):
                due_tasks.append(task)
        with self._lock:
            self._state = replace(
                self._state,
                scanned_count=self._state.scanned_count + len(due_tasks),
                latest_action="scan_due_tasks",
                latest_error="",
                updated_at=_now_iso(),
            )
        return tuple(due_tasks)

    def run_once(self) -> tuple[TaskDispatchItem, ...]:
        """执行一次扫描与调度，把已到时任务切换为 `queued` 并发出回调。"""
        dispatches: list[TaskDispatchItem] = []
        for task in self.scan_due_tasks():
            dispatch = _build_dispatch_item(task)
            self.dispatch_handler(dispatch)
            self.task_store.update_task(
                task.task_id,
                frontmatter_updates={"status": "queued"},
                state_updates={"last_result_summary": "任务已到时，等待后台 worker 执行。"},
                section_updates={"next_action": "等待后台 worker 拉起执行。"},
            )
            dispatches.append(dispatch)
            self._mark_dispatched(dispatch)
        return tuple(dispatches)

    def _run_loop(self) -> None:
        """周期执行一次扫描与调度，并在异常时保留状态。"""
        while not self._stop_event.is_set():
            try:
                self.run_once()
            except Exception as exc:  # noqa: BLE001
                self._mark_failed(str(exc))
            self._sleep_until_next_scan()
        self._mark_exited()

    def _sleep_until_next_scan(self) -> None:
        """使用可中断 sleep，避免 stop 时长时间等待。"""
        deadline = time.time() + self.scan_interval_seconds
        while time.time() < deadline:
            if self._stop_event.wait(timeout=0.05):
                return

    def _mark_stopping(self) -> threading.Thread | None:
        """把当前调度器标记为 stopping，并返回活动线程。"""
        with self._lock:
            if not self._thread_is_alive():
                self._state = replace(
                    self._state,
                    status="stopped",
                    thread_alive=False,
                    latest_action="scheduler_already_stopped",
                    updated_at=_now_iso(),
                )
                return None
            self._state = replace(
                self._state,
                status="stopping",
                thread_alive=True,
                latest_action="scheduler_stopping",
                updated_at=_now_iso(),
            )
            return self._thread

    def _mark_dispatched(self, dispatch: TaskDispatchItem) -> None:
        """写入单条任务已被调度成功的状态。"""
        with self._lock:
            self._state = replace(
                self._state,
                status="running",
                thread_alive=True,
                dispatched_count=self._state.dispatched_count + 1,
                latest_task_id=dispatch.task_id,
                latest_action="dispatched",
                latest_error="",
                updated_at=_now_iso(),
            )

    def _mark_failed(self, error_message: str) -> None:
        """记录一次调度失败，但不杀死线程。"""
        with self._lock:
            self._state = replace(
                self._state,
                status="running",
                thread_alive=True,
                latest_action="failed",
                latest_error=error_message,
                updated_at=_now_iso(),
            )

    def _mark_exited(self) -> None:
        """在调度线程退出时更新最终状态。"""
        with self._lock:
            self._state = replace(
                self._state,
                status="stopped",
                thread_alive=False,
                latest_action="scheduler_exited",
                updated_at=_now_iso(),
            )

    def _snapshot_locked(self, *, status: str = "") -> TaskSchedulerState:
        """在持锁条件下返回最新状态快照。"""
        return replace(
            self._state,
            status=status or self._state.status,
            thread_alive=self._thread_is_alive(),
            updated_at=_now_iso(),
        )

    def _thread_is_alive(self) -> bool:
        """判断当前调度线程是否存活。"""
        return self._thread is not None and self._thread.is_alive()


def _build_dispatch_item(task: TaskRecord) -> TaskDispatchItem:
    """把任务对象转换为调度器对外发出的最小 dispatch 载荷。"""
    return TaskDispatchItem(
        task_id=task.task_id,
        task_file=str(task.path),
        scheduled_for=task.scheduled_for,
        run_mode=task.run_mode,
        execution_profile=task.execution_profile,
    )


def _is_due_scheduled_task(task: TaskRecord, now: datetime) -> bool:
    """判断任务是否属于“已到时且仍待调度”的一次性定时任务。"""
    if task.status != "scheduled":
        return False
    if task.run_mode != "run_at":
        return False
    if not task.scheduled_for:
        return False
    scheduled_for = _parse_iso_datetime(task.scheduled_for)
    if scheduled_for is None:
        return False
    return scheduled_for <= now


def _parse_iso_datetime(value: str) -> datetime | None:
    """把 ISO-8601 字符串解析为带时区时间；非法值返回空。"""
    text = value.strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _local_now() -> datetime:
    """返回当前本地时区时间。"""
    return datetime.now().astimezone()


def _now_iso() -> str:
    """返回当前本地时区 ISO 时间字符串。"""
    return _local_now().isoformat(timespec="seconds")


def _noop_dispatch_handler(dispatch: TaskDispatchItem) -> None:
    """默认 dispatch handler 什么也不做，只验证调度链可运行。"""
    del dispatch


def _self_test() -> None:
    """验证调度器可发现一条已到时的定时任务。"""
    import tempfile

    captured: list[str] = []
    with tempfile.TemporaryDirectory() as temp_dir:
        store = TaskStore(Path(temp_dir))
        store.create_task(
            title="self test scheduled task",
            task_id="task_sched_selftest",
            status="scheduled",
            run_mode="run_at",
            scheduled_for="2026-04-29T09:00:00+08:00",
        )
        service = TaskSchedulerService(
            store,
            lambda item: captured.append(item.task_id),
            time_provider=lambda: datetime.fromisoformat("2026-04-29T10:00:00+08:00"),
        )
        dispatches = service.run_once()
    assert len(dispatches) == 1
    assert captured == ["task_sched_selftest"]


if __name__ == "__main__":
    _self_test()
    print("dutyflow task scheduler self-test passed")
