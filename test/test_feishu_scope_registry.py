# 本文件验证飞书 Scope Registry 的 Markdown 落盘、状态流转和 collector 消费查询。

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dutyflow.feishu.scope_registry import (  # noqa: E402
    DIRECT_MESSAGE_COLLECTOR,
    P2P_CHAT_SCOPE,
    FeishuScopeRecord,
    FeishuScopeRegistry,
    scope_account_id_from_config,
    seed_owner_p2p_scope,
)


class TestFeishuScopeRegistry(unittest.TestCase):
    """验证飞书资源同步范围注册表第一版能力。"""

    def test_upsert_candidate_writes_detail_and_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry = FeishuScopeRegistry(root)

            record = registry.upsert_candidate(_scope("oc_1"))
            detail = registry.path_for("account_1", P2P_CHAT_SCOPE, "oc_1").read_text(encoding="utf-8")
            index = (root / "data/feishu/scopes/index.md").read_text(encoding="utf-8")

        self.assertEqual(record.status, "candidate")
        self.assertIn("schema: dutyflow.feishu_scope.v1", detail)
        self.assertIn("scope_id: oc_1", detail)
        self.assertIn("direct_message_collector", index)

    def test_approve_enable_and_list_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry = FeishuScopeRegistry(root)
            registry.upsert_candidate(_scope("oc_1"))

            registry.approve_scope("account_1", P2P_CHAT_SCOPE, "oc_1", approved_by="tester")
            enabled = registry.enable_scope("account_1", P2P_CHAT_SCOPE, "oc_1")
            listed = registry.list_enabled(DIRECT_MESSAGE_COLLECTOR, account_id="account_1")

        self.assertEqual(enabled.status, "enabled")
        self.assertEqual(enabled.approved_by, "tester")
        self.assertEqual(tuple(item.scope_id for item in listed), ("oc_1",))

    def test_disabled_scope_is_not_reenabled_by_candidate_upsert(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = FeishuScopeRegistry(Path(tmp))
            registry.upsert_candidate(_scope("oc_1"))
            registry.disable_scope("account_1", P2P_CHAT_SCOPE, "oc_1", reason="manual")

            updated = registry.upsert_candidate(_scope("oc_1"))

        self.assertEqual(updated.status, "disabled")
        self.assertEqual(updated.disabled_reason, "manual")

    def test_mark_permission_denied_records_error_without_enabled_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = FeishuScopeRegistry(Path(tmp))
            registry.upsert_candidate(_scope("oc_1"))
            registry.enable_scope("account_1", P2P_CHAT_SCOPE, "oc_1")

            denied = registry.mark_permission_denied("account_1", P2P_CHAT_SCOPE, "oc_1", "forbidden")
            listed = registry.list_enabled(DIRECT_MESSAGE_COLLECTOR)

        self.assertEqual(denied.status, "permission_denied")
        self.assertEqual(denied.permission_error, "forbidden")
        self.assertEqual(listed, ())

    def test_account_filter_keeps_scopes_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = FeishuScopeRegistry(Path(tmp))
            registry.upsert_candidate(_scope("oc_1", account_id="account_1"))
            registry.upsert_candidate(_scope("oc_2", account_id="account_2"))
            registry.enable_scope("account_1", P2P_CHAT_SCOPE, "oc_1")
            registry.enable_scope("account_2", P2P_CHAT_SCOPE, "oc_2")

            listed = registry.list_enabled(DIRECT_MESSAGE_COLLECTOR, account_id="account_2")

        self.assertEqual(tuple(item.scope_id for item in listed), ("oc_2",))

    def test_seed_owner_p2p_scope_from_env_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = FeishuScopeRegistry(Path(tmp))
            config = SimpleNamespace(
                feishu_tenant_key="tenant_1",
                feishu_owner_open_id="ou_1",
                feishu_owner_user_id="uid_1",
                feishu_owner_report_chat_id="oc_seed",
            )

            seeded = seed_owner_p2p_scope(registry, config)
            account_id = scope_account_id_from_config(config)
            listed = registry.list_enabled(DIRECT_MESSAGE_COLLECTOR, account_id=account_id)

        self.assertIsNotNone(seeded)
        self.assertEqual(tuple(item.scope_id for item in listed), ("oc_seed",))
        self.assertEqual(listed[0].discovered_from, "env")

    def test_resolve_identifier_accepts_record_id_or_scope_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = FeishuScopeRegistry(Path(tmp))
            record = registry.upsert_candidate(_scope("oc_1"))

            by_record_id = registry.resolve_identifier(record.record_id)
            by_scope_id = registry.resolve_identifier("oc_1")

        self.assertEqual(by_record_id[0].scope_id, "oc_1")
        self.assertEqual(by_scope_id[0].record_id, record.record_id)


def _scope(scope_id: str, *, account_id: str = "account_1") -> FeishuScopeRecord:
    """构造测试用 p2p scope。"""
    return FeishuScopeRecord(
        account_id=account_id,
        scope_type=P2P_CHAT_SCOPE,
        scope_id=scope_id,
        collector_names=(DIRECT_MESSAGE_COLLECTOR,),
        discovered_from="manual_add",
    )


def _self_test() -> None:
    """运行本文件所有单元测试。"""
    unittest.main(verbosity=2)


if __name__ == "__main__":
    _self_test()
