# 本文件验证飞书 group_candidate_discovery 的发现、过滤和 scope registry 写入行为。

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dutyflow.feishu.collector_budget import CollectorBudget  # noqa: E402
from dutyflow.feishu.collectors.group_candidate_discovery import (  # noqa: E402
    DISCOVERY_NAME,
    GroupCandidateDiscovery,
)
from dutyflow.feishu.scope_registry import (  # noqa: E402
    GROUP_CHAT_SCOPE,
    GROUP_DOCUMENT_COLLECTOR,
    GROUP_MESSAGE_COLLECTOR,
    FeishuScopeRegistry,
)
from dutyflow.feishu.user_request import FeishuUserResponse  # noqa: E402


class _FakeConfig:
    feishu_tenant_key = "tk_1"
    feishu_owner_open_id = "ou_owner"


class _FakeUserClient:
    """测试替身：模拟 FeishuUserClient.get 并记录请求参数。"""

    def __init__(self, responses: list[FeishuUserResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, **kwargs: Any) -> FeishuUserResponse:
        self.calls.append({"url": url, **kwargs})
        if not self.responses:
            raise AssertionError("unexpected extra request")
        return self.responses.pop(0)


def _ok_response(
    items: list[dict[str, Any]],
    *,
    has_more: bool = False,
    page_token: str = "",
) -> FeishuUserResponse:
    """构造成功的群列表 API 响应。"""
    return FeishuUserResponse(
        ok=True,
        status="ok",
        http_status=200,
        feishu_code=0,
        detail="",
        data={"items": items, "has_more": has_more, "page_token": page_token},
        page_token=page_token,
        has_more=has_more,
        raw_path="",
    )


def _error_response(status: str = "api_error") -> FeishuUserResponse:
    """构造失败的 API 响应。"""
    return FeishuUserResponse(
        ok=False,
        status=status,
        http_status=400,
        feishu_code=-1,
        detail="request failed",
        data={},
        page_token="",
        has_more=False,
        raw_path="",
    )


def _group_item(chat_id: str, owner: str = "ou_1") -> dict[str, Any]:
    return {"chat_id": chat_id, "chat_mode": "group", "owner_user_id": owner}


def _p2p_item(chat_id: str) -> dict[str, Any]:
    return {"chat_id": chat_id, "chat_mode": "p2p", "owner_user_id": "ou_1"}


class TestGroupCandidateDiscovery(unittest.TestCase):
    """验证群组发现的核心行为边界。"""

    def test_discovers_groups_and_writes_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            client = _FakeUserClient([
                _ok_response([_group_item("oc_g1"), _group_item("oc_g2")])
            ])
            discovery = GroupCandidateDiscovery(root, client, _FakeConfig())  # type: ignore[arg-type]

            result = discovery.discover()

        self.assertTrue(result.ok)
        self.assertEqual(result.scopes_written, 2)
        ids = {r.scope_id for r in result.scope_records}
        self.assertIn("oc_g1", ids)
        self.assertIn("oc_g2", ids)

    def test_filters_out_p2p_chats(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            client = _FakeUserClient([
                _ok_response([
                    _group_item("oc_g1"),
                    _p2p_item("oc_p1"),
                    _group_item("oc_g2"),
                ])
            ])
            discovery = GroupCandidateDiscovery(root, client, _FakeConfig())  # type: ignore[arg-type]

            result = discovery.discover()

        self.assertTrue(result.ok)
        self.assertEqual(result.scopes_written, 2)
        ids = {r.scope_id for r in result.scope_records}
        self.assertNotIn("oc_p1", ids)

    def test_scope_record_has_correct_type_and_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            client = _FakeUserClient([_ok_response([_group_item("oc_g1")])])
            discovery = GroupCandidateDiscovery(root, client, _FakeConfig())  # type: ignore[arg-type]

            result = discovery.discover()
            record = result.scope_records[0]

        self.assertEqual(record.scope_type, GROUP_CHAT_SCOPE)
        self.assertEqual(record.status, "candidate")
        self.assertIn(GROUP_MESSAGE_COLLECTOR, record.collector_names)
        self.assertIn(GROUP_DOCUMENT_COLLECTOR, record.collector_names)
        self.assertEqual(record.discovered_from, "oauth_chat_list")

    def test_api_request_uses_correct_params(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            client = _FakeUserClient([_ok_response([])])
            discovery = GroupCandidateDiscovery(root, client, _FakeConfig())  # type: ignore[arg-type]

            discovery.discover()
            params = client.calls[0]["params"]

        self.assertEqual(params["page_size"], 100)
        self.assertEqual(params["user_id_type"], "open_id")
        self.assertNotIn("page_token", params)

    def test_paginates_when_has_more(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            client = _FakeUserClient([
                _ok_response([_group_item("oc_g1")], has_more=True, page_token="pt_2"),
                _ok_response([_group_item("oc_g2")]),
            ])
            budget = CollectorBudget(DISCOVERY_NAME, max_pages_per_run=2)
            discovery = GroupCandidateDiscovery(root, client, _FakeConfig(), budget=budget)  # type: ignore[arg-type]

            result = discovery.discover()
            second_call_params = client.calls[1]["params"]

        self.assertEqual(len(client.calls), 2)
        self.assertEqual(result.scopes_written, 2)
        self.assertEqual(second_call_params["page_token"], "pt_2")

    def test_stops_pagination_at_budget_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            client = _FakeUserClient([
                _ok_response([_group_item("oc_g1")], has_more=True, page_token="pt_2"),
                _ok_response([_group_item("oc_g2")], has_more=True, page_token="pt_3"),
            ])
            budget = CollectorBudget(DISCOVERY_NAME, max_pages_per_run=2)
            discovery = GroupCandidateDiscovery(root, client, _FakeConfig(), budget=budget)  # type: ignore[arg-type]

            result = discovery.discover()

        self.assertEqual(len(client.calls), 2)
        self.assertTrue(result.has_more)

    def test_item_budget_does_not_write_unreported_extra_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            client = _FakeUserClient([
                _ok_response([
                    _group_item("oc_g1"),
                    _group_item("oc_g2"),
                    _group_item("oc_g3"),
                ])
            ])
            budget = CollectorBudget(DISCOVERY_NAME, max_items_per_run=2)
            discovery = GroupCandidateDiscovery(root, client, _FakeConfig(), budget=budget)  # type: ignore[arg-type]

            result = discovery.discover()
            records = FeishuScopeRegistry(root).list_records()

        self.assertEqual(result.scopes_written, 2)
        self.assertEqual(len(records), 2)
        self.assertTrue(result.has_more)
        self.assertNotIn("oc_g3", {record.scope_id for record in records})

    def test_returns_error_on_api_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            client = _FakeUserClient([_error_response("permission_denied")])
            discovery = GroupCandidateDiscovery(root, client, _FakeConfig())  # type: ignore[arg-type]

            result = discovery.discover()

        self.assertFalse(result.ok)
        self.assertEqual(result.status, "permission_denied")
        self.assertEqual(result.scopes_written, 0)

    def test_existing_candidate_not_overwritten_by_rediscovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry = FeishuScopeRegistry(root)
            client = _FakeUserClient([
                _ok_response([_group_item("oc_g1")]),
            ])
            discovery = GroupCandidateDiscovery(root, client, _FakeConfig(), registry=registry)  # type: ignore[arg-type]

            result1 = discovery.discover()
            first_record = result1.scope_records[0]
            approved = registry.approve_scope(
                first_record.account_id, GROUP_CHAT_SCOPE, "oc_g1"
            )
            client2 = _FakeUserClient([_ok_response([_group_item("oc_g1")])])
            discovery2 = GroupCandidateDiscovery(root, client2, _FakeConfig(), registry=registry)  # type: ignore[arg-type]
            result2 = discovery2.discover()
            final_record = registry.read(first_record.account_id, GROUP_CHAT_SCOPE, "oc_g1")

        self.assertEqual(approved.status, "approved")
        self.assertEqual(result2.scopes_written, 1)
        self.assertIsNotNone(final_record)
        assert final_record is not None
        self.assertEqual(final_record.status, "approved")

    def test_empty_chat_list_returns_zero_written(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            client = _FakeUserClient([_ok_response([])])
            discovery = GroupCandidateDiscovery(root, client, _FakeConfig())  # type: ignore[arg-type]

            result = discovery.discover()

        self.assertTrue(result.ok)
        self.assertEqual(result.scopes_written, 0)
        self.assertFalse(result.has_more)


if __name__ == "__main__":
    unittest.main()
