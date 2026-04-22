# 本文件验证 Hook 预留接口的事件校验和最小运行行为。

from pathlib import Path
import sys
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dutyflow.agent.hooks import HookEvent, HookResult, HookRunner  # noqa: E402


class TestAgentHooks(unittest.TestCase):
    """验证 HookRunner 只提供预留接口，不接入主循环。"""

    def test_unknown_hook_event_is_rejected(self) -> None:
        """未知事件名必须显式失败。"""
        with self.assertRaises(ValueError):
            HookEvent(name="UnknownEvent")

    def test_runner_returns_default_continue_without_handlers(self) -> None:
        """未注册 handler 时应返回默认 continue 结果。"""
        result = HookRunner().run("SessionStart", {"query_id": "query_001"})
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.message, "")

    def test_runner_short_circuits_on_inject_result(self) -> None:
        """出现 inject 结果时应立即停止后续 handler。"""
        calls: list[str] = []

        def allow_handler(event: HookEvent) -> HookResult:
            calls.append("allow")
            return HookResult(exit_code=0)

        def inject_handler(event: HookEvent) -> HookResult:
            calls.append("inject")
            return HookResult(exit_code=2, message="inject message")

        def late_handler(event: HookEvent) -> HookResult:
            calls.append("late")
            return HookResult(exit_code=0)

        runner = HookRunner()
        runner.register("PreToolUse", allow_handler)
        runner.register("PreToolUse", inject_handler)
        runner.register("PreToolUse", late_handler)
        result = runner.run("PreToolUse", {"tool_name": "echo_text"})
        self.assertEqual(result.exit_code, 2)
        self.assertEqual(result.message, "inject message")
        self.assertEqual(calls, ["allow", "inject"])


def _self_test() -> None:
    """运行本文件单元测试。"""
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestAgentHooks)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    _self_test()
