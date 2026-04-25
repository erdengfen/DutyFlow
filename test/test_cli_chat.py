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
    """验证 CLI 可以暴露 /chat 调试入口。"""

    def test_help_lists_chat_command(self) -> None:
        """help 输出必须包含 /chat。"""
        self.assertIn("/chat", CliConsole(_FakeApp()).handle_command("/help"))

    def test_chat_command_calls_app_chat_debug(self) -> None:
        """CLI /chat 应委托给 app 的调试链路。"""
        output = CliConsole(_FakeApp()).handle_command("/chat ping")
        self.assertIn('"final_text": "pong: ping"', output)
        self.assertIn('"tools"', output)
        self.assertIn('"tool_result_count"', output)

    def test_interactive_chat_session_keeps_running(self) -> None:
        """交互式 /chat 应持续接收多轮输入直到 /back。"""
        cli = CliConsole(_FakeApp())
        inputs = iter(("/chat", "first", "/chat second", "/back", "/exit"))
        with patch("builtins.input", lambda prompt="": next(inputs)):
            with patch("sys.stdout", new_callable=io.StringIO) as stdout:
                self.assertEqual(cli.start(), 0)
        output = stdout.getvalue()
        self.assertIn("Chat debug started", output)
        self.assertIn('"turn": 1', output)
        self.assertIn('"turn": 2', output)
        self.assertIn('"final_text": "second"', output)

    def test_multiline_paste_is_merged_into_single_chat_turn(self) -> None:
        """多行粘贴内容应合并成一轮 chat 输入，而不是拆成多条命令。"""
        cli = CliConsole(_FakeApp())
        inputs = iter(("/chat", "line one", "/back", "/exit"))
        with patch("builtins.input", lambda prompt="": next(inputs)):
            with patch.object(cli, "_read_immediate_chat_lines", return_value=("line two", "", "line four")):
                with patch("sys.stdout", new_callable=io.StringIO) as stdout:
                    self.assertEqual(cli.start(), 0)
        output = stdout.getvalue()
        self.assertIn('line one\nline two\n\nline four', output)
        self.assertNotIn("Unsupported command", output)
        self.assertIn('"turn": 1', output)

    def test_interactive_chat_turn_error_does_not_exit(self) -> None:
        """Chat 单轮异常应封装输出，并允许返回主 CLI。"""
        cli = CliConsole(_ErrorApp())
        inputs = iter(("/chat", "boom", "/back", "/exit"))
        with patch("builtins.input", lambda prompt="": next(inputs)):
            with patch("sys.stdout", new_callable=io.StringIO) as stdout:
                self.assertEqual(cli.start(), 0)
        output = stdout.getvalue()
        self.assertIn("chat_turn_failed", output)
        self.assertIn("DutyFlow CLI started", output)


class _FakeApp:
    """提供 CLI 测试所需的最小 app 接口。"""

    def health_check(self) -> str:
        """返回健康状态。"""
        return "status=ok"

    def run_chat_debug(self, user_text: str) -> str:
        """返回可见调试结果。"""
        return (
            '{\n'
            f'  "final_text": "pong: {user_text}",\n'
            '  "tool_result_count": 0,\n'
            '  "tools": []\n'
            '}'
        )

    def create_chat_debug_session(self) -> object:
        """返回测试 chat 会话。"""
        return _FakeChatSession()


class _FakeChatSession:
    """为 CLI 子会话测试提供最小对象。"""

    def __init__(self) -> None:
        """初始化轮次。"""
        self.turn = 0

    def run_turn(self, user_text: str) -> object:
        """返回带 to_debug_text 的结果对象。"""
        self.turn += 1
        return _FakeChatResult(user_text, self.turn)


class _FakeChatResult:
    """提供 CLI 子会话测试输出。"""

    def __init__(self, user_text: str, turn: int) -> None:
        """保存用户输入和轮次。"""
        self.user_text = user_text
        self.turn = turn

    def to_debug_text(self) -> str:
        """返回测试调试文本。"""
        return f'{{"final_text": "{self.user_text}", "turn": {self.turn}}}'


class _ErrorApp(_FakeApp):
    """提供会抛错的 Chat 会话。"""

    def create_chat_debug_session(self) -> object:
        """返回错误会话。"""
        return _ErrorChatSession()


class _ErrorChatSession:
    """模拟第二轮模型/API异常。"""

    def run_turn(self, user_text: str) -> object:
        """抛出测试异常。"""
        raise RuntimeError("fake chat error")


def _self_test() -> None:
    """运行本文件单元测试。"""
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestCliChat)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    _self_test()
