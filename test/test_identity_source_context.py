# 本文件验证联系人解析器与来源解析器的核心匹配行为。

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dutyflow.identity.contact_resolver import ContactResolver  # noqa: E402
from dutyflow.identity.source_context import SourceContextResolver  # noqa: E402
from identity_fixture_data import write_identity_fixtures  # noqa: E402


class TestIdentitySourceContext(unittest.TestCase):
    """验证身份与来源解析层。"""

    def test_contact_resolver_supports_strong_and_scoped_match(self) -> None:
        """联系人解析应支持飞书 ID 和姓名加部门的稳定匹配。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_identity_fixtures(root)
            resolver = ContactResolver(root)
            by_feishu = resolver.resolve_contact({"feishu_user_id": "ou_001"})
            by_name = resolver.resolve_contact({"name": "张三", "department": "产品部"})
        self.assertEqual(by_feishu["match_status"], "unique")
        self.assertEqual(by_feishu["contact_id"], "contact_001")
        self.assertEqual(by_name["matched_by"], "name+department")
        self.assertIn("relationship_to_user=manager", by_name["context_snippet"])

    def test_contact_resolver_returns_ambiguous_for_name_only(self) -> None:
        """仅按重名姓名查询时必须返回 ambiguous。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_identity_fixtures(root)
            payload = ContactResolver(root).resolve_contact({"name": "张三"})
        self.assertEqual(payload["match_status"], "ambiguous")
        self.assertEqual(len(payload["ambiguous_candidates"]), 2)

    def test_source_context_resolver_supports_strong_and_scoped_match(self) -> None:
        """来源解析应支持 source_id 与类型加显示名匹配。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_identity_fixtures(root)
            resolver = SourceContextResolver(root)
            by_id = resolver.resolve_source({"source_id": "source_chat_001"})
            by_scope = resolver.resolve_source({"source_type": "chat", "display_name": "核心项目群"})
        self.assertEqual(by_id["match_status"], "unique")
        self.assertEqual(by_id["owner_contact_id"], "contact_001")
        self.assertEqual(by_scope["source_type"], "chat")
        self.assertIn("default_weight=high", by_scope["context_snippet"])


def _self_test() -> None:
    """运行本文件单元测试。"""
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestIdentitySourceContext)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    _self_test()
