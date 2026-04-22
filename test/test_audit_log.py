# 本文件验证按日 Markdown 审计日志。

from pathlib import Path
import sys
import tempfile
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dutyflow.logging.audit_log import AuditLogger
from dutyflow.storage.file_store import FileStore
from dutyflow.storage.markdown_store import MarkdownStore


class TestAuditLog(unittest.TestCase):
    """验证 AuditLogger 的基础行为。"""

    def test_record_writes_daily_markdown_log(self) -> None:
        """记录日志时应创建按日 Markdown 文件。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            markdown = MarkdownStore(FileStore(root))
            logger = AuditLogger(markdown, Path("data/logs"))
            path = logger.record("test_event", "hello api_key=secret")
            content = path.read_text(encoding="utf-8")
        self.assertIn("test_event", content)
        self.assertNotIn("api_key", content)

    def test_record_event_writes_structured_fields_and_redacts_payload(self) -> None:
        """结构化审计事件应写入稳定字段和 JSON payload。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            markdown = MarkdownStore(FileStore(root))
            logger = AuditLogger(markdown, Path("data/logs"))
            path = logger.record_event(
                category="permission",
                event_type="permission_decision",
                outcome="waiting",
                note="token should be hidden",
                query_id="query_001",
                task_id="task_001",
                tool_name="sensitive_echo_text",
                tool_use_id="tool_001",
                permission_mode="default",
                payload={"token": "secret-value", "text": "hello"},
            )
            content = path.read_text(encoding="utf-8")
        self.assertIn("permission_decision", content)
        self.assertIn("- category: permission", content)
        self.assertIn('"text": "hello"', content)
        self.assertNotIn("secret-value", content)
        self.assertNotIn("token should be hidden", content)

    def test_preview_uses_same_redaction_strategy(self) -> None:
        """审计预览应与日志写入使用同一套脱敏策略。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            markdown = MarkdownStore(FileStore(root))
            logger = AuditLogger(markdown, Path("data/logs"))
            preview = logger.preview({"authorization": "abc", "text": "hello"})
        self.assertIn("hello", preview)
        self.assertNotIn("abc", preview)


def _self_test() -> None:
    """运行本文件单元测试。"""
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestAuditLog)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    _self_test()
