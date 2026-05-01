# 本文件验证 CLI /chat 调试命令的解析和输出约束。

from pathlib import Path
import io
import sys
import unittest
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dutyflow.cli.main import CliConsole  # noqa: E402


class TestCliChat(unittest.TestCase):
    """验证 CLI 可以暴露非阻塞 /chat 调试入口。"""

    def test_help_lists_chat_command(self) -> None:
        """help 输出必须包含 /chat。"""
        self.assertIn("/chat", CliConsole(_FakeApp()).handle_command("/help"))
        self.assertIn("/chat run", CliConsole(_FakeApp()).handle_command("/help"))
        self.assertIn("/chat status", CliConsole(_FakeApp()).handle_command("/help"))
        self.assertIn("/chat latest", CliConsole(_FakeApp()).handle_command("/help"))
        self.assertIn("/agent state", CliConsole(_FakeApp()).handle_command("/help"))
        self.assertIn("/feishu status", CliConsole(_FakeApp()).handle_command("/help"))
        self.assertIn("/feishu doctor", CliConsole(_FakeApp()).handle_command("/help"))

    def test_chat_command_submits_non_blocking_task(self) -> None:
        """CLI /chat 简写形式应委托给 app 的非阻塞调试入口。"""
        output = CliConsole(_FakeApp()).handle_command("/chat ping")
        self.assertIn('"action": "accepted"', output)
        self.assertIn('"user_text": "ping"', output)

    def test_chat_run_status_and_latest_are_available(self) -> None:
        """CLI /chat 应暴露 run、status、latest 三类非阻塞命令。"""
        cli = CliConsole(_FakeApp())
        self.assertIn('"action": "accepted"', cli.handle_command("/chat run first"))
        self.assertIn('"action": "worker_status"', cli.handle_command("/chat status"))
        self.assertIn('"action": "completed"', cli.handle_command("/chat latest"))

    def test_agent_state_command_is_available(self) -> None:
        """CLI 应暴露正式 runtime AgentState 调试视图。"""
        output = CliConsole(_FakeApp()).handle_command("/agent state")
        self.assertIn('"action": "agent_state"', output)
        self.assertIn('"budget_report"', output)

    def test_interactive_chat_no_longer_enters_blocking_sub_session(self) -> None:
        """交互式输入 /chat 时应立即返回结果，而不是进入 Chat> 子会话。"""
        cli = CliConsole(_FakeApp())
        inputs = iter(("/chat ping", "/chat status", "/chat latest", "/exit"))
        with patch("builtins.input", lambda prompt="": next(inputs)):
            with patch("sys.stdout", new_callable=io.StringIO) as stdout:
                self.assertEqual(cli.start(), 0)
        output = stdout.getvalue()
        self.assertNotIn("Chat>", output)
        self.assertNotIn("Chat debug started", output)
        self.assertIn('"action": "accepted"', output)
        self.assertIn('"action": "worker_status"', output)
        self.assertIn('"action": "completed"', output)

    def test_feishu_fixture_command_calls_app_debug_entry(self) -> None:
        """CLI /feishu fixture 应委托给 app 的接入层调试入口。"""
        output = CliConsole(_FakeApp()).handle_command("/feishu fixture ping")
        self.assertIn('"action": "fixture"', output)
        self.assertIn('"detail": "ping"', output)

    def test_feishu_status_and_latest_commands_are_available(self) -> None:
        """CLI 应暴露飞书状态和最近结果查看命令。"""
        cli = CliConsole(_FakeApp())
        self.assertIn("listener_status", cli.handle_command("/feishu"))
        self.assertIn("listener_status", cli.handle_command("/feishu status"))
        self.assertIn("已废弃", cli.handle_command("/feishu listen"))
        self.assertIn('"action": "latest"', cli.handle_command("/feishu latest"))
        self.assertIn('"action": "doctor_status"', cli.handle_command("/feishu doctor"))

    def test_interactive_feishu_doctor_session_keeps_running(self) -> None:
        """交互式 /feishu doctor 应进入诊断子会话，直到 /back。"""
        cli = CliConsole(_FakeApp())
        inputs = iter(("/feishu doctor", "/status", "/listener", "/back", "/exit"))
        with patch("builtins.input", lambda prompt="": next(inputs)):
            with patch("sys.stdout", new_callable=io.StringIO) as stdout:
                self.assertEqual(cli.start(), 0)
        output = stdout.getvalue()
        self.assertIn("Feishu doctor opened", output)
        self.assertIn('"action": "doctor_status"', output)
        self.assertIn('"raw_event_count": 0', output)
        self.assertIn('"action": "listener_status"', output)


class _FakeApp:
    """提供 CLI 测试所需的最小 app 接口。"""

    def health_check(self) -> str:
        """返回健康状态。"""
        return "status=ok"

    def submit_chat_debug_task(self, user_text: str) -> str:
        """返回非阻塞入队结果。"""
        return (
            '{\n'
            '  "action": "accepted",\n'
            '  "payload": {\n'
            f'    "user_text": "{user_text}"\n'
            "  }\n"
            '}'
        )

    def get_chat_debug_status(self) -> str:
        """返回调试 worker 状态。"""
        return '{"action": "worker_status", "payload": {"worker_alive": true}}'

    def get_latest_chat_debug(self) -> str:
        """返回最近一条调试任务结果。"""
        return '{"action": "completed", "payload": {"result_text": "pong"}}'

    def get_agent_state_debug(self) -> str:
        """返回测试 AgentState 调试视图。"""
        return '{"action": "agent_state", "payload": {"budget_report": {"total_estimated_tokens": 1}}}'

    def run_feishu_fixture_debug(self, user_text: str) -> str:
        """返回测试飞书 fixture 结果。"""
        return f'{{"action": "fixture", "detail": "{user_text}"}}'

    def get_feishu_status_debug(self) -> str:
        """返回测试飞书监听状态。"""
        return '{"action": "listener_status", "detail": "running"}'

    def start_feishu_listener_debug(self) -> str:
        """保留兼容；返回测试飞书监听状态。"""
        return self.get_feishu_status_debug()

    def get_latest_feishu_debug(self) -> str:
        """返回测试最近飞书事件。"""
        return '{"action": "latest", "detail": "none"}'

    def get_feishu_doctor_debug(self) -> str:
        """返回测试飞书 doctor 快照。"""
        return '{"action": "doctor_status", "payload": {"listener": {"raw_event_count": 0}}}'


def _self_test() -> None:
    """运行本文件单元测试。"""
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestCliChat)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    _self_test()
