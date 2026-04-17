# 本文件验证 CLI /chat 调试命令的解析和输出约束。

from pathlib import Path
import sys
import unittest

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
        self.assertIn('"agent_state"', output)
        self.assertIn('"tool_results"', output)


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
            '  "agent_state": {},\n'
            '  "tool_results": []\n'
            '}'
        )


def _self_test() -> None:
    """运行本文件单元测试。"""
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestCliChat)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    _self_test()
