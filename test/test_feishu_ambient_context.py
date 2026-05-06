# 本文件验证飞书用户面 ambient_context 统一 Markdown 落盘和索引维护。

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dutyflow.feishu.ambient_context import (  # noqa: E402
    AmbientContextRecord,
    AmbientContextStore,
    AmbientDocLink,
    AmbientFileClue,
)
from dutyflow.storage.file_store import FileStore  # noqa: E402
from dutyflow.storage.markdown_store import MarkdownStore  # noqa: E402


class TestAmbientContextStore(unittest.TestCase):
    """验证主动感知记录和索引的基础落盘行为。"""

    def test_write_record_creates_detail_and_indexes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = AmbientContextStore(root)

            result = store.write(_record())
            detail = result.path.read_text(encoding="utf-8")
            source_index = result.source_index_path.read_text(encoding="utf-8")
            global_index = result.global_index_path.read_text(encoding="utf-8")

        self.assertTrue(result.path.match("*/data/ambient_context/direct_message/2026-05-06/dm_om_1.md"))
        self.assertIn("schema: dutyflow.ambient_context.v1", detail)
        self.assertIn("token_1", detail)
        self.assertIn("file_key_1", detail)
        self.assertIn("dm_om_1", source_index)
        self.assertIn("dm_om_1", global_index)

    def test_rewrite_record_deduplicates_index_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = AmbientContextStore(root)

            store.write(_record(text="first"))
            store.write(_record(text="second"))
            source_index = (root / "data/ambient_context/direct_message/index.md").read_text(encoding="utf-8")

        self.assertEqual(source_index.count("| dm_om_1 |"), 1)
        self.assertIn("second", source_index)

    def test_dash_prefixed_preview_is_valid_frontmatter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = AmbientContextStore(root)

            result = store.write(_record(text="- needs review"))
            document = MarkdownStore(FileStore(root)).read_document(result.path)

        self.assertEqual(document.frontmatter["text_preview"], "- needs review")

    def test_project_absolute_refs_are_written_as_relative_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = AmbientContextStore(root)
            raw_path = root / "data/feishu/raw/2026-05-06/raw_1.md"

            result = store.write(_record(raw_message_ref=str(raw_path)))
            detail = result.path.read_text(encoding="utf-8")
            document = MarkdownStore(FileStore(root)).read_document(result.path)

        self.assertEqual(document.frontmatter["raw_message_ref"], "data/feishu/raw/2026-05-06/raw_1.md")
        self.assertIn("- raw_message_ref: data/feishu/raw/2026-05-06/raw_1.md", detail)
        self.assertNotIn(str(root), detail)


def _record(
    text: str = "见文档 https://example.feishu.cn/docx/token_1",
    *,
    raw_message_ref: str = "data/feishu/raw/2026-05-06/raw_1.md",
) -> AmbientContextRecord:
    """构造测试用 ambient_context 记录。"""
    return AmbientContextRecord(
        record_id="dm_om_1",
        source_type="direct_message",
        collector_name="direct_message_collector",
        source_id="oc_1",
        sync_scope_id="oc_1",
        created_at="2026-05-06T12:00:00+08:00",
        fetched_at="2026-05-06T12:01:00+08:00",
        text=text,
        text_preview=text,
        raw_message_ref=raw_message_ref,
        sync_state_ref="data/feishu/sync_state/direct_message_collector/oc_1.md",
        doc_links=(AmbientDocLink("https://example.feishu.cn/docx/token_1", "docx", "token_1"),),
        file_clues=(AmbientFileClue("om_1", "file", "file_key_1", "demo.txt"),),
    )


def _self_test() -> None:
    """运行本文件所有单元测试。"""
    unittest.main(verbosity=2)


if __name__ == "__main__":
    _self_test()
