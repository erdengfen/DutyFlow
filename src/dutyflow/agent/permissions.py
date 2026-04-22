# 本文件负责工具执行前的权限判断，只输出稳定的 allow/deny/ask 决策。

from __future__ import annotations

from dataclasses import dataclass

from dutyflow.agent.tools.context import ToolUseContext
from dutyflow.agent.tools.router import ToolRoute

PERMISSION_MODES = frozenset({"default", "plan", "auto"})
PERMISSION_BEHAVIORS = frozenset({"allow", "deny", "ask"})


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
        if not self._is_sensitive(route):
            return PermissionDecision(
                mode=mode,
                behavior="allow",
                reason="tool declared low-risk execution",
                tool_name=route.tool_call.tool_name,
                is_sensitive=False,
            )
        if mode == "auto":
            return PermissionDecision(
                mode=mode,
                behavior="deny",
                reason="auto mode blocked sensitive tool",
                tool_name=route.tool_call.tool_name,
                is_sensitive=True,
            )
        return PermissionDecision(
            mode=mode,
            behavior="ask",
            reason=f"{mode} mode requires manual approval for sensitive tool",
            tool_name=route.tool_call.tool_name,
            is_sensitive=True,
        )

    def _resolve_mode(self, mode: str) -> str:
        """解析上下文中的权限模式，缺失时回落到 default。"""
        normalized = (mode or "default").strip().lower()
        if normalized not in PERMISSION_MODES:
            raise ValueError(f"Unknown permission mode: {normalized}")
        return normalized

    def _is_sensitive(self, route: ToolRoute) -> bool:
        """根据当前工具声明判断是否属于敏感工具。"""
        spec = route.tool_spec
        if spec.requires_approval:
            return True
        if spec.idempotency != "read_only":
            return True
        return False


def _self_test() -> None:
    """验证 default 模式下敏感工具会进入 ask。"""
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


if __name__ == "__main__":
    _self_test()
    print("dutyflow permission gate self-test passed")
