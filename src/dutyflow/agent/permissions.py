# 本文件负责工具执行前的权限判断，只输出稳定的 allow/deny/ask 决策。

from __future__ import annotations

from dataclasses import dataclass
import re
import shlex

from dutyflow.agent.tools.context import ToolUseContext
from dutyflow.agent.tools.router import ToolRoute

PERMISSION_MODES = frozenset({"default", "plan", "auto"})
PERMISSION_BEHAVIORS = frozenset({"allow", "deny", "ask"})

CLI_READ_ONLY_COMMANDS = frozenset(
    {
        "cat",
        "cd",
        "env",
        "export",
        "find",
        "git",
        "grep",
        "head",
        "ls",
        "pwd",
        "printf",
        "rg",
        "sleep",
        "stat",
        "tail",
        "unset",
        "wc",
        "which",
    }
)
CLI_DANGEROUS_COMMANDS = frozenset(
    {
        "chmod",
        "chown",
        "cp",
        "curl",
        "dd",
        "git-apply",
        "install",
        "ln",
        "mkdir",
        "mv",
        "pip",
        "python",
        "python3",
        "rm",
        "rmdir",
        "sed",
        "sh",
        "tee",
        "touch",
        "truncate",
        "uv",
        "wget",
    }
)
CLI_SAFE_GIT_SUBCOMMANDS = frozenset({"branch", "diff", "log", "rev-parse", "show", "status"})
CLI_DANGEROUS_GIT_SUBCOMMANDS = frozenset(
    {
        "add",
        "apply",
        "checkout",
        "cherry-pick",
        "clean",
        "commit",
        "fetch",
        "merge",
        "pull",
        "push",
        "rebase",
        "reset",
        "restore",
        "revert",
        "stash",
        "switch",
        "tag",
    }
)


@dataclass(frozen=True)
class PermissionDecision:
    """表示一次工具调用在权限层得到的稳定结果。"""

    mode: str
    behavior: str
    reason: str
    tool_name: str
    is_sensitive: bool

    def __post_init__(self) -> None:
        """校验权限决定字段的取值范围。"""
        if self.mode not in PERMISSION_MODES:
            raise ValueError(f"Unknown permission mode: {self.mode}")
        if self.behavior not in PERMISSION_BEHAVIORS:
            raise ValueError(f"Unknown permission behavior: {self.behavior}")
        if not self.tool_name:
            raise ValueError("PermissionDecision.tool_name is required")
        if not self.reason:
            raise ValueError("PermissionDecision.reason is required")


class PermissionGate:
    """根据工具声明和当前模式生成工具执行前的权限决定。"""

    def decide(self, route: ToolRoute, context: ToolUseContext) -> PermissionDecision:
        """返回当前工具调用的 allow、deny 或 ask 决策。"""
        mode = self._resolve_mode(context.permission_mode)
        if not route.is_executable:
            return PermissionDecision(
                mode=mode,
                behavior="allow",
                reason="route validation will handle unavailable tool",
                tool_name=route.tool_call.tool_name,
                is_sensitive=False,
            )
        static_reason = self._static_sensitive_reason(route)
        if static_reason is not None:
            return self._sensitive_decision(mode, route, static_reason)
        command_reason = self._command_sensitive_reason(route)
        if command_reason is not None:
            return self._sensitive_decision(mode, route, command_reason)
        return PermissionDecision(
            mode=mode,
            behavior="allow",
            reason="tool declared low-risk execution and command inspection passed",
            tool_name=route.tool_call.tool_name,
            is_sensitive=False,
        )

    def _sensitive_decision(
        self,
        mode: str,
        route: ToolRoute,
        reason: str,
    ) -> PermissionDecision:
        """根据当前模式把敏感执行映射为 ask 或 deny。"""
        if mode == "auto":
            return PermissionDecision(
                mode=mode,
                behavior="deny",
                reason=reason,
                tool_name=route.tool_call.tool_name,
                is_sensitive=True,
            )
        return PermissionDecision(
            mode=mode,
            behavior="ask",
            reason=reason,
            tool_name=route.tool_call.tool_name,
            is_sensitive=True,
        )

    def _resolve_mode(self, mode: str) -> str:
        """解析上下文中的权限模式，缺失时回落到 default。"""
        normalized = (mode or "default").strip().lower()
        if normalized not in PERMISSION_MODES:
            raise ValueError(f"Unknown permission mode: {normalized}")
        return normalized

    def _static_sensitive_reason(self, route: ToolRoute) -> str | None:
        """根据当前工具静态声明判断是否必须走敏感执行。"""
        spec = route.tool_spec
        if spec.requires_approval:
            return "tool declared requires_approval"
        if spec.idempotency != "read_only":
            return f"tool declared non-read-only idempotency: {spec.idempotency}"
        return None

    def _command_sensitive_reason(self, route: ToolRoute) -> str | None:
        """对命令型工具按本次 tool_call 内容做危险命令识别。"""
        if route.tool_call.tool_name != "exec_cli_command":
            return None
        command = str(route.tool_call.tool_input.get("command", "")).strip()
        if not command:
            return "command inspection failed: empty command requires manual approval"
        if _is_dangerous_cli_command(command):
            return "command inspection flagged dangerous shell command"
        return None


def _is_dangerous_cli_command(command: str) -> bool:
    """判断当前 CLI 命令是否超出只读低风险范围。"""
    for segment in _split_command_segments(command):
        tokens = _safe_split_tokens(segment)
        if not tokens:
            continue
        if _segment_contains_file_write(segment):
            return True
        if _segment_is_dangerous(tokens):
            return True
    return False


def _split_command_segments(command: str) -> tuple[str, ...]:
    """按常见 shell 串联符切分命令片段。"""
    parts = re.split(r"\s*(?:&&|\|\||;|\|)\s*", command)
    return tuple(part.strip() for part in parts if part.strip())


def _safe_split_tokens(command: str) -> tuple[str, ...]:
    """使用 shlex 拆分命令；失败时回退到空白拆分。"""
    try:
        return tuple(shlex.split(command, posix=True))
    except ValueError:
        return tuple(token for token in command.split() if token)


def _segment_contains_file_write(segment: str) -> bool:
    """识别输出重定向到文件的写入行为。"""
    return bool(re.search(r"(^|[\s])(?:>>?|1>>?|2>>?)(?!\s*&\d)", segment))


def _segment_is_dangerous(tokens: tuple[str, ...]) -> bool:
    """判断单个命令片段是否属于危险执行。"""
    head = tokens[0]
    if head == "git":
        return _git_segment_is_dangerous(tokens)
    if head in CLI_DANGEROUS_COMMANDS:
        if head == "sed" and "-i" not in tokens[1:]:
            return False
        return True
    if head in CLI_READ_ONLY_COMMANDS:
        return False
    return True


def _git_segment_is_dangerous(tokens: tuple[str, ...]) -> bool:
    """识别 git 子命令是否属于只读。"""
    if len(tokens) < 2:
        return True
    subcommand = tokens[1]
    if subcommand in CLI_SAFE_GIT_SUBCOMMANDS:
        return False
    if subcommand in CLI_DANGEROUS_GIT_SUBCOMMANDS:
        return True
    return True


def _self_test() -> None:
    """验证静态敏感工具和危险命令都会进入 ask。"""
    from pathlib import Path

    from dutyflow.agent.state import create_initial_agent_state
    from dutyflow.agent.tools.context import ToolUseContext
    from dutyflow.agent.tools.registry import ToolRegistry
    from dutyflow.agent.tools.router import ToolRoute
    from dutyflow.agent.tools.types import ToolCall, ToolSpec

    route = ToolRoute(
        tool_call=ToolCall("tool_1", "sensitive_tool", {"text": "x"}, 0, 0),
        tool_spec=ToolSpec(
            name="sensitive_tool",
            description="demo",
            input_schema={"required": ["text"]},
            requires_approval=True,
        ),
        source="native",
        is_concurrency_safe=False,
        execution_mode="serial",
        is_executable=True,
    )
    context = ToolUseContext(
        query_id="query_permission",
        cwd=Path.cwd(),
        agent_state=create_initial_agent_state("query_permission", "run"),
        registry=ToolRegistry(),
    )
    decision = PermissionGate().decide(route, context)
    assert decision.behavior == "ask"
    command_route = ToolRoute(
        tool_call=ToolCall(
            "tool_2",
            "exec_cli_command",
            {"session_id": "sess_1", "command": "git commit -m demo", "timeout": 1.0},
            0,
            0,
        ),
        tool_spec=ToolSpec(
            name="exec_cli_command",
            description="demo",
            input_schema={"required": ["session_id", "command", "timeout"]},
        ),
        source="native",
        is_concurrency_safe=False,
        execution_mode="serial",
        is_executable=True,
    )
    assert PermissionGate().decide(command_route, context).behavior == "ask"
    assert _is_dangerous_cli_command("pwd") is False
    assert _is_dangerous_cli_command("git status") is False
    assert _is_dangerous_cli_command("rm -rf data") is True


if __name__ == "__main__":
    _self_test()
    print("dutyflow permission gate self-test passed")
