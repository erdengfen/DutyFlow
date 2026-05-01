# 本文件验证 Runtime Context Evidence Store 的显式外置能力。

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import sys
import tempfile
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dutyflow.agent.tools.types import ToolResultEnvelope  # noqa: E402
from dutyflow.context.evidence_store import EvidenceStore  # noqa: E402
from dutyflow.storage.file_store import FileStore  # noqa: E402
from dutyflow.storage.markdown_store import MarkdownStore  # noqa: E402


class TestContextEvidenceStore(unittest.TestCase):
    """验证 Evidence Store 只处理显式写入的长内容。"""

    def test_save_content_writes_markdown_and_ref(self) -> None:
        """显式保存内容后应写入标准 Evidence Markdown。"""
        content = "# Report\n\n## Inner Heading\n\nlong body"
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = EvidenceStore(root)
            record = store.save_content(
                source_type="manual",
                source_id="manual_001",
                content=content,
                summary="人工证据摘要",
                evidence_id="evid_manual_001",
                content_format="markdown",
            )
            loaded = store.read_evidence("evid_manual_001")
            document = MarkdownStore(FileStore(root)).read_document(record.path)
        self.assertEqual(record.to_ref(), "evidence:data/contexts/evidence/evid_manual_001.md")
        self.assertEqual(document.frontmatter["schema"], "dutyflow.context_evidence.v1")
        self.assertEqual(document.frontmatter["source_type"], "manual")
        self.assertEqual(document.frontmatter["content_size"], str(len(content)))
        self.assertEqual(document.frontmatter["content_sha256"], sha256(content.encode("utf-8")).hexdigest())
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.content, content)
        self.assertEqual(loaded.summary, "人工证据摘要")

    def test_save_tool_result_preserves_tool_anchors(self) -> None:
        """工具结果外置时应保留 tool_use_id、tool_name 和任务事件锚点。"""
        result = ToolResultEnvelope(
            "tool_abc",
            "lookup_contact_identity",
            True,
            '{"name":"张三","details":"' + ("x" * 900) + '"}',
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            store = EvidenceStore(Path(temp_dir))
            record = store.save_tool_result(
                result,
                summary="联系人查询长结果。",
                evidence_id="evid_tool_abc",
                task_id="task_001",
                event_id="evt_001",
            )
            loaded = store.read_evidence("evid_tool_abc")
        self.assertEqual(record.source_type, "tool_result")
        self.assertEqual(record.source_id, "tool_abc")
        self.assertEqual(record.tool_use_id, "tool_abc")
        self.assertEqual(record.tool_name, "lookup_contact_identity")
        self.assertEqual(record.task_id, "task_001")
        self.assertEqual(record.event_id, "evt_001")
        self.assertEqual(record.content_format, "json")
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.tool_use_id, "tool_abc")
        self.assertIn('"name":"张三"', loaded.content)

    def test_list_evidence_does_not_scan_perception_records(self) -> None:
        """Evidence Store 只能枚举 evidence 目录，不应主动索引感知文件。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            perception_path = root / "data" / "perception" / "2026-05-01" / "per_001.md"
            perception_path.parent.mkdir(parents=True)
            perception_path.write_text("---\nschema: demo\n---\n\n# Perception", encoding="utf-8")
            records = EvidenceStore(root).list_evidence()
        self.assertEqual(records, ())

    def test_read_preserves_content_with_markdown_headings(self) -> None:
        """Content 区域应通过 marker 读取，不受原文二级标题影响。"""
        content = "line 1\n\n## This heading belongs to content\n\nline 2"
        with tempfile.TemporaryDirectory() as temp_dir:
            store = EvidenceStore(Path(temp_dir))
            store.save_content(
                source_type="observation",
                source_id="obs_001",
                content=content,
                evidence_id="evid_obs_001",
                content_format="markdown",
            )
            loaded = store.read_evidence("evid_obs_001")
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.content, content)

    def test_invalid_inputs_are_rejected(self) -> None:
        """非法来源、ID 和内部 marker 应被拒绝，避免不可追踪记录。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            store = EvidenceStore(Path(temp_dir))
            with self.assertRaises(ValueError):
                store.save_content(source_type="unknown", source_id="x", content="x")
            with self.assertRaises(ValueError):
                store.save_content(source_type="manual", source_id="x", content="x", evidence_id="../bad")
            with self.assertRaises(ValueError):
                store.save_content(
                    source_type="manual",
                    source_id="x",
                    content="<!-- dutyflow:evidence-content:start -->",
                )


def _self_test() -> None:
    """运行本文件单元测试。"""
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestContextEvidenceStore)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    _self_test()
