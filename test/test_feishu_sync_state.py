# 本文件验证飞书用户面 collector sync_state 的 Markdown 落盘和续跑状态读取。

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dutyflow.feishu.sync_state import FeishuSyncStateStore  # noqa: E402


class TestFeishuSyncStateStore(unittest.TestCase):
    """验证 sync_state 最小接口的状态读写行为。"""

    def test_missing_state_returns_initial_empty_cursor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = FeishuSyncStateStore(Path(tmp))

            state = store.read("user_docs", "root", "user_document")

        self.assertEqual(state.collector_name, "user_docs")
        self.assertEqual(state.surface_type, "user_document")
        self.assertEqual(state.scope_id, "root")
        self.assertEqual(state.cursor, "")
        self.assertEqual(state.next_cursor, "")
        self.assertEqual(state.last_success_at, "")

    def test_mark_success_writes_cursor_and_next_cursor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = FeishuSyncStateStore(root)

            written = store.mark_success(
                "user_docs",
                "root",
                cursor="cursor_1",
                next_cursor="cursor_2",
                surface_type="user_document",
            )
            read_back = store.read("user_docs", "root")
            state_path = store.path_for("user_docs", "root")
            content = state_path.read_text(encoding="utf-8")

        self.assertEqual(written.cursor, "cursor_1")
        self.assertEqual(read_back.cursor, "cursor_1")
        self.assertEqual(read_back.next_cursor, "cursor_2")
        self.assertIn("last_success_at", content)
        self.assertIn("cursor_1", content)
        self.assertIn("cursor_2", content)

    def test_mark_failure_writes_error_and_preserves_cursor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = FeishuSyncStateStore(Path(tmp))
            store.mark_success("user_group", "chat_1", "cur_ok", "cur_next")

            failed = store.mark_failure(
                "user_group",
                "chat_1",
                error_kind="timeout",
                error_detail="request timeout",
            )

        self.assertEqual(failed.cursor, "cur_ok")
        self.assertEqual(failed.next_cursor, "cur_next")
        self.assertEqual(failed.last_error_kind, "timeout")
        self.assertEqual(failed.last_error_detail, "request timeout")
        self.assertIn("T", failed.last_failure_at)

    def test_next_cursor_prefers_next_cursor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = FeishuSyncStateStore(Path(tmp))
            store.mark_success("user_docs", "root", "cursor_1", "cursor_2")

            cursor = store.next_cursor("user_docs", "root")

        self.assertEqual(cursor, "cursor_2")

    def test_next_cursor_falls_back_to_cursor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = FeishuSyncStateStore(Path(tmp))
            store.mark_success("user_docs", "root", "cursor_1", "")

            cursor = store.next_cursor("user_docs", "root")

        self.assertEqual(cursor, "cursor_1")

    def test_scope_id_is_safely_converted_to_filename(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            store = FeishuSyncStateStore(root)
            escape_target = root.parent / f"{root.name}_outside"
            unsafe_scope = f"../../{escape_target.name}/scope"

            store.mark_success("group_docs", unsafe_scope, "cur_1", "")
            path = store.path_for("group_docs", unsafe_scope)

            self.assertTrue(path.is_relative_to(root))
            self.assertNotIn("..", path.name)
            self.assertNotIn("/", path.name)
            self.assertFalse(escape_target.exists())

    def test_frontmatter_handles_dash_prefixed_error_detail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = FeishuSyncStateStore(Path(tmp))

            store.mark_failure(
                "meeting_minutes",
                "scope_1",
                error_kind="api_error",
                error_detail="- forbidden",
            )
            state = store.read("meeting_minutes", "scope_1")

        self.assertEqual(state.last_error_detail, "- forbidden")


def _self_test() -> None:
    """运行本文件所有单元测试。"""
    unittest.main(verbosity=2)


if __name__ == "__main__":
    _self_test()
