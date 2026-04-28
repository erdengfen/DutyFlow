# 本文件负责正式 Agent Runtime 的最小服务骨架，只处理队列、单 worker 和运行状态快照。

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from queue import Empty, Queue
import threading
from typing import Any, Callable, Mapping
from uuid import uuid4


def _now() -> str:
    """返回当前 UTC ISO 时间字符串。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class RuntimeLoopInput:
    """表示正式 runtime worker 接收的一条标准输入。"""

    perception_id: str
    perception_file: str
    trigger_kind: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RuntimeWorkItem:
    """表示进入正式 runtime queue 的单条待处理任务。"""

    work_id: str
    perception_id: str
    enqueued_at: str
    loop_input: RuntimeLoopInput


@dataclass(frozen=True)
class RuntimeServiceState:
    """表示正式 runtime service 的最小可观察状态。"""

    status: str = "initialized"
    worker_started: bool = False
    worker_alive: bool = False
    queue_size: int = 0
    accepted_count: int = 0
    processed_count: int = 0
    failed_count: int = 0
    latest_work_id: str = ""
    latest_perception_id: str = ""
    latest_action: str = ""
    latest_error: str = ""
    updated_at: str = field(default_factory=_now)


class RuntimeService:
    """维护正式 Agent Runtime 的单队列和单 worker 执行骨架。"""

    def __init__(
        self,
        work_handler: Callable[[RuntimeWorkItem], None] | None = None,
        *,
        queue_poll_seconds: float = 0.1,
    ) -> None:
        """初始化队列、线程控制对象和最小运行状态。"""
        self._work_handler = work_handler or _noop_work_handler
        self._queue: Queue[RuntimeWorkItem | None] = Queue()
        self._queue_poll_seconds = queue_poll_seconds
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._worker_thread: threading.Thread | None = None
        self._state = RuntimeServiceState()

    def start(self) -> RuntimeServiceState:
        """启动正式 runtime worker；已运行时直接返回当前状态。"""
        with self._lock:
            if self._worker_is_alive():
                return self._snapshot_locked(status="running")
            self._stop_event.clear()
            self._worker_thread = threading.Thread(
                target=self._run_worker,
                name="dutyflow-runtime-worker",
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

    def stop(self, timeout_seconds: float = 2.0) -> RuntimeServiceState:
        """停止正式 runtime worker，并返回停止后的状态快照。"""
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

    def enqueue_perception(self, loop_input: Mapping[str, Any]) -> RuntimeWorkItem:
        """把一条感知记录输入放入正式 runtime queue。"""
        input_payload = _normalize_loop_input(loop_input)
        work_item = RuntimeWorkItem(
            work_id=f"run_{uuid4().hex}",
            perception_id=input_payload.perception_id,
            enqueued_at=_now(),
            loop_input=input_payload,
        )
        with self._lock:
            if not self._worker_is_alive():
                raise RuntimeError("runtime worker is not running")
            self._queue.put(work_item)
            self._state = replace(
                self._state,
                queue_size=self._queue.qsize(),
                accepted_count=self._state.accepted_count + 1,
                latest_work_id=work_item.work_id,
                latest_perception_id=work_item.perception_id,
                latest_action="enqueued",
                updated_at=_now(),
            )
            return work_item

    def get_state(self) -> RuntimeServiceState:
        """返回当前 runtime service 的状态快照。"""
        with self._lock:
            return self._snapshot_locked()

    def _mark_stopping(self) -> threading.Thread | None:
        """把当前 runtime 标记为 stopping，并返回活动线程。"""
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
        """持续消费队列中的任务，并维护最小运行状态。"""
        while not self._stop_event.is_set():
            work_item = self._next_work_item()
            if work_item is None:
                continue
            if work_item is _QUEUE_STOP:
                break
            self._process_work_item(work_item)
        self._mark_worker_exited()

    def _next_work_item(self) -> RuntimeWorkItem | object | None:
        """从队列取下一条任务；超时则返回空。"""
        try:
            item = self._queue.get(timeout=self._queue_poll_seconds)
        except Empty:
            self._refresh_running_state()
            return None
        if item is None:
            self._queue.task_done()
            return _QUEUE_STOP
        return item

    def _process_work_item(self, work_item: RuntimeWorkItem) -> None:
        """执行单条任务，并把结果写回 service 状态。"""
        self._mark_work_started(work_item)
        try:
            self._work_handler(work_item)
        except Exception as exc:  # noqa: BLE001
            self._mark_work_failed(work_item, str(exc))
            self._queue.task_done()
            return
        self._mark_work_finished(work_item)
        self._queue.task_done()

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

    def _mark_work_started(self, work_item: RuntimeWorkItem) -> None:
        """写入当前任务开始执行的状态。"""
        with self._lock:
            self._state = replace(
                self._state,
                status="running",
                worker_alive=True,
                queue_size=self._queue.qsize(),
                latest_work_id=work_item.work_id,
                latest_perception_id=work_item.perception_id,
                latest_action="processing",
                latest_error="",
                updated_at=_now(),
            )

    def _mark_work_finished(self, work_item: RuntimeWorkItem) -> None:
        """写入当前任务执行成功后的状态。"""
        with self._lock:
            self._state = replace(
                self._state,
                status="running",
                worker_alive=True,
                queue_size=self._queue.qsize(),
                processed_count=self._state.processed_count + 1,
                latest_work_id=work_item.work_id,
                latest_perception_id=work_item.perception_id,
                latest_action="processed",
                latest_error="",
                updated_at=_now(),
            )

    def _mark_work_failed(self, work_item: RuntimeWorkItem, error_message: str) -> None:
        """写入当前任务执行失败后的状态。"""
        with self._lock:
            self._state = replace(
                self._state,
                status="running",
                worker_alive=True,
                queue_size=self._queue.qsize(),
                failed_count=self._state.failed_count + 1,
                latest_work_id=work_item.work_id,
                latest_perception_id=work_item.perception_id,
                latest_action="failed",
                latest_error=error_message,
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

    def _snapshot_locked(self, *, status: str = "") -> RuntimeServiceState:
        """在持锁条件下返回最新状态快照。"""
        current_status = status or self._state.status
        return replace(
            self._state,
            status=current_status,
            worker_alive=self._worker_is_alive(),
            queue_size=self._queue.qsize(),
            updated_at=_now(),
        )

    def _worker_is_alive(self) -> bool:
        """判断当前 worker 线程是否仍存活。"""
        return self._worker_thread is not None and self._worker_thread.is_alive()


def _normalize_loop_input(loop_input: Mapping[str, Any]) -> RuntimeLoopInput:
    """把感知层标准输入转换为 runtime service 使用的对象。"""
    perception_id = str(loop_input.get("perception_id", "")).strip()
    if not perception_id:
        raise ValueError("perception_id is required")
    payload = dict(loop_input)
    return RuntimeLoopInput(
        perception_id=perception_id,
        perception_file=str(loop_input.get("perception_file", "")).strip(),
        trigger_kind=str(loop_input.get("trigger_kind", "")).strip(),
        payload=payload,
    )


def _noop_work_handler(work_item: RuntimeWorkItem) -> None:
    """默认 work handler 什么也不做，只验证队列可消费。"""
    del work_item


_QUEUE_STOP = object()


def _self_test() -> None:
    """验证 runtime service 可启动、入队并停止。"""
    event = threading.Event()

    def _handler(work_item: RuntimeWorkItem) -> None:
        if work_item.perception_id == "per_demo":
            event.set()

    service = RuntimeService(_handler, queue_poll_seconds=0.01)
    service.start()
    service.enqueue_perception({"perception_id": "per_demo", "trigger_kind": "p2p_text"})
    if not event.wait(timeout=1.0):
        raise AssertionError("runtime worker did not process work item")
    state = service.stop()
    assert state.worker_started


if __name__ == "__main__":
    _self_test()
    print("dutyflow runtime service self-test passed")
