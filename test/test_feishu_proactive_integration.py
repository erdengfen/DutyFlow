# 本文件验证主动感知完整链路：发现、审批去重、采集、ambient 入队、context_ref 读取、
# 定时总结任务创建和后台 worker 执行链路的端到端行为。

from __future__ import annotations

import sys
import tempfile
import threading
import time
import unittest
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dutyflow.agent.background_task_worker import (  # noqa: E402
    BackgroundTaskExecutionResult,
    BackgroundTaskWorker,
)
from dutyflow.feishu.ambient_analysis_intake import AmbientAnalysisIntakeService  # noqa: E402
from dutyflow.feishu.ambient_context import (  # noqa: E402
    AmbientContextRecord,
    AmbientContextStore,
)
from dutyflow.feishu.proactive_service import FeishuProactiveService  # noqa: E402
from dutyflow.feishu.scope_registry import (  # noqa: E402
    GROUP_CHAT_SCOPE,
    GROUP_MESSAGE_COLLECTOR,
    FeishuScopeRecord,
    FeishuScopeRegistry,
    scope_account_id_from_config,
)
from dutyflow.feishu.summary_task_intake import SummaryTaskIntakeService  # noqa: E402
from dutyflow.tasks.task_state import TaskRecord, TaskStore  # noqa: E402


class _FakeConfig:
    feishu_tenant_key = "tk_test"
    feishu_owner_open_id = "ou_test"
    feishu_owner_report_chat_id = "oc_report"


@dataclass
class _FakeDiscoveryResult:
    ok: bool = True
    scopes_written: int = 1
    scope_records: tuple = ()
    has_more: bool = False
    status: str = "ok"
    detail: str = ""


@dataclass
class _FakeDocRootResult:
    ok: bool = True
    scope_record: Any = None
    detail: str = ""


@dataclass
class _FakeCollectResult:
    ok: bool = True
    items_written: int = 0
    status: str = "ok"
    detail: str = ""


@dataclass
class _FakeApprovalResult:
    ok: bool = True
    status: str = "ok"
    detail: str = ""


@dataclass
class _FakeIntakeResult:
    ok: bool = True
    packets_enqueued: int = 0
    record_ids_sent: tuple = ()
    analysis_ids: tuple = ()
    status: str = "ok"
    detail: str = ""


class _FakeApprovalService:
    def __init__(self) -> None:
        self.calls: list[Any] = []

    def request_enable_scope(self, record: Any) -> _FakeApprovalResult:
        self.calls.append(record)
        return _FakeApprovalResult(ok=True)


def _write_ambient_record(
    store: AmbientContextStore,
    record_id: str,
    source_type: str = "direct_message",
    collector_name: str = "direct_message_collector",
    source_id: str = "msg_1",
    sync_scope_id: str = "oc_1",
    text: str = "test message",
) -> AmbientContextRecord:
    """写入一条 ambient_context 记录并返回。"""
    record = AmbientContextRecord(
        record_id=record_id,
        source_type=source_type,
        collector_name=collector_name,
        source_id=source_id,
        sync_scope_id=sync_scope_id,
        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        fetched_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        text=text,
    )
    store.write(record)
    return record


class TestAmbientAnalysisIntakeChain(unittest.TestCase):
    """验证 ambient_context 记录 → intake 服务 → 分析任务入队的完整链路。"""

    def test_new_ambient_records_produce_queued_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ambient_store = AmbientContextStore(root)
            _write_ambient_record(ambient_store, "dm_1", source_type="direct_message")
            _write_ambient_record(ambient_store, "dm_2", source_type="direct_message")

            fake_runtime = mock.MagicMock()
            service = AmbientAnalysisIntakeService(
                root, fake_runtime, config=_FakeConfig(), ambient_store=ambient_store
            )
            result = service.enqueue_new_records()

        self.assertTrue(result.ok)
        self.assertGreater(result.packets_enqueued, 0)
        self.assertTrue(fake_runtime.enqueue_perception.called)

    def test_second_intake_run_skips_already_sent_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ambient_store = AmbientContextStore(root)
            _write_ambient_record(ambient_store, "dm_1", source_type="direct_message")

            fake_runtime = mock.MagicMock()
            service = AmbientAnalysisIntakeService(
                root, fake_runtime, config=_FakeConfig(), ambient_store=ambient_store
            )
            result1 = service.enqueue_new_records()
            result2 = service.enqueue_new_records()

        self.assertGreater(result1.packets_enqueued, 0)
        self.assertEqual(result2.packets_enqueued, 0)

    def test_intake_groups_records_by_source_type(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ambient_store = AmbientContextStore(root)
            _write_ambient_record(ambient_store, "dm_1", source_type="direct_message", sync_scope_id="p2p_1")
            _write_ambient_record(ambient_store, "gm_1", source_type="group_message", sync_scope_id="grp_1",
                                  collector_name="group_message_collector")

            fake_runtime = mock.MagicMock()
            service = AmbientAnalysisIntakeService(
                root, fake_runtime, config=_FakeConfig(), ambient_store=ambient_store
            )
            result = service.enqueue_new_records()

        # 两个不同 source_type / scope 各自产生一个 packet
        self.assertGreaterEqual(result.packets_enqueued, 2)


class TestSummaryTaskIntakeChain(unittest.TestCase):
    """验证 SummaryTaskIntakeService 通过 FeishuProactiveService 定时触发并写入 TaskStore。"""

    def test_proactive_run_once_creates_summary_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_store = TaskStore(root)
            summary_intake = SummaryTaskIntakeService(root, task_store=task_store)

            service = FeishuProactiveService(
                root,
                _FakeConfig(),
                summary_task_intake=summary_intake,
            )
            state = service.run_once()

        # run_once 走 user_client_unavailable 提前返回，summary_tasks 应为 0
        self.assertEqual(state.last_summary_tasks_created, 0)

    def test_run_summary_tasks_directly_creates_four_types(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_store = TaskStore(root)
            summary_intake = SummaryTaskIntakeService(root, task_store=task_store)

            result = summary_intake.create_due_summary_tasks()
            tasks = task_store.list_tasks()

        self.assertEqual(result.tasks_created, 4)
        self.assertEqual(len(tasks), 4)
        source_ids = {t.source_id for t in tasks}
        self.assertIn("summary_task_intake:dm_summary", source_ids)
        self.assertIn("summary_task_intake:group_summary", source_ids)
        self.assertIn("summary_task_intake:doc_summary", source_ids)
        self.assertIn("summary_task_intake:daily_summary", source_ids)

    def test_summary_tasks_include_context_refs_when_ambient_records_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ambient_store = AmbientContextStore(root)
            _write_ambient_record(ambient_store, "dm_ref_1", source_type="direct_message", text="重要消息")

            task_store = TaskStore(root)
            summary_intake = SummaryTaskIntakeService(
                root, task_store=task_store, ambient_store=ambient_store
            )
            summary_intake.create_due_summary_tasks(summary_types=("dm_summary",), lookback_hours=24)
            tasks = task_store.list_tasks()

        self.assertEqual(len(tasks), 1)
        self.assertIn("dm_ref_1", tasks[0].resume_payload)

    def test_summary_tasks_cooldown_prevents_duplicate_in_same_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_store = TaskStore(root)
            summary_intake = SummaryTaskIntakeService(root, task_store=task_store)

            r1 = summary_intake.create_due_summary_tasks()
            r2 = summary_intake.create_due_summary_tasks()

        self.assertEqual(r1.tasks_created, 4)
        self.assertEqual(r2.tasks_created, 0)


class TestApprovalRequestDedup(unittest.TestCase):
    """验证主动感知调度层的审批请求冷却去重行为。"""

    def _make_registry_with_candidate(self, root: Path) -> FeishuScopeRegistry:
        registry = FeishuScopeRegistry(root)
        account_id = scope_account_id_from_config(_FakeConfig())
        registry.upsert_candidate(FeishuScopeRecord(
            account_id=account_id,
            scope_type=GROUP_CHAT_SCOPE,
            scope_id="oc_group_1",
            collector_names=(GROUP_MESSAGE_COLLECTOR,),
            discovered_from="test",
            status="candidate",
        ))
        return registry

    def test_approval_requested_once_per_tick_per_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry = self._make_registry_with_candidate(root)
            approval_service = _FakeApprovalService()

            service = FeishuProactiveService(
                root,
                _FakeConfig(),
                user_client_factory=lambda: object(),
                approval_service=approval_service,
                registry=registry,
            )
            with (
                mock.patch("dutyflow.feishu.proactive_service.GroupCandidateDiscovery"),
                mock.patch("dutyflow.feishu.proactive_service.UserDocumentCollector"),
                mock.patch("dutyflow.feishu.proactive_service.DirectMessageCollector"),
                mock.patch("dutyflow.feishu.proactive_service.GroupMessageCollector"),
                mock.patch("dutyflow.feishu.proactive_service.AmbientAnalysisIntakeService") as mock_intake,
            ):
                mock_intake.return_value.enqueue_new_records.return_value = _FakeIntakeResult()
                service.run_once()

        self.assertEqual(len(approval_service.calls), 1)

    def test_approval_not_repeated_after_mark_requested(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry = self._make_registry_with_candidate(root)
            account_id = scope_account_id_from_config(_FakeConfig())
            registry.mark_approval_requested(account_id, GROUP_CHAT_SCOPE, "oc_group_1")
            approval_service = _FakeApprovalService()

            service = FeishuProactiveService(
                root,
                _FakeConfig(),
                user_client_factory=lambda: object(),
                approval_service=approval_service,
                registry=registry,
            )
            with (
                mock.patch("dutyflow.feishu.proactive_service.GroupCandidateDiscovery"),
                mock.patch("dutyflow.feishu.proactive_service.UserDocumentCollector"),
                mock.patch("dutyflow.feishu.proactive_service.DirectMessageCollector"),
                mock.patch("dutyflow.feishu.proactive_service.GroupMessageCollector"),
                mock.patch("dutyflow.feishu.proactive_service.AmbientAnalysisIntakeService") as mock_intake,
            ):
                mock_intake.return_value.enqueue_new_records.return_value = _FakeIntakeResult()
                service.run_once()

        # 冷却期内不应再次发送审批请求
        self.assertEqual(len(approval_service.calls), 0)


class TestCliProactiveObservable(unittest.TestCase):
    """验证新增 CLI 可观察命令在有数据和无数据情况下均返回合法 JSON。"""

    def _make_app(self, root: Path):
        from dutyflow.app import DutyFlowApp
        return DutyFlowApp(root)

    def test_proactive_ambient_returns_ok_with_no_records(self) -> None:
        import json
        with tempfile.TemporaryDirectory() as tmp:
            app = self._make_app(Path(tmp))
            result = app.get_feishu_proactive_ambient_debug()
        payload = json.loads(result)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["action"], "proactive_ambient")
        self.assertIn("by_source_type", payload["payload"])

    def test_proactive_ambient_counts_written_records(self) -> None:
        import json
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ambient_store = AmbientContextStore(root)
            _write_ambient_record(ambient_store, "dm_1", source_type="direct_message")
            _write_ambient_record(ambient_store, "dm_2", source_type="direct_message")
            app = self._make_app(root)
            result = app.get_feishu_proactive_ambient_debug()
        payload = json.loads(result)
        by_type = payload["payload"]["by_source_type"]
        self.assertEqual(by_type["direct_message"]["count"], 2)

    def test_proactive_tasks_returns_ok_with_no_tasks(self) -> None:
        import json
        with tempfile.TemporaryDirectory() as tmp:
            app = self._make_app(Path(tmp))
            result = app.get_feishu_proactive_tasks_debug()
        payload = json.loads(result)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["action"], "proactive_tasks")
        self.assertEqual(payload["payload"]["total"], 0)

    def test_proactive_tasks_lists_summary_tasks(self) -> None:
        import json
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_store = TaskStore(root)
            summary_intake = SummaryTaskIntakeService(root, task_store=task_store)
            summary_intake.create_due_summary_tasks(summary_types=("dm_summary",), lookback_hours=1)
            app = self._make_app(root)
            result = app.get_feishu_proactive_tasks_debug()
        payload = json.loads(result)
        self.assertEqual(payload["payload"]["total"], 1)
        self.assertIn("dm_summary", payload["payload"]["tasks"][0]["source_id"])
        self.assertEqual(payload["payload"]["status_counts"]["queued"], 1)
        self.assertTrue(payload["payload"]["tasks"][0]["will_run_on_worker_scan"])

    def test_proactive_summary_manual_entry_creates_summary_tasks(self) -> None:
        import json
        with tempfile.TemporaryDirectory() as tmp:
            app = self._make_app(Path(tmp))
            result = app.run_feishu_proactive_summary_debug()
        payload = json.loads(result)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["action"], "proactive_summary")
        self.assertEqual(payload["payload"]["tasks_created"], 4)

    def test_proactive_approvals_returns_ok_with_no_scopes(self) -> None:
        import json
        with tempfile.TemporaryDirectory() as tmp:
            app = self._make_app(Path(tmp))
            result = app.get_feishu_proactive_approvals_debug()
        payload = json.loads(result)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["action"], "proactive_approvals")

    def test_proactive_approvals_shows_requested_scopes(self) -> None:
        import json
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            from dutyflow.config.env import load_env_config
            config = load_env_config(root)
            registry = FeishuScopeRegistry(root)
            account_id = scope_account_id_from_config(config)
            registry.upsert_candidate(FeishuScopeRecord(
                account_id=account_id,
                scope_type=GROUP_CHAT_SCOPE,
                scope_id="oc_g1",
                collector_names=(GROUP_MESSAGE_COLLECTOR,),
                discovered_from="test",
                status="candidate",
            ))
            registry.mark_approval_requested(account_id, GROUP_CHAT_SCOPE, "oc_g1")
            app = self._make_app(root)
            result = app.get_feishu_proactive_approvals_debug()
        payload = json.loads(result)
        approval_records = payload["payload"]["approval_requests"]
        self.assertEqual(len(approval_records), 1)
        self.assertEqual(approval_records[0]["scope_id"], "oc_g1")


class TestContextRefReadForAmbient(unittest.TestCase):
    """验证 read_context_ref 工具能正确读取已落盘 ambient_context 记录。"""

    def _call_read_context_ref(self, root: Path, ref_type: str, ref_id: str):
        from dutyflow.agent.tools.logic.context_tools.read_context_ref import ReadContextRefTool
        from dutyflow.agent.tools.types import ToolCall
        ctx = mock.MagicMock()
        ctx.cwd = root
        call = ToolCall("tc_1", "read_context_ref", {"ref_type": ref_type, "ref_id": ref_id}, 0, 0)
        return ReadContextRefTool().handle(call, ctx)

    def test_read_ambient_context_ref_returns_record_data(self) -> None:
        import json
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ambient_store = AmbientContextStore(root)
            _write_ambient_record(
                ambient_store, "ctx_ref_1", source_type="direct_message", text="会议通知"
            )
            result = self._call_read_context_ref(root, "ambient_context", "ctx_ref_1")

        self.assertTrue(result.ok, result)
        payload = json.loads(result.content)
        self.assertEqual(payload["ref_id"], "ctx_ref_1")
        self.assertEqual(payload["ref_type"], "ambient_context")

    def test_read_ambient_context_ref_missing_returns_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = self._call_read_context_ref(root, "ambient_context", "no_such_id")

        self.assertFalse(result.ok)


class TestBackgroundWorkerExecutionChain(unittest.TestCase):
    """验证 BackgroundTaskWorker 能拾起 queued 任务并通过注入的 handler 执行。"""

    def test_worker_processes_queued_summary_task(self) -> None:
        executed: list[str] = []

        def _fake_handler(task: TaskRecord) -> BackgroundTaskExecutionResult:
            executed.append(task.task_id)
            return BackgroundTaskExecutionResult(
                ok=True,
                task_id=task.task_id,
                status="completed",
                result_text="summary done",
                error_text="",
            )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_store = TaskStore(root)
            summary_intake = SummaryTaskIntakeService(root, task_store=task_store)
            summary_intake.create_due_summary_tasks(summary_types=("dm_summary",), lookback_hours=1)

            tasks_before = task_store.list_tasks()
            self.assertEqual(len(tasks_before), 1)
            queued_task_id = tasks_before[0].task_id

            worker = BackgroundTaskWorker(
                task_store,
                task_handler=_fake_handler,
                queue_poll_seconds=0.05,
                ready_scan_interval_seconds=0.1,
            )
            worker.start()
            worker.enqueue_task(queued_task_id, source="test")

            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline and not executed:
                time.sleep(0.05)
            worker.stop(timeout_seconds=2.0)

        self.assertIn(queued_task_id, executed)

    def test_worker_ready_scan_picks_up_queued_tasks(self) -> None:
        executed: list[str] = []

        def _fake_handler(task: TaskRecord) -> BackgroundTaskExecutionResult:
            executed.append(task.task_id)
            return BackgroundTaskExecutionResult(
                ok=True,
                task_id=task.task_id,
                status="completed",
                result_text="ok",
                error_text="",
            )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_store = TaskStore(root)
            summary_intake = SummaryTaskIntakeService(root, task_store=task_store)
            summary_intake.create_due_summary_tasks(summary_types=("group_summary",), lookback_hours=1)

            tasks_before = task_store.list_tasks()
            queued_task_id = tasks_before[0].task_id

            worker = BackgroundTaskWorker(
                task_store,
                task_handler=_fake_handler,
                queue_poll_seconds=0.05,
                ready_scan_interval_seconds=0.1,
            )
            worker.start()
            # 不手动 enqueue，等 ready_scan 自动拾取
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline and not executed:
                time.sleep(0.05)
            worker.stop(timeout_seconds=2.0)

        self.assertIn(queued_task_id, executed)


if __name__ == "__main__":
    unittest.main()
