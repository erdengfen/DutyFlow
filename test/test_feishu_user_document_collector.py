# 本文件验证飞书 user_document_collector 的 root 发现、文件夹清单落盘、预算和 scope 状态行为。

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
from dutyflow.feishu.collectors.user_document_collector import (  # noqa: E402
    COLLECTOR_NAME,
    SOURCE_TYPE,
    UserDocumentCollector,
)
from dutyflow.feishu.scope_registry import (  # noqa: E402
    DOC_SCOPE,
    DRIVE_FOLDER_SCOPE,
    USER_DOCUMENT_COLLECTOR,
    FeishuScopeRecord,
    FeishuScopeRegistry,
)
from dutyflow.feishu.sync_state import FeishuSyncStateStore  # noqa: E402
from dutyflow.feishu.user_request import FeishuUserResponse  # noqa: E402


class _FakeConfig:
    feishu_tenant_key = "tk_1"
    feishu_owner_open_id = "ou_owner"
    feishu_owner_user_id = "uid_owner"


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
    data: dict[str, Any],
    *,
    has_more: bool = False,
    page_token: str = "",
    raw_path: str = "",
) -> FeishuUserResponse:
    """构造成功 API 响应。"""
    merged = dict(data)
    merged.setdefault("has_more", has_more)
    merged.setdefault("page_token", page_token)
    return FeishuUserResponse(True, "ok", 200, 0, "", merged, page_token, has_more, raw_path)


def _error_response(status: str = "api_error") -> FeishuUserResponse:
    """构造失败 API 响应。"""
    return FeishuUserResponse(False, status, 400, -1, "request failed", {}, "", False, "")


def _file_item(
    token: str = "doxcn_1",
    name: str = "项目计划",
    file_type: str = "docx",
    modified_time: str = "1778040000",
) -> dict[str, Any]:
    """构造飞书云盘文件清单 item。"""
    return {
        "token": token,
        "name": name,
        "type": file_type,
        "url": f"https://example.feishu.cn/docx/{token}",
        "created_time": "1778030000",
        "modified_time": modified_time,
        "owner_id": "ou_1",
    }


class TestUserDocumentCollector(unittest.TestCase):
    """验证 user_document_collector 第一版功能边界。"""

    def test_discover_root_writes_candidate_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            client = _FakeUserClient([
                _ok_response({"token": "fld_root", "id": "id_root", "user_id": "uid_owner"})
            ])
            collector = UserDocumentCollector(root, client)  # type: ignore[arg-type]

            result = collector.discover_root(_FakeConfig())  # type: ignore[arg-type]
            record = FeishuScopeRegistry(root).read("tk_1_ou_owner", DRIVE_FOLDER_SCOPE, "fld_root")

        self.assertTrue(result.ok)
        self.assertEqual(result.root_folder_token, "fld_root")
        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record.status, "candidate")
        self.assertIn(USER_DOCUMENT_COLLECTOR, record.collector_names)
        self.assertEqual(record.discovered_from, "oauth_drive_root")
        self.assertIn("/drive/explorer/v2/root_folder/meta", client.calls[0]["url"])

    def test_collect_folder_writes_ambient_context_and_sync_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw_path = root / "data/feishu/raw/2026-05-07/raw_udc.md"
            client = _FakeUserClient([
                _ok_response({"files": [_file_item()]}, raw_path=str(raw_path))
            ])
            collector = UserDocumentCollector(root, client)  # type: ignore[arg-type]

            result = collector.collect("fld_root", account_id="tk_1_ou_owner")
            detail = (root / result.record_paths[0]).read_text(encoding="utf-8")
            state = FeishuSyncStateStore(root).read(COLLECTOR_NAME, "fld_root")
            params = client.calls[0]["params"]
            child_scope = FeishuScopeRegistry(root).read("tk_1_ou_owner", DOC_SCOPE, "doxcn_1")

        self.assertTrue(result.ok)
        self.assertEqual(result.items_written, 1)
        self.assertEqual(result.candidate_scopes_written, 1)
        self.assertEqual(params["folder_token"], "fld_root")
        self.assertEqual(params["page_size"], 50)
        self.assertEqual(params["order_by"], "EditedTime")
        self.assertEqual(params["direction"], "DESC")
        self.assertIn("user_document", detail)
        self.assertIn("项目计划", detail)
        self.assertNotIn(str(root), detail)
        self.assertEqual(state.cursor, "1778040000")
        self.assertEqual(state.next_cursor, "")
        self.assertIsNotNone(child_scope)

    def test_collect_extracts_doc_link(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            client = _FakeUserClient([_ok_response({"files": [_file_item("doxcn_abc")]})])
            collector = UserDocumentCollector(root, client)  # type: ignore[arg-type]

            result = collector.collect("fld_root", account_id="tk_1_ou_owner")
            detail = (root / result.record_paths[0]).read_text(encoding="utf-8")

        self.assertIn("doxcn_abc", detail)
        self.assertIn("Doc Links", detail)

    def test_collect_uses_page_token_for_next_page(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            client = _FakeUserClient([
                _ok_response({"files": [_file_item("doxcn_1")]}, has_more=True, page_token="p2"),
                _ok_response({"files": [_file_item("doxcn_2", modified_time="1778040001")]}),
            ])
            budget = CollectorBudget(COLLECTOR_NAME, max_pages_per_run=2)
            collector = UserDocumentCollector(root, client, budget=budget)  # type: ignore[arg-type]

            result = collector.collect("fld_root", account_id="tk_1_ou_owner")
            second_params = client.calls[1]["params"]

        self.assertEqual(result.items_written, 2)
        self.assertEqual(second_params["page_token"], "p2")

    def test_stops_at_max_pages_and_stores_next_cursor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            client = _FakeUserClient([
                _ok_response({"files": [_file_item("doxcn_1")]}, has_more=True, page_token="p2"),
                _ok_response({"files": [_file_item("doxcn_2")]}, has_more=True, page_token="p3"),
            ])
            budget = CollectorBudget(COLLECTOR_NAME, max_pages_per_run=2)
            collector = UserDocumentCollector(root, client, budget=budget)  # type: ignore[arg-type]

            result = collector.collect("fld_root", account_id="tk_1_ou_owner")
            state = FeishuSyncStateStore(root).read(COLLECTOR_NAME, "fld_root")

        self.assertEqual(len(client.calls), 2)
        self.assertTrue(result.has_more)
        self.assertEqual(result.next_cursor, "p3")
        self.assertEqual(state.next_cursor, "p3")
        self.assertEqual(result.stopped_reason, "max_pages_per_run")

    def test_failure_response_marks_sync_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            client = _FakeUserClient([_error_response("permission_denied")])
            collector = UserDocumentCollector(root, client)  # type: ignore[arg-type]

            result = collector.collect("fld_root", account_id="tk_1_ou_owner")
            state = FeishuSyncStateStore(root).read(COLLECTOR_NAME, "fld_root")

        self.assertFalse(result.ok)
        self.assertEqual(result.status, "permission_denied")
        self.assertEqual(result.items_written, 0)
        self.assertEqual(state.last_error_kind, "permission_denied")

    def test_items_budget_stops_collection_without_extra_candidate(self) -> None:
        files = [_file_item(f"doxcn_{index}") for index in range(3)]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            client = _FakeUserClient([_ok_response({"files": files})])
            budget = CollectorBudget(COLLECTOR_NAME, max_items_per_run=2)
            collector = UserDocumentCollector(root, client, budget=budget)  # type: ignore[arg-type]

            result = collector.collect("fld_root", account_id="tk_1_ou_owner")
            scopes = FeishuScopeRegistry(root).list_records(account_id="tk_1_ou_owner")

        self.assertEqual(result.items_written, 2)
        self.assertEqual(result.candidate_scopes_written, 2)
        self.assertEqual(result.stopped_reason, "max_items_per_run")
        self.assertNotIn("doxcn_2", {scope.scope_id for scope in scopes})

    def test_collect_enabled_scopes_marks_registry_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _enable_folder_scope(root, "fld_root")
            client = _FakeUserClient([_ok_response({"files": [_file_item()]})])
            collector = UserDocumentCollector(root, client)  # type: ignore[arg-type]

            results = collector.collect_enabled_scopes(_FakeConfig())  # type: ignore[arg-type]
            record = FeishuScopeRegistry(root).read("tk_1_ou_owner", DRIVE_FOLDER_SCOPE, "fld_root")

        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].ok)
        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record.status, "enabled")
        self.assertTrue(record.last_success_at)

    def test_collect_enabled_scopes_marks_permission_denied(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _enable_folder_scope(root, "fld_root")
            client = _FakeUserClient([_error_response("permission_denied")])
            collector = UserDocumentCollector(root, client)  # type: ignore[arg-type]

            results = collector.collect_enabled_scopes(_FakeConfig())  # type: ignore[arg-type]
            record = FeishuScopeRegistry(root).read("tk_1_ou_owner", DRIVE_FOLDER_SCOPE, "fld_root")

        self.assertEqual(len(results), 1)
        self.assertFalse(results[0].ok)
        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record.status, "permission_denied")
        self.assertIn("request failed", record.permission_error)

    def test_collect_enabled_doc_scope_writes_metadata_without_api_request(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry = FeishuScopeRegistry(root)
            record = FeishuScopeRecord(
                account_id="tk_1_ou_owner",
                scope_type=DOC_SCOPE,
                scope_id="doxcn_direct",
                collector_names=(COLLECTOR_NAME,),
                discovered_from="manual_add",
                source_url="https://example.feishu.cn/docx/doxcn_direct",
            )
            registry.upsert_candidate(record)
            registry.approve_scope(record.account_id, record.scope_type, record.scope_id)
            registry.enable_scope(record.account_id, record.scope_type, record.scope_id)
            client = _FakeUserClient([])
            collector = UserDocumentCollector(root, client)  # type: ignore[arg-type]

            results = collector.collect_enabled_scopes(_FakeConfig())  # type: ignore[arg-type]
            detail = (root / results[0].record_paths[0]).read_text(encoding="utf-8")

        self.assertEqual(len(client.calls), 0)
        self.assertEqual(results[0].items_written, 1)
        self.assertIn("doxcn_direct", detail)

    def test_source_type_and_collector_name_are_stable(self) -> None:
        self.assertEqual(SOURCE_TYPE, "user_document")
        self.assertEqual(COLLECTOR_NAME, "user_document_collector")


def _enable_folder_scope(root: Path, folder_token: str) -> None:
    """写入并启用一个 drive_folder scope 供 collect_enabled_scopes 测试消费。"""
    registry = FeishuScopeRegistry(root)
    record = FeishuScopeRecord(
        account_id="tk_1_ou_owner",
        scope_type=DRIVE_FOLDER_SCOPE,
        scope_id=folder_token,
        collector_names=(COLLECTOR_NAME,),
        discovered_from="manual_add",
    )
    registry.upsert_candidate(record)
    registry.approve_scope(record.account_id, record.scope_type, record.scope_id)
    registry.enable_scope(record.account_id, record.scope_type, record.scope_id)


if __name__ == "__main__":
    unittest.main()
