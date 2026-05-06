# 本文件验证 FeishuProactiveService 的调度、状态管理和审批去重行为。

from __future__ import annotations

import sys
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dutyflow.feishu.proactive_service import (  # noqa: E402
    APPROVAL_REQUEST_COOLDOWN_HOURS,
    FeishuProactiveService,
    _approval_cooldown_expired,
    _is_due,
)
from dutyflow.feishu.scope_registry import (  # noqa: E402
    GROUP_CHAT_SCOPE,
    GROUP_MESSAGE_COLLECTOR,
    FeishuScopeRecord,
    FeishuScopeRegistry,
    scope_account_id_from_config,
)


class _FakeConfig:
    feishu_tenant_key = "tk_test"
    feishu_owner_open_id = "ou_test"
    feishu_owner_report_chat_id = "oc_report"


@dataclass
class _FakeDiscoveryResult:
    ok: bool = True
    scopes_written: int = 2
    scope_records: tuple = ()
    has_more: bool = False
    status: str = "ok"
    detail: str = ""


@dataclass
class _FakeDocRootResult:
    ok: bool = True
    scope_id: str = "fld_root"
    scope_record: Any = None
    detail: str = ""


@dataclass
class _FakeCollectResult:
    ok: bool = True
    items_written: int = 3
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
    packets_enqueued: int = 1
    record_ids_sent: tuple = ()
    analysis_ids: tuple = ()
    status: str = "ok"
    detail: str = ""


class _FakeGroupDiscovery:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def discover(self, **kwargs) -> _FakeDiscoveryResult:
        return _FakeDiscoveryResult(ok=True, scopes_written=2)


class _FakeDocCollector:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def discover_root(self, config, **kwargs) -> _FakeDocRootResult:
        return _FakeDocRootResult(ok=True)

    def collect_enabled_scopes(self, config, **kwargs) -> tuple:
        return (_FakeCollectResult(ok=True, items_written=2),)


class _FakeDMCollector:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def collect_enabled_scopes(self, config, **kwargs) -> tuple:
        return (_FakeCollectResult(ok=True, items_written=1),)


class _FakeGMCollector:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def collect_enabled_scopes(self, config, **kwargs) -> tuple:
        return (_FakeCollectResult(ok=True, items_written=2),)


class _FakeApprovalService:
    def __init__(self) -> None:
        self.calls: list[Any] = []

    def request_enable_scope(self, record) -> _FakeApprovalResult:
        self.calls.append(record)
        return _FakeApprovalResult(ok=True)


class _FakeRuntime:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def enqueue_perception(self, loop_input: dict) -> None:
        self.calls.append(loop_input)


def _make_service(root: Path, **kwargs) -> FeishuProactiveService:
    """构造注入了 fake 依赖的 ProactiveService，跳过真实飞书调用。"""
    service = FeishuProactiveService(root, _FakeConfig(), **kwargs)
    return service


class TestFeishuProactiveService(unittest.TestCase):
    """验证 FeishuProactiveService 的调度行为。"""

    def test_run_once_without_client_factory_returns_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = _make_service(Path(tmp))
            state = service.run_once()

        self.assertEqual(state.tick_count, 1)
        self.assertEqual(state.last_error, "user_client_unavailable")

    def test_run_once_increments_tick_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = _make_service(Path(tmp), user_client_factory=lambda: object())
            state = service.run_once()

        self.assertEqual(state.tick_count, 1)

    def test_start_and_stop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = _make_service(Path(tmp))
            service.start()
            state = service.stop()

        self.assertEqual(state.status, "stopped")
        self.assertFalse(state.worker_alive)

    def test_discovery_updates_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            service = _make_service(root, user_client_factory=lambda: object())
            # 注入 fake discovery 和 collector
            import unittest.mock as mock

            with mock.patch(
                "dutyflow.feishu.proactive_service.GroupCandidateDiscovery",
                _FakeGroupDiscovery,
            ), mock.patch(
                "dutyflow.feishu.proactive_service.UserDocumentCollector",
                _FakeDocCollector,
            ), mock.patch(
                "dutyflow.feishu.proactive_service.DirectMessageCollector",
                _FakeDMCollector,
            ), mock.patch(
                "dutyflow.feishu.proactive_service.GroupMessageCollector",
                _FakeGMCollector,
            ):
                state = service.run_once()

        self.assertGreater(state.last_scopes_discovered, 0)
        self.assertNotEqual(state.last_discovery_at, "")

    def test_collection_updates_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            service = _make_service(root, user_client_factory=lambda: object())
            import unittest.mock as mock

            with mock.patch(
                "dutyflow.feishu.proactive_service.GroupCandidateDiscovery",
                _FakeGroupDiscovery,
            ), mock.patch(
                "dutyflow.feishu.proactive_service.UserDocumentCollector",
                _FakeDocCollector,
            ), mock.patch(
                "dutyflow.feishu.proactive_service.DirectMessageCollector",
                _FakeDMCollector,
            ), mock.patch(
                "dutyflow.feishu.proactive_service.GroupMessageCollector",
                _FakeGMCollector,
            ):
                state = service.run_once()

        self.assertGreaterEqual(state.last_records_collected, 0)
        self.assertNotEqual(state.last_collect_at, "")

    def test_approval_requests_sent_for_candidate_scopes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry = FeishuScopeRegistry(root)
            record = FeishuScopeRecord(
                account_id=scope_account_id_from_config(_FakeConfig()),
                scope_type=GROUP_CHAT_SCOPE,
                scope_id="oc_g1",
                status="candidate",
                collector_names=(GROUP_MESSAGE_COLLECTOR,),
            )
            registry.upsert_candidate(record)
            approval_service = _FakeApprovalService()
            service = _make_service(
                root,
                user_client_factory=lambda: object(),
                approval_service=approval_service,
                registry=registry,
            )
            import unittest.mock as mock

            with mock.patch(
                "dutyflow.feishu.proactive_service.GroupCandidateDiscovery",
                _FakeGroupDiscovery,
            ), mock.patch(
                "dutyflow.feishu.proactive_service.UserDocumentCollector",
                _FakeDocCollector,
            ), mock.patch(
                "dutyflow.feishu.proactive_service.DirectMessageCollector",
                _FakeDMCollector,
            ), mock.patch(
                "dutyflow.feishu.proactive_service.GroupMessageCollector",
                _FakeGMCollector,
            ):
                state = service.run_once()

        self.assertEqual(state.last_approval_requests_sent, 1)
        self.assertEqual(len(approval_service.calls), 1)

    def test_approval_request_not_repeated_within_cooldown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry = FeishuScopeRegistry(root)
            record = FeishuScopeRecord(
                account_id=scope_account_id_from_config(_FakeConfig()),
                scope_type=GROUP_CHAT_SCOPE,
                scope_id="oc_g1",
                status="candidate",
                collector_names=(GROUP_MESSAGE_COLLECTOR,),
            )
            registry.upsert_candidate(record)
            approval_service = _FakeApprovalService()
            service = _make_service(
                root,
                user_client_factory=lambda: object(),
                approval_service=approval_service,
                registry=registry,
            )
            import unittest.mock as mock

            with mock.patch(
                "dutyflow.feishu.proactive_service.GroupCandidateDiscovery",
                _FakeGroupDiscovery,
            ), mock.patch(
                "dutyflow.feishu.proactive_service.UserDocumentCollector",
                _FakeDocCollector,
            ), mock.patch(
                "dutyflow.feishu.proactive_service.DirectMessageCollector",
                _FakeDMCollector,
            ), mock.patch(
                "dutyflow.feishu.proactive_service.GroupMessageCollector",
                _FakeGMCollector,
            ):
                service.run_once()
                state2 = service.run_once()

        self.assertEqual(state2.last_approval_requests_sent, 0)
        self.assertEqual(len(approval_service.calls), 1)

    def test_intake_updates_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = _FakeRuntime()
            service = _make_service(
                root,
                user_client_factory=lambda: object(),
                runtime_service=runtime,
            )
            import unittest.mock as mock

            with mock.patch(
                "dutyflow.feishu.proactive_service.GroupCandidateDiscovery",
                _FakeGroupDiscovery,
            ), mock.patch(
                "dutyflow.feishu.proactive_service.UserDocumentCollector",
                _FakeDocCollector,
            ), mock.patch(
                "dutyflow.feishu.proactive_service.DirectMessageCollector",
                _FakeDMCollector,
            ), mock.patch(
                "dutyflow.feishu.proactive_service.GroupMessageCollector",
                _FakeGMCollector,
            ):
                state = service.run_once()

        self.assertNotEqual(state.last_intake_at, "")


class TestApprovalDedup(unittest.TestCase):
    """验证审批去重辅助函数的边界行为。"""

    def test_empty_last_requested_is_expired(self) -> None:
        self.assertTrue(_approval_cooldown_expired(""))

    def test_recent_request_is_not_expired(self) -> None:
        from datetime import datetime, timezone

        recent = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.assertFalse(_approval_cooldown_expired(recent))

    def test_old_request_is_expired(self) -> None:
        old = "2020-01-01T00:00:00+00:00"
        self.assertTrue(_approval_cooldown_expired(old))

    def test_mark_approval_requested_persists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry = FeishuScopeRegistry(root)
            record = FeishuScopeRecord(
                account_id="local_owner",
                scope_type=GROUP_CHAT_SCOPE,
                scope_id="oc_g1",
                status="candidate",
                collector_names=(GROUP_MESSAGE_COLLECTOR,),
            )
            registry.upsert_candidate(record)
            updated = registry.mark_approval_requested("local_owner", GROUP_CHAT_SCOPE, "oc_g1")

        self.assertNotEqual(updated.last_approval_requested_at, "")

    def test_is_due_with_empty_last_run(self) -> None:
        self.assertTrue(_is_due("", 3600))

    def test_is_due_with_old_last_run(self) -> None:
        self.assertTrue(_is_due("2020-01-01T00:00:00+00:00", 3600))

    def test_is_not_due_with_recent_last_run(self) -> None:
        from datetime import datetime, timezone

        recent = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.assertFalse(_is_due(recent, 3600))


if __name__ == "__main__":
    unittest.main()
