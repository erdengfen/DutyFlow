# 本文件验证 .env 配置读取和明确缺失项返回。

from pathlib import Path
import sys
import tempfile
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dutyflow.config.env import load_env_config, validate_env_config


class TestEnvConfig(unittest.TestCase):
    """验证 DutyFlow 统一配置入口。"""

    def test_missing_model_config_returns_clear_errors(self) -> None:
        """缺失模型配置时应返回明确缺失键。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            config = load_env_config(Path(temp_dir))
            result = validate_env_config(config)
        self.assertFalse(result.ok)
        self.assertIn("DUTYFLOW_MODEL_API_KEY", result.missing_keys)

    def test_dotenv_values_are_loaded(self) -> None:
        """本地 .env 中的简单 KEY=VALUE 应被读取。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / ".env").write_text("DUTYFLOW_MODEL_NAME=demo\n", encoding="utf-8")
            config = load_env_config(root)
        self.assertEqual(config.model_name, "demo")


def _self_test() -> None:
    """运行本文件单元测试。"""
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestEnvConfig)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    _self_test()
