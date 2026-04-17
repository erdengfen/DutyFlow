# 本文件验证 Step 0 应用入口、CLI 入口和基础目录骨架。

from pathlib import Path
import sys
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dutyflow.app import DutyFlowApp


class TestAppEntry(unittest.TestCase):
    """验证 DutyFlow Step 0 的入口迁移和健康检查。"""

    def test_health_check_reports_required_dirs(self) -> None:
        """健康检查应返回基础目录存在状态。"""
        app = DutyFlowApp(PROJECT_ROOT)
        status = app.health_check()
        self.assertEqual(status.status, "ok")
        self.assertTrue(status.data_dir_exists)
        self.assertTrue(status.skills_dir_exists)
        self.assertTrue(status.test_dir_exists)
        self.assertTrue(status.agent_control_state_exists)

    def test_cli_health_command_uses_app(self) -> None:
        """CLI 的 /health 命令应通过应用实例获取健康状态。"""
        app = DutyFlowApp(PROJECT_ROOT)
        output = app.cli.handle_command("/health")
        self.assertIn("status=ok", output)
        self.assertIn("app_entry=src/dutyflow/app.py", output)
        self.assertIn("agent_control_state_exists=True", output)


def _self_test() -> None:
    """运行本文件的单元测试。"""
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestAppEntry)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    _self_test()
