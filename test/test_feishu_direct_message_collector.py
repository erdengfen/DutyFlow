# 本文件验证飞书 direct_message_collector 的请求参数、预算、落盘和 sync_state 行为。

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dutyflow.feishu.collector_budget import CollectorBudget  # noqa: E402
from dutyflow.feishu.collectors.direct_message_collector import (  # noqa: E402
    COLLECTOR_NAME,
    DirectMessageCollector,
)
from dutyflow.feishu.sync_state import FeishuSyncStateStore  # noqa: E402
from dutyflow.feishu.user_request import FeishuUserResponse  # noqa: E402


class _FakeUserClient:
    """测试替身：模拟 FeishuUserClient.get 并记录请求参数。"""

    def __init__(self, responses: list[FeishuUserResponse]) -> None:
        """绑定预置响应序列。"""
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, **kwargs: Any) -> FeishuUserResponse:
        """记录请求并返回下一条预置响应。"""
        self.calls.append({"url": url, **kwargs})
        if not self.responses:
            raise AssertionError("unexpected extra request")
        return self.responses.pop(0)


class TestDirectMessageCollector(unittest.TestCase):
    """验证 direct_message_collector 第一版功能边界。"""

    def test_collect_text_message_writes_ambient_context_and_sync_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            client = _FakeUserClient([_ok_response([_text_message()])])
            collector = DirectMessageCollector(root, client)  # type: ignore[arg-type]

            result = collector.collect("oc_1", start_time=1778039900, end_time=1778040100)
            detail = (root / result.record_paths[0]).read_text(encoding="utf-8")
            state = FeishuSyncStateStore(root).read(COLLECTOR_NAME, "oc_1")
            params = client.calls[0]["params"]

        self.assertTrue(result.ok)
        self.assertEqual(result.items_written, 1)
        self.assertEqual(params["container_id"], "oc_1")
        self.assertEqual(params["container_id_type"], "chat")
        self.assertEqual(params["sort_type"], "ByCreateTimeAsc")
        self.assertEqual(params["page_size"], 50)
        self.assertIn("om_1", detail)
        self.assertIn("token_1", detail)
        self.assertEqual(state.cursor, "1778040000000")
        self.assertEqual(state.next_cursor, "1778040000")

    def test_collect_uses_page_token_for_next_page(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            client = _FakeUserClient(
                [
                    _ok_response([_text_message("om_1")], has_more=True, page_token="p2"),
                    _ok_response([_text_message("om_2", create_time="1778040001000")]),
                ]
            )
            budget = CollectorBudget(COLLECTOR_NAME, max_pages_per_run=2)
            collector = DirectMessageCollector(root, client, budget=budget)  # type: ignore[arg-type]

            result = collector.collect("oc_1", start_time=1778039900, end_time=1778040100)

        self.assertTrue(result.ok)
        self.assertEqual(result.items_written, 2)
        self.assertEqual(client.calls[1]["params"]["page_token"], "p2")
        self.assertEqual(result.cursor, "1778040001000")

    def test_item_budget_stops_without_requesting_next_page(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            client = _FakeUserClient(
                [_ok_response([_text_message("om_1"), _text_message("om_2")], has_more=True, page_token="p2")]
            )
            budget = CollectorBudget(COLLECTOR_NAME, max_items_per_run=1)
            collector = DirectMessageCollector(root, client, budget=budget)  # type: ignore[arg-type]

            result = collector.collect("oc_1", start_time=1778039900, end_time=1778040100)

        self.assertTrue(result.ok)
        self.assertEqual(result.items_written, 1)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(result.stopped_reason, "max_items_per_run")
        self.assertEqual(result.next_page_token, "p2")

    def test_file_message_writes_file_clue_without_binary_fetch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            client = _FakeUserClient([_ok_response([_file_message()])])
            collector = DirectMessageCollector(root, client)  # type: ignore[arg-type]

            result = collector.collect("oc_1", start_time=1778039900, end_time=1778040100)
            detail = (root / result.record_paths[0]).read_text(encoding="utf-8")

        self.assertTrue(result.ok)
        self.assertIn("file_key_1", detail)
        self.assertIn("demo.txt", detail)
        self.assertNotIn("binary", detail.lower())

    def test_permission_denied_marks_failure_without_ambient_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            client = _FakeUserClient([_permission_response()])
            collector = DirectMessageCollector(root, client)  # type: ignore[arg-type]

            result = collector.collect("oc_1", start_time=1778039900, end_time=1778040100)
            state = FeishuSyncStateStore(root).read(COLLECTOR_NAME, "oc_1")

        self.assertFalse(result.ok)
        self.assertEqual(result.status, "permission_denied")
        self.assertEqual(result.items_written, 0)
        self.assertEqual(state.last_error_kind, "permission_denied")
        self.assertFalse((root / "data/ambient_context").exists())

    def test_empty_success_preserves_existing_cursor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            FeishuSyncStateStore(root).mark_success(
                COLLECTOR_NAME,
                "oc_1",
                cursor="1778040000000",
                next_cursor="1778040000",
                surface_type="direct_message",
            )
            client = _FakeUserClient([_ok_response([])])
            collector = DirectMessageCollector(root, client)  # type: ignore[arg-type]

            result = collector.collect("oc_1", start_time=1778040000, end_time=1778040100)

        self.assertTrue(result.ok)
        self.assertEqual(result.items_written, 0)
        self.assertEqual(result.cursor, "1778040000000")
        self.assertEqual(result.next_cursor, "1778040000")


def _ok_response(
    items: list[dict[str, Any]],
    *,
    has_more: bool = False,
    page_token: str = "",
) -> FeishuUserResponse:
    """构造成功的飞书用户面响应。"""
    return FeishuUserResponse(
        ok=True,
        status="ok",
        http_status=200,
        feishu_code=0,
        detail="",
        data={"items": items, "has_more": has_more, "page_token": page_token},
        page_token=page_token,
        has_more=has_more,
        raw_path="data/feishu/raw/2026-05-06/raw_dmc.md",
    )


def _permission_response() -> FeishuUserResponse:
    """构造权限不足响应。"""
    return FeishuUserResponse(
        ok=False,
        status="permission_denied",
        http_status=403,
        feishu_code=99991672,
        detail="forbidden",
        data={},
        page_token="",
        has_more=False,
        raw_path="",
    )


def _text_message(
    message_id: str = "om_1",
    *,
    create_time: str = "1778040000000",
) -> dict[str, Any]:
    """构造 text 私信消息。"""
    return {
        "message_id": message_id,
        "root_id": "",
        "parent_id": "",
        "create_time": create_time,
        "update_time": create_time,
        "chat_id": "oc_1",
        "sender": {"id": "ou_1", "id_type": "open_id"},
        "body": {"content": json.dumps({"text": "请看 https://example.feishu.cn/docx/token_1"})},
        "msg_type": "text",
    }


def _file_message() -> dict[str, Any]:
    """构造 file 私信消息。"""
    return {
        "message_id": "om_file_1",
        "create_time": "1778040000000",
        "update_time": "1778040000000",
        "chat_id": "oc_1",
        "sender": {"id": "ou_1", "id_type": "open_id"},
        "body": {"content": json.dumps({"file_key": "file_key_1", "file_name": "demo.txt"})},
        "msg_type": "file",
    }


def _self_test() -> None:
    """运行本文件所有单元测试。"""
    unittest.main(verbosity=2)


if __name__ == "__main__":
    _self_test()
