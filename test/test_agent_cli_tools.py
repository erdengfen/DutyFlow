# 本文件验证 Linux / WSL CLI session tools 的审批、持久上下文和结构化输出。

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dutyflow.agent.cli_session import get_cli_session_manager  # noqa: E402
from dutyflow.agent.state import create_initial_agent_state  # noqa: E402
from dutyflow.agent.tools.context import ToolUseContext  # noqa: E402
from dutyflow.agent.tools.executor import ToolExecutor  # noqa: E402
from dutyflow.agent.tools.registry import create_runtime_tool_registry  # noqa: E402
from dutyflow.agent.tools.router import ToolRouter  # noqa: E402
from dutyflow.agent.tools.types import ToolCall  # noqa: E402


class TestAgentCliTools(unittest.TestCase):
    """验证 CLI session tools 的最小可控闭环。"""

    def setUp(self) -> None:
        """每条测试前关闭遗留 session，避免互相污染。"""
        get_cli_session_manager().close_all_sessions()

    def tearDown(self) -> None:
        """每条测试后关闭遗留 session，避免 bash 常驻进程泄漏。"""
        get_cli_session_manager().close_all_sessions()

    def test_open_exec_close_round_trip(self) -> None:
        """只读命令应能打开、执行并关闭一个 bash session。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            registry = create_runtime_tool_registry()
            context = _context(registry, Path(temp_dir), approved=False)
            opened = _execute(registry, _open_call(), context)
            self.assertTrue(opened.ok)
            session_id = _json_content(opened)["session_id"]
            result = _execute(registry, _exec_call(session_id, "pwd"), context)
            payload = _json_content(result)
            self.assertTrue(result.ok)
            self.assertEqual(payload["exit_code"], 0)
            self.assertEqual(Path(payload["cwd_after"]), Path(temp_dir))
            closed = _execute(registry, _close_call(session_id), context)
            self.assertTrue(_json_content(closed)["closed"])

    def test_exec_command_preserves_cwd_between_calls(self) -> None:
        """同一 session 内执行 cd 后，后续命令应继承新的 cwd。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            cwd = Path(temp_dir)
            (cwd / "nested").mkdir()
            registry = create_runtime_tool_registry()
            context = _context(registry, cwd, approved=False)
            session_id = _json_content(_execute(registry, _open_call(), context))["session_id"]
            first = _execute(registry, _exec_call(session_id, "cd nested"), context)
            second = _execute(registry, _exec_call(session_id, "pwd"), context)
            self.assertTrue(first.ok)
            self.assertEqual(Path(_json_content(second)["cwd_after"]), cwd / "nested")

    def test_exec_command_preserves_exported_env(self) -> None:
        """同一 session 内 export 的环境变量应能在下一条命令中读取。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            registry = create_runtime_tool_registry()
            context = _context(registry, Path(temp_dir), approved=False)
            session_id = _json_content(_execute(registry, _open_call(), context))["session_id"]
            _execute(registry, _exec_call(session_id, "export DUTYFLOW_SESSION_VAR=hello"), context)
            result = _execute(registry, _exec_call(session_id, 'printf "$DUTYFLOW_SESSION_VAR"'), context)
            self.assertEqual(_json_content(result)["stdout"], "hello")

    def test_exec_command_separates_stdout_and_stderr(self) -> None:
        """命令输出应分别返回 stdout 和 stderr。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            registry = create_runtime_tool_registry()
            context = _context(registry, Path(temp_dir), approved=False)
            session_id = _json_content(_execute(registry, _open_call(), context))["session_id"]
            result = _execute(registry, _exec_call(session_id, "printf out; printf err >&2"), context)
            payload = _json_content(result)
            self.assertEqual(payload["stdout"], "out")
            self.assertEqual(payload["stderr"], "err")
            self.assertFalse(payload["timed_out"])

    def test_dangerous_cli_command_requires_manual_approval(self) -> None:
        """审批拒绝时危险 CLI 命令不应执行。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            registry = create_runtime_tool_registry()
            context = _context(registry, Path(temp_dir), approved=False)
            session_id = _json_content(_execute(registry, _open_call(), context))["session_id"]
            result = _execute(registry, _exec_call(session_id, "git commit -m demo"), context)
            self.assertFalse(result.ok)
            self.assertEqual(result.error_kind, "approval_rejected")

    def test_timeout_closes_session_for_safety(self) -> None:
        """命令超时后应返回结构化超时结果，并关闭原 session。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            registry = create_runtime_tool_registry()
            context = _context(registry, Path(temp_dir), approved=False)
            session_id = _json_content(_execute(registry, _open_call(), context))["session_id"]
            timed_out = _execute(registry, _exec_call(session_id, "sleep 0.2", timeout=0.05), context)
            self.assertFalse(timed_out.ok)
            self.assertEqual(timed_out.error_kind, "command_timed_out")
            self.assertTrue(_json_content(timed_out)["timed_out"])
            missing = _execute(registry, _close_call(session_id), context)
            self.assertFalse(missing.ok)
            self.assertEqual(missing.error_kind, "unknown_session")


def _open_call() -> ToolCall:
    """构造测试用 open_cli_session 调用。"""
    return ToolCall(
        "tool_open_1",
        "open_cli_session",
        {"cwd": ".", "timeout": 1.0, "shell_type": "bash"},
        0,
        0,
    )


def _exec_call(session_id: str, command: str, timeout: float = 1.0) -> ToolCall:
    """构造测试用 exec_cli_command 调用。"""
    return ToolCall(
        "tool_exec_1",
        "exec_cli_command",
        {"session_id": session_id, "command": command, "timeout": timeout},
        0,
        0,
    )


def _close_call(session_id: str) -> ToolCall:
    """构造测试用 close_cli_session 调用。"""
    return ToolCall("tool_close_1", "close_cli_session", {"session_id": session_id}, 0, 0)


def _context(registry, cwd: Path, approved: bool) -> ToolUseContext:
    """构造 CLI tool 测试上下文。"""
    return ToolUseContext(
        query_id="query_cli_001",
        cwd=cwd,
        agent_state=create_initial_agent_state("query_cli_001", "run"),
        registry=registry,
        approval_requester=lambda tool_name, reason, tool_input: approved,
    )


def _execute(registry, call: ToolCall, context: ToolUseContext):
    """通过真实执行层运行单个 CLI tool 调用。"""
    routes = ToolRouter(registry).route_many((call,))
    return ToolExecutor(registry).execute_routes(routes, context)[0]


def _json_content(result) -> dict[str, object]:
    """把工具结果中的 JSON 文本解析成字典。"""
    return json.loads(result.content)


def _self_test() -> None:
    """运行本文件单元测试。"""
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestAgentCliTools)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    _self_test()
