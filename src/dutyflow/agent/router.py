# 本文件负责把 ToolCall 路由到能力来源和执行策略，不执行工具。

from __future__ import annotations

from dataclasses import dataclass

from dutyflow.agent.registry import ToolRegistry
from dutyflow.agent.tools import ToolCall, ToolSpec


@dataclass(frozen=True)
class ToolRoute:
    """表示一次工具调用的执行路线和可执行性。"""

    tool_call: ToolCall
    tool_spec: ToolSpec
    source: str
    is_concurrency_safe: bool
    execution_mode: str
    is_executable: bool
    error_message: str = ""


class ToolRouter:
    """根据注册表把工具调用转换为稳定 ToolRoute。"""

    def __init__(self, registry: ToolRegistry) -> None:
        """绑定工具注册表。"""
        self.registry = registry

    def route(self, tool_call: ToolCall) -> ToolRoute:
        """为单个 ToolCall 生成执行路线。"""
        if not self.registry.has(tool_call.tool_name):
            return self._unknown_route(tool_call)
        spec = self.registry.get(tool_call.tool_name)
        if spec.source != "native":
            return self._reserved_route(tool_call, spec)
        mode = "concurrent" if spec.is_concurrency_safe else "serial"
        return ToolRoute(
            tool_call=tool_call,
            tool_spec=spec,
            source=spec.source,
            is_concurrency_safe=spec.is_concurrency_safe,
            execution_mode=mode,
            is_executable=True,
        )

    def route_many(self, tool_calls: tuple[ToolCall, ...]) -> tuple[ToolRoute, ...]:
        """按输入顺序批量生成 ToolRoute。"""
        return tuple(self.route(tool_call) for tool_call in tool_calls)

    def _unknown_route(self, tool_call: ToolCall) -> ToolRoute:
        """为未注册工具生成不可执行占位路线。"""
        spec = ToolSpec(
            name=tool_call.tool_name,
            description="Unregistered tool placeholder.",
            source="placeholder",
        )
        return ToolRoute(
            tool_call=tool_call,
            tool_spec=spec,
            source="placeholder",
            is_concurrency_safe=False,
            execution_mode="placeholder",
            is_executable=False,
            error_message=f"Tool is not registered: {tool_call.tool_name}",
        )

    def _reserved_route(self, tool_call: ToolCall, spec: ToolSpec) -> ToolRoute:
        """为未实现能力来源生成不可执行占位路线。"""
        return ToolRoute(
            tool_call=tool_call,
            tool_spec=spec,
            source=spec.source,
            is_concurrency_safe=False,
            execution_mode="placeholder",
            is_executable=False,
            error_message=f"Tool source is not implemented: {spec.source}",
        )


def _self_test() -> None:
    """验证未注册工具不会被路由为可执行 native。"""
    router = ToolRouter(ToolRegistry())
    call = ToolCall("tool_1", "missing", {}, 0, 0)
    assert not router.route(call).is_executable


if __name__ == "__main__":
    _self_test()
    print("dutyflow tool router self-test passed")
