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


def _self_test() -> None:
    """运行本文件单元测试。"""
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestAuditLog)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    _self_test()
