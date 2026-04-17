# 本文件负责工具执行运行时，包括分批、并发、校验和结果信封封装。

from __future__ import annotations

import sys

_THIS_DIR = __file__.rsplit("/", 1)[0]
if sys.path and sys.path[0] == _THIS_DIR:
    sys.path.pop(0)

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Mapping, Sequence

from dutyflow.agent.state import create_initial_agent_state
from dutyflow.agent.tools.context import ToolUseContext
from dutyflow.agent.tools.registry import ToolRegistry
from dutyflow.agent.tools.router import ToolRoute
from dutyflow.agent.tools.types import ToolCall, ToolResultEnvelope, error_envelope


@dataclass(frozen=True)
class ToolExecutionBatch:
    """表示一批可按相同策略执行的 ToolRoute。"""

    is_concurrency_safe: bool
    routes: tuple[ToolRoute, ...]


class ToolExecutor:
    """执行已路由工具并把所有结果封装为 ToolResultEnvelope。"""

    def __init__(self, registry: ToolRegistry, max_workers: int = 4) -> None:
        """绑定注册表并设置并发执行上限。"""
        self.registry = registry
        self.max_workers = max(1, max_workers)

    def execute_routes(
        self,
        routes: Sequence[ToolRoute],
        context: ToolUseContext,
    ) -> tuple[ToolResultEnvelope, ...]:
        """按批次执行 ToolRoute，并按 call_index 稳定返回结果。"""
        envelopes: list[ToolResultEnvelope] = []
        for batch in self.partition_routes(routes):
            if batch.is_concurrency_safe:
                envelopes.extend(self._execute_concurrent_batch(batch, context))
            else:
                envelopes.extend(self._execute_serial_batch(batch, context))
        return tuple(sorted(envelopes, key=lambda item: item.call_index))

    def partition_routes(self, routes: Sequence[ToolRoute]) -> tuple[ToolExecutionBatch, ...]:
        """按并发安全性对 routes 分批，保持原始相邻顺序。"""
        batches: list[ToolExecutionBatch] = []
        current: list[ToolRoute] = []
        current_safe: bool | None = None
        for route in routes:
            route_safe = bool(route.is_executable and route.is_concurrency_safe)
            if current and route_safe != current_safe:
                batches.append(ToolExecutionBatch(bool(current_safe), tuple(current)))
                current = []
            current.append(route)
            current_safe = route_safe
        if current:
            batches.append(ToolExecutionBatch(bool(current_safe), tuple(current)))
        return tuple(batches)

    def _execute_concurrent_batch(
        self,
        batch: ToolExecutionBatch,
        context: ToolUseContext,
    ) -> tuple[ToolResultEnvelope, ...]:
        """真实并发执行 concurrency-safe 批次。"""
        max_workers = min(self.max_workers, len(batch.routes)) or 1
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = [
                pool.submit(self._execute_one_route, route, context)
                for route in batch.routes
            ]
            return tuple(future.result() for future in as_completed(futures))

    def _execute_serial_batch(
        self,
        batch: ToolExecutionBatch,
        context: ToolUseContext,
    ) -> tuple[ToolResultEnvelope, ...]:
        """串行执行 exclusive 或不可执行批次。"""
        return tuple(self._execute_one_route(route, context) for route in batch.routes)

    def _execute_one_route(
        self,
        route: ToolRoute,
        context: ToolUseContext,
    ) -> ToolResultEnvelope:
        """执行单个 route，任何失败都转换成结果信封。"""
        validation = self._validate_route(route, context)
        if validation is not None:
            return validation
        try:
            handler = self.registry.get_handler(route.tool_call.tool_name)
            result = handler(route.tool_call, context)  # type: ignore[misc]
        except Exception as exc:  # noqa: BLE001
            return error_envelope(route.tool_call, "handler_exception", str(exc))
        return self._normalize_handler_result(route, result)

    def _validate_route(
        self,
        route: ToolRoute,
        context: ToolUseContext,
    ) -> ToolResultEnvelope | None:
        """执行前二次校验 route、registry、handler 和输入。"""
        call = route.tool_call
        if not route.is_executable:
            return error_envelope(call, "route_unavailable", route.error_message)
        if context.registry is not self.registry:
            return error_envelope(call, "context_registry_mismatch", "registry mismatch")
        if route.tool_spec.name != call.tool_name:
            return error_envelope(call, "route_mismatch", "route tool name mismatch")
        if not isinstance(call.tool_input, Mapping):
            return error_envelope(call, "invalid_input", "tool input must be dict")
        return self._validate_registered_route(route)

    def _validate_registered_route(self, route: ToolRoute) -> ToolResultEnvelope | None:
        """校验已注册工具的来源、handler 和 input schema。"""
        call = route.tool_call
        if not self.registry.has(call.tool_name):
            return error_envelope(call, "unknown_tool", "tool is not registered")
        spec = self.registry.get(call.tool_name)
        if spec.name != route.tool_spec.name or spec.source != route.source:
            return error_envelope(call, "route_mismatch", "route spec mismatch")
        if spec.source != "native":
            return error_envelope(call, "source_unavailable", "tool source unavailable")
        if spec.requires_approval:
            return error_envelope(call, "approval_required", "permission gate is not implemented")
        if self.registry.get_handler(call.tool_name) is None:
            return error_envelope(call, "missing_handler", "native tool handler is missing")
        return self._validate_input(call, route)

    def _validate_input(self, call: ToolCall, route: ToolRoute) -> ToolResultEnvelope | None:
        """把 registry 输入校验错误封装为结果信封。"""
        try:
            self.registry.validate_tool_input(call)
        except ValueError as exc:
            return error_envelope(route.tool_call, "invalid_input", str(exc))
        return None

    def _normalize_handler_result(
        self,
        route: ToolRoute,
        result: object,
    ) -> ToolResultEnvelope:
        """校验 handler 返回值并补齐 call_index。"""
        call = route.tool_call
        if not isinstance(result, ToolResultEnvelope):
            return error_envelope(call, "invalid_result", "handler returned invalid result")
        if result.tool_use_id != call.tool_use_id or result.tool_name != call.tool_name:
            return error_envelope(call, "invalid_result", "handler result does not match call")
        return replace(result, call_index=call.call_index)


def _self_test() -> None:
    """验证空 route 执行返回空结果。"""
    registry = ToolRegistry()
    state = create_initial_agent_state("query", "hello")
    state_context = ToolUseContext("query", Path.cwd(), state, registry)
    assert ToolExecutor(registry).execute_routes((), state_context) == ()


if __name__ == "__main__":
    _self_test()
    print("dutyflow tool executor self-test passed")
