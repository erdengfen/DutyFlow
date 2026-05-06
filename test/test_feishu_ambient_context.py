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
    AmbientContextScanQuery,
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

    def test_read_by_record_id_restores_detail_record(self) -> None:
        """按 record_id 读回应恢复正文、链接、附件和引用字段。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = AmbientContextStore(root)
            store.write(_record())

            loaded = store.read_by_record_id("dm_om_1")

        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.record_id, "dm_om_1")
        self.assertIn("token_1", loaded.text)
        self.assertEqual(loaded.doc_links[0].token, "token_1")
        self.assertEqual(loaded.file_clues[0].file_key, "file_key_1")
        self.assertEqual(loaded.sync_state_ref, "data/feishu/sync_state/direct_message_collector/oc_1.md")

    def test_scan_records_filters_by_source_collector_time_and_ids(self) -> None:
        """扫描接口应按 source_type、collector_name、created_at 和 record_id 过滤。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = AmbientContextStore(Path(tmp))
            store.write(_record(record_id="dm_old", created_at="2026-05-06T09:00:00+08:00"))
            store.write(_record(record_id="dm_new", created_at="2026-05-06T11:00:00+08:00"))
            store.write(
                _record(
                    record_id="gm_new",
                    source_type="group_message",
                    collector_name="group_message_collector",
                    created_at="2026-05-06T11:30:00+08:00",
                )
            )

            records = store.scan_records(
                AmbientContextScanQuery(
                    source_type="direct_message",
                    collector_name="direct_message_collector",
                    created_after="2026-05-06T10:00:00+08:00",
                    record_ids=("dm_new", "gm_new"),
                )
            )

        self.assertEqual(tuple(record.record_id for record in records), ("dm_new",))

    def test_scan_records_respects_limit(self) -> None:
        """扫描接口应遵守 context packet 记录数预算。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = AmbientContextStore(Path(tmp))
            store.write(_record(record_id="dm_1", created_at="2026-05-06T09:00:00+08:00"))
            store.write(_record(record_id="dm_2", created_at="2026-05-06T10:00:00+08:00"))
            store.write(_record(record_id="dm_3", created_at="2026-05-06T11:00:00+08:00"))

            limited = store.scan_records(AmbientContextScanQuery(source_type="direct_message", limit=2))
            empty = store.scan_records(AmbientContextScanQuery(source_type="direct_message", limit=0))

        self.assertEqual(tuple(record.record_id for record in limited), ("dm_1", "dm_2"))
        self.assertEqual(empty, ())

    def test_build_context_packet_returns_stable_summary(self) -> None:
        """context packet 应包含稳定 packet_id、scope_ids、时间窗口和记录摘要。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = AmbientContextStore(Path(tmp))
            store.write(_record(record_id="dm_a", created_at="2026-05-06T09:00:00+08:00", sync_scope_id="oc_a"))
            store.write(_record(record_id="dm_b", created_at="2026-05-06T10:00:00+08:00", sync_scope_id="oc_b"))

            packet = store.build_context_packet(AmbientContextScanQuery(source_type="direct_message"))
            payload = packet.to_payload()

        self.assertTrue(packet.packet_id.startswith("ambpkt_"))
        self.assertEqual(packet.record_ids, ("dm_a", "dm_b"))
        self.assertEqual(packet.scope_ids, ("oc_a", "oc_b"))
        self.assertEqual(payload["time_window"]["start"], "2026-05-06T09:00:00+08:00")
        self.assertEqual(payload["record_count"], 2)
        self.assertEqual(payload["records"][0]["detail_file"], "data/ambient_context/direct_message/2026-05-06/dm_a.md")


def _record(
    text: str = "见文档 https://example.feishu.cn/docx/token_1",
    *,
    record_id: str = "dm_om_1",
    source_type: str = "direct_message",
    collector_name: str = "direct_message_collector",
    created_at: str = "2026-05-06T12:00:00+08:00",
    sync_scope_id: str = "oc_1",
    raw_message_ref: str = "data/feishu/raw/2026-05-06/raw_1.md",
) -> AmbientContextRecord:
    """构造测试用 ambient_context 记录。"""
    return AmbientContextRecord(
        record_id=record_id,
        source_type=source_type,
        collector_name=collector_name,
        source_id="oc_1",
        sync_scope_id=sync_scope_id,
        created_at=created_at,
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
