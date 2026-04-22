# 本文件验证 Step 0 应用入口、CLI 入口和基础目录骨架。

from pathlib import Path
import io
import sys
import unittest
from unittest.mock import patch

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

    def test_no_interactive_keeps_script_check_available(self) -> None:
        """--no-interactive 应保留启动后立即退出的脚本检查能力。"""
        app = DutyFlowApp(PROJECT_ROOT)
        with patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(app.run(("--no-interactive",)), 0)

    def test_cli_permission_prompt_uses_enter_as_approve(self) -> None:
        """CLI 审批提示应允许用户直接按 Enter 放行。"""
        app = DutyFlowApp(PROJECT_ROOT)
        with patch("builtins.input", return_value=""):
            with patch("sys.stdout", new_callable=io.StringIO) as stdout:
                approved = app._prompt_cli_permission("send_message", "sensitive tool", {"text": "hello"})
        self.assertTrue(approved)
        self.assertIn("Permission Required", stdout.getvalue())

    def test_cli_permission_prompt_allows_explicit_reject(self) -> None:
        """CLI 审批提示输入 no 时应拒绝执行。"""
        app = DutyFlowApp(PROJECT_ROOT)
        with patch("builtins.input", return_value="no"):
            self.assertFalse(app._prompt_cli_permission("send_message", "sensitive tool", {"text": "hello"}))


def _self_test() -> None:
    """运行本文件的单元测试。"""
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestAppEntry)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    _self_test()
