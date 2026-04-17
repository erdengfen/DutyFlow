# 本文件验证 Markdown 存储的 frontmatter 约束和章节抽取。

from pathlib import Path
import sys
import tempfile
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dutyflow.storage.file_store import FileStore
from dutyflow.storage.markdown_store import MarkdownDocument, MarkdownStore


class TestMarkdownStore(unittest.TestCase):
    """验证 MarkdownStore 的基础行为。"""

    def test_write_read_and_extract_section(self) -> None:
        """写入后应能读取 frontmatter 并抽取指定章节。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MarkdownStore(FileStore(Path(temp_dir)))
            doc = MarkdownDocument({"schema": "demo.v1"}, "# Demo\n\n## Details\n\nbody\n")
            store.write_document("data/demo.md", doc)
            loaded = store.read_document("data/demo.md")
            section = store.extract_section("data/demo.md", "Details")
        self.assertEqual(loaded.frontmatter["schema"], "demo.v1")
        self.assertEqual(section, "body")

    def test_complex_frontmatter_is_rejected(self) -> None:
        """复杂列表 frontmatter 应被拒绝。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MarkdownStore(FileStore(Path(temp_dir)))
            doc = MarkdownDocument({"ids": "[a, b]"}, "# Demo\n")
            with self.assertRaises(ValueError):
                store.write_document("data/demo.md", doc)

    def test_rewrite_does_not_grow_frontmatter_blank_lines(self) -> None:
        """重复读取写入时不应在 frontmatter 后累积空行。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MarkdownStore(FileStore(Path(temp_dir)))
            doc = MarkdownDocument({"schema": "demo.v1"}, "# Demo\n")
            store.write_document("data/demo.md", doc)
            store.write_document("data/demo.md", store.read_document("data/demo.md"))
            content = (Path(temp_dir) / "data/demo.md").read_text(encoding="utf-8")
        self.assertIn("---\n\n# Demo", content)
        self.assertNotIn("---\n\n\n# Demo", content)


def _self_test() -> None:
    """运行本文件单元测试。"""
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestMarkdownStore)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    _self_test()
