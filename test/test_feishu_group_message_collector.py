# 本文件验证飞书 group_message_collector 的请求参数、预算、落盘和 sync_state 行为。

from __future__ import annotations

import json
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
from dutyflow.feishu.collectors.group_message_collector import (  # noqa: E402
    COLLECTOR_NAME,
    SOURCE_TYPE,
    GroupMessageCollector,
)
from dutyflow.feishu.scope_registry import (  # noqa: E402
    GROUP_CHAT_SCOPE,
    FeishuScopeRecord,
    FeishuScopeRegistry,
)
from dutyflow.feishu.sync_state import FeishuSyncStateStore  # noqa: E402
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
    raw_path: str = "",
) -> FeishuUserResponse:
    """构造成功的群消息 API 响应。"""
    return FeishuUserResponse(
        ok=True,
        status="ok",
        http_status=200,
        feishu_code=0,
        detail="",
        data={"items": items, "has_more": has_more, "page_token": page_token},
        page_token=page_token,
        has_more=has_more,
        raw_path=raw_path,
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


def _text_message(
    message_id: str = "om_grp1",
    chat_id: str = "oc_grp1",
    create_time: str = "1778040000000",
    text: str = "hello group",
) -> dict[str, Any]:
    """构造飞书群文本消息 item。"""
    return {
        "message_id": message_id,
        "create_time": create_time,
        "chat_id": chat_id,
        "sender": {"id": "ou_1", "id_type": "open_id"},
        "body": {"content": json.dumps({"text": text})},
        "msg_type": "text",
    }


def _doc_link_message(message_id: str = "om_grp2", chat_id: str = "oc_grp1") -> dict[str, Any]:
    """构造包含飞书云文档链接的群消息 item。"""
    return {
        "message_id": message_id,
        "create_time": "1778040001000",
        "chat_id": chat_id,
        "sender": {"id": "ou_2", "id_type": "open_id"},
        "body": {"content": json.dumps({"text": "见 https://example.feishu.cn/docx/token_abc"})},
        "msg_type": "text",
    }


class TestGroupMessageCollector(unittest.TestCase):
    """验证 group_message_collector 第一版功能边界。"""

    def test_collect_text_message_writes_ambient_context_and_sync_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw_path = root / "data/feishu/raw/2026-05-06/raw_gmc.md"
            client = _FakeUserClient([_ok_response([_text_message()], raw_path=str(raw_path))])
            collector = GroupMessageCollector(root, client)  # type: ignore[arg-type]

            result = collector.collect("oc_grp1", start_time=1778039900, end_time=1778040100)
            detail = (root / result.record_paths[0]).read_text(encoding="utf-8")
            state = FeishuSyncStateStore(root).read(COLLECTOR_NAME, "oc_grp1")
            params = client.calls[0]["params"]

        self.assertTrue(result.ok)
        self.assertEqual(result.items_written, 1)
        self.assertEqual(params["container_id"], "oc_grp1")
        self.assertEqual(params["container_id_type"], "chat")
        self.assertEqual(params["sort_type"], "ByCreateTimeAsc")
        self.assertEqual(params["page_size"], 50)
        self.assertIn("om_grp1", detail)
        self.assertIn("group_message", detail)
        self.assertNotIn(str(root), detail)
        self.assertEqual(state.cursor, "1778040000000")
        self.assertEqual(state.next_cursor, "1778040000")

    def test_record_id_prefix_is_gm(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            client = _FakeUserClient([_ok_response([_text_message("om_grp1")])])
            collector = GroupMessageCollector(root, client)  # type: ignore[arg-type]

            result = collector.collect("oc_grp1", start_time=1778039900, end_time=1778040100)
            detail = (root / result.record_paths[0]).read_text(encoding="utf-8")

        self.assertIn("gm_om_grp1", detail)

    def test_collect_extracts_doc_links(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            client = _FakeUserClient([_ok_response([_doc_link_message()])])
            collector = GroupMessageCollector(root, client)  # type: ignore[arg-type]

            result = collector.collect("oc_grp1", start_time=1778039900, end_time=1778040100)
            detail = (root / result.record_paths[0]).read_text(encoding="utf-8")

        self.assertTrue(result.ok)
        self.assertIn("token_abc", detail)

    def test_collect_uses_page_token_for_next_page(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            client = _FakeUserClient([
                _ok_response([_text_message("om_grp1")], has_more=True, page_token="p2"),
                _ok_response([_text_message("om_grp2", create_time="1778040001000")]),
            ])
            budget = CollectorBudget(COLLECTOR_NAME, max_pages_per_run=2)
            collector = GroupMessageCollector(root, client, budget=budget)  # type: ignore[arg-type]

            result = collector.collect("oc_grp1", start_time=1778039900, end_time=1778040100)
            second_params = client.calls[1]["params"]

        self.assertEqual(result.items_written, 2)
        self.assertEqual(second_params["page_token"], "p2")

    def test_stops_at_max_pages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            client = _FakeUserClient([
                _ok_response([_text_message("om_grp1")], has_more=True, page_token="p2"),
                _ok_response([_text_message("om_grp2", create_time="1778040001000")], has_more=True, page_token="p3"),
            ])
            budget = CollectorBudget(COLLECTOR_NAME, max_pages_per_run=2)
            collector = GroupMessageCollector(root, client, budget=budget)  # type: ignore[arg-type]

            result = collector.collect("oc_grp1", start_time=1778039900, end_time=1778040100)

        self.assertEqual(len(client.calls), 2)
        self.assertTrue(result.has_more)
        self.assertEqual(result.stopped_reason, "max_pages_per_run")

    def test_failure_response_returns_error_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            client = _FakeUserClient([_error_response("permission_denied")])
            collector = GroupMessageCollector(root, client)  # type: ignore[arg-type]

            result = collector.collect("oc_grp1", start_time=1778039900, end_time=1778040100)

        self.assertFalse(result.ok)
        self.assertEqual(result.status, "permission_denied")
        self.assertEqual(result.items_written, 0)

    def test_collect_enabled_scopes_marks_registry_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _enable_group_scope(root, "oc_grp1")
            client = _FakeUserClient([_ok_response([_text_message()])])
            collector = GroupMessageCollector(root, client)  # type: ignore[arg-type]

            results = collector.collect_enabled_scopes(
                _FakeConfig(),  # type: ignore[arg-type]
                start_time=1778039900,
                end_time=1778040100,
            )
            record = FeishuScopeRegistry(root).read("tk_1_ou_owner", GROUP_CHAT_SCOPE, "oc_grp1")

        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].ok)
        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record.status, "enabled")
        self.assertTrue(record.last_success_at)

    def test_collect_enabled_scopes_marks_permission_denied(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _enable_group_scope(root, "oc_grp1")
            client = _FakeUserClient([_error_response("permission_denied")])
            collector = GroupMessageCollector(root, client)  # type: ignore[arg-type]

            results = collector.collect_enabled_scopes(
                _FakeConfig(),  # type: ignore[arg-type]
                start_time=1778039900,
                end_time=1778040100,
            )
            record = FeishuScopeRegistry(root).read("tk_1_ou_owner", GROUP_CHAT_SCOPE, "oc_grp1")

        self.assertEqual(len(results), 1)
        self.assertFalse(results[0].ok)
        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record.status, "permission_denied")
        self.assertIn("request failed", record.permission_error)

    def test_items_budget_stops_collection(self) -> None:
        messages = [_text_message(f"om_grp{i}", create_time=f"177804000{i}000") for i in range(3)]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            client = _FakeUserClient([_ok_response(messages)])
            budget = CollectorBudget(COLLECTOR_NAME, max_items_per_run=2)
            collector = GroupMessageCollector(root, client, budget=budget)  # type: ignore[arg-type]

            result = collector.collect("oc_grp1", start_time=1778039900, end_time=1778040100)

        self.assertEqual(result.items_written, 2)
        self.assertEqual(result.stopped_reason, "max_items_per_run")

    def test_source_type_is_group_message(self) -> None:
        self.assertEqual(SOURCE_TYPE, "group_message")

    def test_collector_name_is_group_message_collector(self) -> None:
        self.assertEqual(COLLECTOR_NAME, "group_message_collector")


def _enable_group_scope(root: Path, chat_id: str) -> None:
    """写入并启用一个 group_chat scope 供 collect_enabled_scopes 测试消费。"""
    registry = FeishuScopeRegistry(root)
    record = FeishuScopeRecord(
        account_id="tk_1_ou_owner",
        scope_type=GROUP_CHAT_SCOPE,
        scope_id=chat_id,
        collector_names=(COLLECTOR_NAME,),
        discovered_from="manual_add",
    )
    registry.upsert_candidate(record)
    registry.approve_scope(record.account_id, record.scope_type, record.scope_id)
    registry.enable_scope(record.account_id, record.scope_type, record.scope_id)


if __name__ == "__main__":
    unittest.main()
