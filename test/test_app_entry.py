# 本文件验证 Step 0 应用入口、CLI 入口和基础目录骨架。

from pathlib import Path
import io
import json
import sys
import time
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
        with patch.object(app, "_bootstrap_background_services") as bootstrap:
            with patch("sys.stdout", new_callable=io.StringIO):
                self.assertEqual(app.run(("--no-interactive",)), 0)
        bootstrap.assert_called_once()

    def test_run_bootstraps_background_services_before_cli(self) -> None:
        """正常启动应先拉起后台服务，再进入 CLI。"""
        app = DutyFlowApp(PROJECT_ROOT)
        with patch.object(app, "_bootstrap_background_services") as bootstrap:
            with patch.object(app.cli, "start", return_value=0) as cli_start:
                self.assertEqual(app.run(()), 0)
        bootstrap.assert_called_once()
        cli_start.assert_called_once_with(interactive=True)

    def test_bootstrap_background_services_starts_runtime_and_feishu(self) -> None:
        """后台服务启动应同时拉起 runtime worker 和飞书监听。"""
        app = DutyFlowApp(PROJECT_ROOT)
        runtime = _FakeRuntimeService()
        ingress = _FakeIngressService()
        with patch.object(app, "_ensure_runtime_layout"):
            with patch.object(app, "_get_or_create_runtime_service", return_value=runtime):
                with patch.object(app, "_get_or_create_feishu_ingress_service", return_value=ingress):
                    app._bootstrap_background_services()
        self.assertTrue(runtime.started)
        self.assertTrue(ingress.started)

    def test_health_mode_does_not_bootstrap_background_services(self) -> None:
        """健康检查模式不应提前启动正式 runtime 和飞书监听。"""
        app = DutyFlowApp(PROJECT_ROOT)
        with patch.object(app, "_bootstrap_background_services") as bootstrap:
            with patch("sys.stdout", new_callable=io.StringIO):
                self.assertEqual(app.run(("--no-interactive",)), 0)
        bootstrap.assert_called_once()
        app = DutyFlowApp(PROJECT_ROOT)
        with patch.object(app, "_bootstrap_background_services") as bootstrap:
            with patch("sys.stdout", new_callable=io.StringIO):
                self.assertEqual(app.run(("--health",)), 0)
        bootstrap.assert_not_called()

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

    def test_chat_debug_status_is_empty_before_worker_start(self) -> None:
        """未提交任务前，/chat 状态应明确提示 worker 尚未启动。"""
        app = DutyFlowApp(PROJECT_ROOT)
        payload = json.loads(app.get_chat_debug_status())
        self.assertEqual(payload["status"], "empty")
        self.assertEqual(payload["action"], "no_worker")

    def test_submit_chat_debug_task_eventually_produces_latest_result(self) -> None:
        """提交非阻塞 /chat 任务后，应能轮询拿到最近结果。"""
        app = DutyFlowApp(PROJECT_ROOT)
        with patch.object(app, "create_chat_debug_session", return_value=_FakeChatSession()):
            accepted = json.loads(app.submit_chat_debug_task("ping"))
            latest = self._wait_for_chat_debug_result(app)
        self.assertEqual(accepted["action"], "accepted")
        self.assertEqual(latest["status"], "ok")
        self.assertEqual(latest["action"], "completed")
        self.assertIn('"final_text": "pong: ping"', latest["payload"]["result_text"])

    def _wait_for_chat_debug_result(self, app: DutyFlowApp) -> dict[str, object]:
        """轮询等待后台 /chat 任务完成，避免测试直接依赖固定 sleep。"""
        deadline = time.time() + 1.0
        while time.time() < deadline:
            payload = json.loads(app.get_latest_chat_debug())
            if payload["action"] == "completed":
                return payload
            time.sleep(0.02)
        raise AssertionError("chat debug worker did not produce latest result in time")


class _FakeRuntimeService:
    """模拟可启动的 runtime service。"""

    def __init__(self) -> None:
        """记录是否已被启动。"""
        self.started = False

    def start(self):
        """模拟 runtime worker 启动。"""
        self.started = True
        return object()


class _FakeIngressService:
    """模拟可启动监听的飞书接入服务。"""

    def __init__(self) -> None:
        """记录是否已被启动。"""
        self.started = False

    def start_long_connection(self):
        """模拟长连接启动。"""
        self.started = True
        return object()


class _FakeChatSession:
    """模拟旧 /chat loop 仍可提供的最小调试会话。"""

    def run_turn(self, user_text: str) -> object:
        """返回具备 to_debug_text 的最小结果对象。"""
        return _FakeChatResult(user_text)


class _FakeChatResult:
    """为应用测试提供最小 chat 调试结果。"""

    def __init__(self, user_text: str) -> None:
        """保存本轮用户输入。"""
        self.user_text = user_text

    def to_debug_text(self) -> str:
        """返回测试用调试文本。"""
        return f'{{"final_text": "pong: {self.user_text}"}}'


def _self_test() -> None:
    """运行本文件的单元测试。"""
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestAppEntry)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    _self_test()
