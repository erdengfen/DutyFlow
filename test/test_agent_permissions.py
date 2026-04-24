# 本文件验证工具权限层的模式决策和敏感工具识别。

from pathlib import Path
import sys
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dutyflow.agent.permissions import PermissionGate  # noqa: E402
from dutyflow.agent.state import create_initial_agent_state  # noqa: E402
from dutyflow.agent.tools.context import ToolUseContext  # noqa: E402
from dutyflow.agent.tools.registry import ToolRegistry  # noqa: E402
from dutyflow.agent.tools.router import ToolRoute  # noqa: E402
from dutyflow.agent.tools.types import ToolCall, ToolSpec  # noqa: E402


class TestAgentPermissions(unittest.TestCase):
    """验证 PermissionGate 的第一版模式规则。"""

    def test_default_mode_asks_for_sensitive_tool(self) -> None:
        """default 模式下敏感工具应进入人工审批。"""
        decision = PermissionGate().decide(_route(requires_approval=True), _context("default"))
        self.assertEqual(decision.behavior, "ask")

    def test_plan_mode_asks_for_sensitive_tool(self) -> None:
        """plan 模式下敏感工具也应进入人工审批。"""
        decision = PermissionGate().decide(_route(idempotency="idempotent"), _context("plan"))
        self.assertEqual(decision.behavior, "ask")

    def test_auto_mode_denies_sensitive_tool(self) -> None:
        """auto 模式下敏感工具应直接拒绝。"""
        decision = PermissionGate().decide(_route(requires_approval=True), _context("auto"))
        self.assertEqual(decision.behavior, "deny")

    def test_safe_tool_is_allowed(self) -> None:
        """低风险只读工具在各模式下都可直接放行。"""
        decision = PermissionGate().decide(_route(), _context("default"))
        self.assertEqual(decision.behavior, "allow")

    def test_default_mode_asks_for_dangerous_cli_command(self) -> None:
        """default 模式下危险 CLI 命令应升级为审批。"""
        decision = PermissionGate().decide(_exec_route("git commit -m demo"), _context("default"))
        self.assertEqual(decision.behavior, "ask")

    def test_auto_mode_denies_dangerous_cli_command(self) -> None:
        """auto 模式下危险 CLI 命令应直接拒绝。"""
        decision = PermissionGate().decide(_exec_route("rm -rf data"), _context("auto"))
        self.assertEqual(decision.behavior, "deny")

    def test_read_only_cli_command_is_allowed(self) -> None:
        """只读 CLI 命令在 default 模式下应直接放行。"""
        decision = PermissionGate().decide(_exec_route("git status"), _context("default"))
        self.assertEqual(decision.behavior, "allow")

    def test_unknown_mode_is_rejected(self) -> None:
        """未知权限模式必须报错，避免静默回退。"""
        with self.assertRaises(ValueError):
            PermissionGate().decide(_route(), _context("mystery"))


def _context(mode: str) -> ToolUseContext:
    """构造带指定 permission_mode 的测试上下文。"""
    return ToolUseContext(
        query_id="query_permission_test",
        cwd=PROJECT_ROOT,
        agent_state=create_initial_agent_state("query_permission_test", "run"),
        registry=ToolRegistry(),
        permission_mode=mode,
    )


def _route(
    requires_approval: bool = False,
    idempotency: str = "read_only",
) -> ToolRoute:
    """构造测试用 ToolRoute。"""
    return ToolRoute(
        tool_call=ToolCall("tool_1", "demo_tool", {"text": "hello"}, 0, 0),
        tool_spec=ToolSpec(
            name="demo_tool",
            description="demo",
            input_schema={"required": ["text"]},
            requires_approval=requires_approval,
            idempotency=idempotency,
        ),
        source="native",
        is_concurrency_safe=True,
        execution_mode="concurrent",
        is_executable=True,
    )


def _exec_route(command: str) -> ToolRoute:
    """构造测试用 exec_cli_command 路线。"""
    return ToolRoute(
        tool_call=ToolCall(
            "tool_2",
            "exec_cli_command",
            {"session_id": "sess_1", "command": command, "timeout": 1.0},
            0,
            0,
        ),
        tool_spec=ToolSpec(
            name="exec_cli_command",
            description="exec",
            input_schema={"required": ["session_id", "command", "timeout"]},
            requires_approval=False,
            idempotency="read_only",
        ),
        source="native",
        is_concurrency_safe=False,
        execution_mode="serial",
        is_executable=True,
    )


def _self_test() -> None:
    """运行本文件单元测试。"""
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestAgentPermissions)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    _self_test()
