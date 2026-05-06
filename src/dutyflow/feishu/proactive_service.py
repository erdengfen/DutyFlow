# 本文件负责飞书主动感知调度层：周期发现、审批请求、采集和 ambient 分析入队。

from __future__ import annotations

import sys
import threading
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

if __package__ in {None, ""}:
    _SRC_ROOT = Path(__file__).resolve().parents[2]
    if str(_SRC_ROOT) not in sys.path:
        sys.path.insert(0, str(_SRC_ROOT))

from dutyflow.feishu.ambient_analysis_intake import AmbientAnalysisIntakeService
from dutyflow.feishu.collectors.direct_message_collector import DirectMessageCollector
from dutyflow.feishu.collectors.group_candidate_discovery import GroupCandidateDiscovery
from dutyflow.feishu.collectors.group_message_collector import GroupMessageCollector
from dutyflow.feishu.collectors.user_document_collector import UserDocumentCollector
from dutyflow.feishu.scope_registry import (
    GROUP_CHAT_SCOPE,
    GROUP_MESSAGE_COLLECTOR,
    P2P_CHAT_SCOPE,
    FeishuScopeRegistry,
    scope_account_id_from_config,
)
from dutyflow.feishu.summary_task_intake import SummaryTaskIntakeService

# 关键开关：主循环轮询间隔秒数，决定服务最大响应延迟。
TICK_INTERVAL_SECONDS = 60
# 关键开关：群组和文档根目录发现的间隔秒数，避免频繁调用飞书 chat list API。
DISCOVERY_INTERVAL_SECONDS = 3600
# 关键开关：enabled scope 消息采集的间隔秒数，用于控制请求频率。
COLLECT_INTERVAL_SECONDS = 300
# 关键开关：同一 candidate scope 重复发送审批卡片的最短冷却小时数，避免卡片噪音。
APPROVAL_REQUEST_COOLDOWN_HOURS = 24
# 关键开关：单次 tick 最多发起审批请求数，控制每次调度的飞书 API 调用量。
MAX_APPROVAL_REQUESTS_PER_TICK = 3
# 关键开关：单次 tick 最多采集的 scope 数，防止单次调度耗时过长。
MAX_SCOPES_PER_TICK = 10
# 关键开关：系统预制总结任务的创建检查间隔秒数，按小时节奏创建，避免总结任务过频。
SUMMARY_TASK_INTERVAL_SECONDS = 3600


def _now() -> str:
    """返回当前 UTC ISO 时间字符串。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _now_dt() -> datetime:
    """返回当前 UTC datetime。"""
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class FeishuProactiveState:
    """表示主动感知服务的最小可观察状态。"""

    status: str = "initialized"
    worker_alive: bool = False
    tick_count: int = 0
    last_tick_at: str = ""
    last_discovery_at: str = ""
    last_collect_at: str = ""
    last_approval_requests_at: str = ""
    last_intake_at: str = ""
    last_scopes_discovered: int = 0
    last_records_collected: int = 0
    last_approval_requests_sent: int = 0
    last_packets_enqueued: int = 0
    last_summary_tasks_at: str = ""
    last_summary_tasks_created: int = 0
    last_error: str = ""
    updated_at: str = field(default_factory=_now)


class FeishuProactiveService:
    """随 app bootstrap 常驻运行的飞书主动感知调度层。"""

    def __init__(
        self,
        project_root: Path,
        config: Any,
        *,
        user_client_factory: Callable[[], Any] | None = None,
        runtime_service: Any = None,
        approval_service: Any = None,
        registry: FeishuScopeRegistry | None = None,
        summary_task_intake: SummaryTaskIntakeService | None = None,
    ) -> None:
        """绑定工作区、配置和可注入服务依赖。"""
        self.project_root = Path(project_root).resolve()
        self.config = config
        self._user_client_factory = user_client_factory
        self._runtime_service = runtime_service
        self._approval_service = approval_service
        self._registry = registry or FeishuScopeRegistry(self.project_root)
        self._summary_task_intake = summary_task_intake or SummaryTaskIntakeService(self.project_root)
        self._state = FeishuProactiveState()
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._worker_thread: threading.Thread | None = None

    def start(self) -> FeishuProactiveState:
        """启动后台调度线程；已运行时直接返回当前状态。"""
        with self._lock:
            if self._worker_is_alive():
                return self._snapshot_locked(status="running")
            self._stop_event.clear()
            self._worker_thread = threading.Thread(
                target=self._run_loop,
                name="dutyflow-proactive-worker",
                daemon=True,
            )
            self._worker_thread.start()
            self._state = replace(
                self._state,
                status="running",
                worker_alive=True,
                updated_at=_now(),
            )
            return self._snapshot_locked()

    def stop(self, timeout_seconds: float = 2.0) -> FeishuProactiveState:
        """停止后台调度线程。"""
        with self._lock:
            thread = self._worker_thread
            if not self._worker_is_alive():
                return self._snapshot_locked(status="stopped")
        self._stop_event.set()
        if thread is not None:
            thread.join(timeout=timeout_seconds)
        with self._lock:
            self._state = replace(
                self._state,
                status="stopped",
                worker_alive=False,
                updated_at=_now(),
            )
            return self._snapshot_locked()

    def get_state(self) -> FeishuProactiveState:
        """返回当前服务状态快照。"""
        with self._lock:
            return self._snapshot_locked()

    def run_once(self) -> FeishuProactiveState:
        """手动触发一次完整调度 tick，供 CLI 调试和测试使用。"""
        return self._execute_tick(force=True)

    def _run_loop(self) -> None:
        """后台线程：按间隔执行调度 tick 直到 stop_event 触发。"""
        while not self._stop_event.wait(timeout=TICK_INTERVAL_SECONDS):
            self._execute_tick(force=False)
        with self._lock:
            self._state = replace(
                self._state,
                status="stopped",
                worker_alive=False,
                updated_at=_now(),
            )

    def _execute_tick(self, *, force: bool) -> FeishuProactiveState:
        """执行单次调度：按时间判断发现、审批、采集、intake 是否到期。"""
        user_client = self._build_user_client()
        if user_client is None:
            return self._record_error("user_client_unavailable")

        state = self.get_state()
        scopes_discovered = 0
        records_collected = 0
        approval_requests_sent = 0
        packets_enqueued = 0
        summary_tasks_created = 0
        last_error = ""

        if force or _is_due(state.last_discovery_at, DISCOVERY_INTERVAL_SECONDS):
            scopes_discovered, last_error = self._run_discovery(user_client)

        if force or _is_due(state.last_approval_requests_at, COLLECT_INTERVAL_SECONDS):
            approval_requests_sent = self._run_approval_requests()

        if force or _is_due(state.last_collect_at, COLLECT_INTERVAL_SECONDS):
            records_collected, last_error = self._run_collection(user_client)

        packets_enqueued = self._run_intake()

        if force or _is_due(state.last_summary_tasks_at, SUMMARY_TASK_INTERVAL_SECONDS):
            summary_tasks_created = self._run_summary_tasks()

        return self._update_state_after_tick(
            scopes_discovered=scopes_discovered,
            records_collected=records_collected,
            approval_requests_sent=approval_requests_sent,
            packets_enqueued=packets_enqueued,
            summary_tasks_created=summary_tasks_created,
            last_error=last_error,
        )

    def _run_discovery(self, user_client: Any) -> tuple[int, str]:
        """运行群组发现和文档根目录发现，返回 (发现数, 错误信息)。"""
        total = 0
        last_error = ""
        try:
            total += self._discover_groups(user_client)
        except Exception as exc:  # noqa: BLE001
            last_error = f"group_discovery_failed: {exc}"
        try:
            total += self._discover_doc_root(user_client)
        except Exception as exc:  # noqa: BLE001
            last_error = last_error or f"doc_root_discovery_failed: {exc}"
        with self._lock:
            self._state = replace(self._state, last_discovery_at=_now(), updated_at=_now())
        return total, last_error

    def _discover_groups(self, user_client: Any) -> int:
        """调用 GroupCandidateDiscovery 写入 candidate group_chat scope。"""
        result = GroupCandidateDiscovery(
            self.project_root, user_client, self.config, registry=self._registry
        ).discover()
        return result.scopes_written if result.ok else 0

    def _discover_doc_root(self, user_client: Any) -> int:
        """调用 UserDocumentCollector.discover_root 写入 candidate drive_folder scope。"""
        result = UserDocumentCollector(
            self.project_root, user_client, registry=self._registry
        ).discover_root(self.config)
        return 1 if result.ok else 0

    def _run_approval_requests(self) -> int:
        """为冷却期内未请求过的 candidate scope 发送审批卡片。"""
        if self._approval_service is None:
            return 0
        account_id = scope_account_id_from_config(self.config)
        candidates = self._registry.list_records(account_id=account_id, status="candidate")
        sent = 0
        for record in candidates:
            if sent >= MAX_APPROVAL_REQUESTS_PER_TICK:
                break
            if not _approval_cooldown_expired(record.last_approval_requested_at):
                continue
            try:
                result = self._approval_service.request_enable_scope(record)
                if result.ok:
                    self._registry.mark_approval_requested(
                        record.account_id, record.scope_type, record.scope_id
                    )
                    sent += 1
            except Exception:  # noqa: BLE001
                pass
        with self._lock:
            self._state = replace(
                self._state, last_approval_requests_at=_now(), updated_at=_now()
            )
        return sent

    def _run_collection(self, user_client: Any) -> tuple[int, str]:
        """对 enabled scope 运行私聊、群聊和云盘文档采集，返回 (写入数, 错误信息)。"""
        total = 0
        last_error = ""
        end_time = int(_now_dt().timestamp())
        start_time = end_time - COLLECT_INTERVAL_SECONDS

        try:
            dm_results = DirectMessageCollector(
                self.project_root, user_client
            ).collect_enabled_scopes(self.config, start_time=start_time, end_time=end_time)
            total += sum(r.items_written for r in dm_results if r.ok)
        except Exception as exc:  # noqa: BLE001
            last_error = f"dm_collect_failed: {exc}"

        try:
            gm_results = GroupMessageCollector(
                self.project_root, user_client
            ).collect_enabled_scopes(self.config, start_time=start_time, end_time=end_time)
            total += sum(r.items_written for r in gm_results if r.ok)
        except Exception as exc:  # noqa: BLE001
            last_error = last_error or f"gm_collect_failed: {exc}"

        try:
            doc_results = UserDocumentCollector(
                self.project_root, user_client, registry=self._registry
            ).collect_enabled_scopes(self.config)
            total += sum(r.items_written for r in doc_results if r.ok)
        except Exception as exc:  # noqa: BLE001
            last_error = last_error or f"doc_collect_failed: {exc}"

        with self._lock:
            self._state = replace(self._state, last_collect_at=_now(), updated_at=_now())
        return total, last_error

    def _run_intake(self) -> int:
        """把新增 ambient_context 送入 runtime 分析队列，返回派发 packet 数。"""
        if self._runtime_service is None:
            return 0
        result = AmbientAnalysisIntakeService(
            self.project_root, self._runtime_service, config=self.config
        ).enqueue_new_records()
        with self._lock:
            self._state = replace(self._state, last_intake_at=_now(), updated_at=_now())
        return result.packets_enqueued

    def _run_summary_tasks(self) -> int:
        """为冷却期已过的总结类型创建系统预制后台任务，返回本次创建数。"""
        try:
            result = self._summary_task_intake.create_due_summary_tasks()
            with self._lock:
                self._state = replace(
                    self._state, last_summary_tasks_at=_now(), updated_at=_now()
                )
            return result.tasks_created
        except Exception:  # noqa: BLE001
            with self._lock:
                self._state = replace(
                    self._state, last_summary_tasks_at=_now(), updated_at=_now()
                )
            return 0

    def _update_state_after_tick(
        self,
        *,
        scopes_discovered: int,
        records_collected: int,
        approval_requests_sent: int,
        packets_enqueued: int,
        summary_tasks_created: int,
        last_error: str,
    ) -> FeishuProactiveState:
        """把单次 tick 的结果写回服务状态。"""
        with self._lock:
            self._state = replace(
                self._state,
                tick_count=self._state.tick_count + 1,
                last_tick_at=_now(),
                last_scopes_discovered=scopes_discovered,
                last_records_collected=records_collected,
                last_approval_requests_sent=approval_requests_sent,
                last_packets_enqueued=packets_enqueued,
                last_summary_tasks_created=summary_tasks_created,
                last_error=last_error,
                updated_at=_now(),
            )
            return self._snapshot_locked()

    def _record_error(self, error: str) -> FeishuProactiveState:
        """记录单次 tick 的顶层错误并返回状态快照。"""
        with self._lock:
            self._state = replace(
                self._state,
                tick_count=self._state.tick_count + 1,
                last_tick_at=_now(),
                last_error=error,
                updated_at=_now(),
            )
            return self._snapshot_locked()

    def _build_user_client(self) -> Any:
        """按需构造飞书用户面 client；工厂未注入时返回 None。"""
        if self._user_client_factory is None:
            return None
        try:
            return self._user_client_factory()
        except Exception:  # noqa: BLE001
            return None

    def _worker_is_alive(self) -> bool:
        """判断后台线程是否仍活跃。"""
        return self._worker_thread is not None and self._worker_thread.is_alive()

    def _snapshot_locked(self, *, status: str = "") -> FeishuProactiveState:
        """在持锁条件下返回最新状态快照。"""
        current_status = status or self._state.status
        return replace(
            self._state,
            status=current_status,
            worker_alive=self._worker_is_alive(),
            updated_at=_now(),
        )


def _is_due(last_run_at: str, interval_seconds: int) -> bool:
    """判断距上次执行是否已超过间隔时间。"""
    if not last_run_at:
        return True
    try:
        last_dt = datetime.fromisoformat(last_run_at)
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return True
    return (_now_dt() - last_dt).total_seconds() >= interval_seconds


def _approval_cooldown_expired(last_requested_at: str) -> bool:
    """判断审批请求冷却期是否已过，允许再次发送审批卡片。"""
    if not last_requested_at:
        return True
    try:
        last_dt = datetime.fromisoformat(last_requested_at)
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return True
    return (_now_dt() - last_dt) >= timedelta(hours=APPROVAL_REQUEST_COOLDOWN_HOURS)


def _self_test() -> None:
    """验证 proactive service 可启动、run_once 不崩溃并可停止。"""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        # 不注入 user_client_factory，run_once 会走 user_client_unavailable 路径
        service = FeishuProactiveService(root, _FakeConfig())
        service.start()
        state = service.run_once()
        service.stop()

    assert state.tick_count == 1
    assert state.last_error == "user_client_unavailable"


class _FakeConfig:
    feishu_tenant_key = "tk_test"
    feishu_owner_open_id = "ou_test"
    feishu_owner_report_chat_id = ""


if __name__ == "__main__":
    _self_test()
    print("dutyflow feishu proactive_service self-test passed")
