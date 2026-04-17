# 本文件验证本地文件存储的读写和工作区边界。

from pathlib import Path
import sys
import tempfile
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dutyflow.storage.file_store import FileStore


class TestFileStore(unittest.TestCase):
    """验证 FileStore 的基础行为。"""

    def test_write_and_read_text(self) -> None:
        """写入后应能读取相同文本。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            store = FileStore(Path(temp_dir))
            store.write_text("data/demo.txt", "hello")
            self.assertEqual(store.read_text("data/demo.txt"), "hello")

    def test_path_escape_is_blocked(self) -> None:
        """访问工作区外路径应被阻止。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            store = FileStore(Path(temp_dir))
            with self.assertRaises(ValueError):
                store.resolve("../outside")


def _self_test() -> None:
    """运行本文件单元测试。"""
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestFileStore)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    _self_test()
