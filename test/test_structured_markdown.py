# 本文件验证结构化 Markdown 解析层的扫描、详情读取和受控更新。

from pathlib import Path
import sys
import tempfile
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dutyflow.storage.structured_markdown import (  # noqa: E402
    FrontmatterParser,
    RecordLocator,
    SchemaRegistry,
    SectionExtractor,
    SnippetBuilder,
    StructuredRecordUpdater,
    _now_iso,
)


class TestStructuredMarkdown(unittest.TestCase):
    """验证结构化 Markdown 解析层。"""

    def test_locator_lists_contact_knowledge_records_from_directory(self) -> None:
        """无索引时应能按固定目录扫描联系人知识记录。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = _write_contact_note(root, "contact_001", "ckn_001", "async review")
            parser = FrontmatterParser(root)
            locator = RecordLocator(root, SchemaRegistry(), parser)
            records = locator.list_records("contact_knowledge")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].record_id, "ckn_001")
        self.assertEqual(records[0].path, path)

    def test_section_extractor_and_snippet_builder_return_lightweight_fields(self) -> None:
        """提取器和拼装器应只返回允许 section。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_contact_note(root, "contact_001", "ckn_001", "async review")
            parser = FrontmatterParser(root)
            locator = RecordLocator(root, SchemaRegistry(), parser)
            record = locator.find_by_id("contact_knowledge", "ckn_001")
            extractor = SectionExtractor()
            builder = SnippetBuilder()
            sections = extractor.extract(record, ("Summary", "Decision Value"))  # type: ignore[arg-type]
            detail = builder.build_detail(
                record,  # type: ignore[arg-type]
                id_key="id",
                section_names=("Summary", "Decision Value"),
                root=root,
            )
        self.assertEqual(sections["Summary"], "async review")
        self.assertIn("decision_value", detail)
        self.assertEqual(detail["source_file"], "data/knowledge/contacts/contact_001/ckn_001.md")

    def test_updater_creates_and_updates_contact_knowledge_record(self) -> None:
        """更新器应能创建记录并追加 Change Log。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            registry = SchemaRegistry()
            parser = FrontmatterParser(root)
            locator = RecordLocator(root, registry, parser)
            updater = StructuredRecordUpdater(root, registry, parser, locator)
            created = updater.create_record(
                "contact_knowledge",
                record_id="ckn_001",
                frontmatter={
                    "schema": "dutyflow.contact_knowledge_note.v1",
                    "id": "ckn_001",
                    "contact_id": "contact_001",
                    "topic": "working_preference",
                    "keywords": "async, review",
                    "confidence": "medium",
                    "status": "active",
                    "source_refs": "evt_001",
                    "created_at": _now_iso(),
                    "updated_at": _now_iso(),
                },
                sections={"Summary": "async review"},
            )
            updated = updater.update_record(
                "contact_knowledge",
                record_id="ckn_001",
                frontmatter_updates={"status": "inactive", "updated_at": _now_iso()},
                section_updates={"Decision Value": "use async first"},
                change_note="manually revised",
            )
        self.assertEqual(created.record_id, "ckn_001")
        self.assertEqual(updated.frontmatter["status"], "inactive")
        self.assertIn("manually revised", updated.sections["Change Log"])
        self.assertEqual(updated.sections["Decision Value"], "use async first")


def _write_contact_note(root: Path, contact_id: str, note_id: str, summary: str) -> Path:
    """写入联系人知识测试文件。"""
    path = root / "data" / "knowledge" / "contacts" / contact_id / f"{note_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        (
            "---\n"
            "schema: dutyflow.contact_knowledge_note.v1\n"
            f"id: {note_id}\n"
            f"contact_id: {contact_id}\n"
            "topic: working_preference\n"
            "keywords: async, review\n"
            "confidence: medium\n"
            "status: active\n"
            "source_refs: evt_001\n"
            f"created_at: {_now_iso()}\n"
            f"updated_at: {_now_iso()}\n"
            "---\n\n"
            f"# Contact Knowledge {note_id}\n\n"
            "## Summary\n\n"
            f"{summary}\n\n"
            "## Structured Facts\n\n"
            "| fact_key | fact_value | confidence | source_ref |\n"
            "|---|---|---|---|\n"
            "| review_style | async first | medium | evt_001 |\n\n"
            "## Decision Value\n\n"
            "prefer async before meetings\n\n"
            "## Change Log\n\n"
            "| at | action | note |\n"
            "|---|---|---|\n"
            f"| {_now_iso()} | created | initial |\n"
        ),
        encoding="utf-8",
    )
    return path


def _self_test() -> None:
    """运行本文件单元测试。"""
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestStructuredMarkdown)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    _self_test()
