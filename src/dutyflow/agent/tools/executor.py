# 本文件负责工具执行运行时，包括分批、并发、校验和结果信封封装。

from __future__ import annotations

import sys
import time
from random import uniform

_THIS_DIR = __file__.rsplit("/", 1)[0]
if sys.path and sys.path[0] == _THIS_DIR:
    sys.path.pop(0)

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError, as_completed
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Mapping, Sequence

from dutyflow.agent.permissions import PermissionDecision, PermissionGate
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


RETRYABLE_ERROR_KINDS = frozenset(
    {
        "tool_timeout",
        "temporary_transport_error",
        "rate_limited",
        "upstream_unavailable",
    }
)

# 关键阈值：任务控制中的 attempt_count 达到 3 后，工具失败会附带人工确认升级建议。
MANUAL_REVIEW_ATTEMPT_THRESHOLD = 3


class ToolExecutor:
    """执行已路由工具并把所有结果封装为 ToolResultEnvelope。"""

    def __init__(
        self,
        registry: ToolRegistry,
        max_workers: int = 4,
        permission_gate: PermissionGate | None = None,
    ) -> None:
        """绑定注册表并设置并发执行上限。"""
        self.registry = registry
        self.permission_gate = permission_gate or PermissionGate()
        # 关键开关：concurrency-safe 工具批次的最大并发数量；当前默认最多并发 4 个工具调用。
        self.max_workers = max(1, max_workers)
        # 关键开关：可重试错误的最大重试次数；当前默认失败后最多追加 3 次重试。
        self.max_retries = 3

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
        # 关键开关：单批实际线程数不会超过配置上限，也不会超过当前批次的工具数量。
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
        permission = self._enforce_permission(route, context)
        if permission is not None:
            return permission
        result = self._execute_with_retry(route, context)
        return self._normalize_handler_result(route, result)

    def _execute_with_retry(
        self,
        route: ToolRoute,
        context: ToolUseContext,
    ) -> ToolResultEnvelope:
        """对可重试错误执行统一重试，不把重试逻辑散落到其它层。"""
        last_result: ToolResultEnvelope | None = None
        retry_budget = self._retry_budget(route)
        for attempt in range(retry_budget + 1):
            result = self._run_handler_with_timeout(route, context)
            if not isinstance(result, ToolResultEnvelope):
                return error_envelope(
                    route.tool_call,
                    "invalid_result",
                    "handler returned invalid result",
                )
            retryable = bool((not result.ok) and result.error_kind in RETRYABLE_ERROR_KINDS)
            should_retry = self._should_retry(route, result, attempt)
            normalized = replace(
                result,
                attempt_count=attempt + 1,
                retryable=retryable,
                retry_exhausted=retryable and (attempt >= retry_budget),
            )
            normalized = self._attach_degradation_hints(route, context, normalized)
            last_result = normalized
            if normalized.ok or not should_retry:
                return normalized
            self._sleep_before_retry(self._backoff_delay(attempt))
        if last_result is None:
            return error_envelope(route.tool_call, "handler_exception", "tool execution failed without result")
        return last_result

    def _run_handler_with_timeout(
        self,
        route: ToolRoute,
        context: ToolUseContext,
    ) -> ToolResultEnvelope:
        """在统一超时预算内执行 handler。

        注意：
        - 当前线程模型下，超时只能判定本轮失败，不能强制停止已在运行的线程。
        - 真正可能长时间阻塞的工具必须在自身 I/O 层继续设置 timeout。
        """
        handler = self.registry.get_handler(route.tool_call.tool_name)
        spec = route.tool_spec
        pool = ThreadPoolExecutor(max_workers=1)
        future = pool.submit(handler, route.tool_call, context)  # type: ignore[misc]
        try:
            result = future.result(timeout=spec.timeout_seconds)
        except FutureTimeoutError:
            future.cancel()
            return error_envelope(
                route.tool_call,
                "tool_timeout",
                f"tool execution exceeded timeout: {spec.timeout_seconds:.3f}s",
            )
        except Exception as exc:  # noqa: BLE001
            return self._classify_handler_exception(route, exc)
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
        if isinstance(result, ToolResultEnvelope):
            return result
        return error_envelope(route.tool_call, "invalid_result", "handler returned invalid result")

    def _classify_handler_exception(
        self,
        route: ToolRoute,
        exc: Exception,
    ) -> ToolResultEnvelope:
        """把 handler 异常归类为可重试或不可重试错误。"""
        if isinstance(exc, TimeoutError):
            return error_envelope(route.tool_call, "temporary_transport_error", str(exc))
        if isinstance(exc, ConnectionError):
            return error_envelope(route.tool_call, "temporary_transport_error", str(exc))
        if isinstance(exc, OSError):
            return error_envelope(route.tool_call, "temporary_transport_error", str(exc))
        return error_envelope(route.tool_call, "handler_exception", str(exc))

    def _should_retry(self, route: ToolRoute, result: ToolResultEnvelope, attempt: int) -> bool:
        """判断本次失败是否进入下一次重试。"""
        if result.ok:
            return False
        if attempt >= self._retry_budget(route):
            return False
        return self._retry_allowed_by_policy(route, result)

    def _retry_budget(self, route: ToolRoute) -> int:
        """返回当前工具声明允许的最大重试次数。"""
        return max(0, min(self.max_retries, route.tool_spec.max_retries))

    def _retry_allowed_by_policy(self, route: ToolRoute, result: ToolResultEnvelope) -> bool:
        """根据工具声明判断当前错误是否允许进入重试。"""
        spec = route.tool_spec
        if spec.retry_policy == "none":
            return False
        if spec.idempotency == "unsafe":
            return False
        if spec.retry_policy == "transient_only":
            return result.error_kind in RETRYABLE_ERROR_KINDS
        return False

    def _backoff_delay(self, attempt: int) -> float:
        """返回下一次重试前的退避秒数。"""
        # 关键开关：重试退避基数固定为 0.5 秒，按指数增长；附加最多 0.1 秒随机抖动，避免瞬时重放。
        return (0.5 * (2 ** attempt)) + uniform(0.0, 0.1)

    def _sleep_before_retry(self, delay_seconds: float) -> None:
        """在下一次重试前等待退避时间。"""
        if delay_seconds > 0:
            time.sleep(delay_seconds)

    def _attach_degradation_hints(
        self,
        route: ToolRoute,
        context: ToolUseContext,
        result: ToolResultEnvelope,
    ) -> ToolResultEnvelope:
        """为失败结果附加降级或人工确认建议，但本阶段不自动执行这些建议。"""
        if result.ok:
            return result
        hints = list(result.context_modifiers)
        spec = route.tool_spec
        task_control = context.agent_state.task_control
        if spec.degradation_mode == "narrow":
            hints.append(
                {
                    "type": "degradation_hint",
                    "strategy": "narrow_scope",
                    "reason": "tool_declared_narrow_fallback",
                    "tool_name": route.tool_call.tool_name,
                }
            )
        if spec.degradation_mode == "fallback" and spec.fallback_tool_names:
            hints.append(
                {
                    "type": "degradation_hint",
                    "strategy": "fallback_tool",
                    "reason": "tool_declared_fallback_candidates",
                    "tool_name": route.tool_call.tool_name,
                    "fallback_tool_names": spec.fallback_tool_names,
                }
            )
        if spec.degradation_mode == "escalate" or spec.idempotency == "unsafe":
            hints.append(
                {
                    "type": "degradation_hint",
                    "strategy": "approval_or_manual_review",
                    "reason": "unsafe_side_effect_or_declared_escalation",
                    "tool_name": route.tool_call.tool_name,
                }
            )
        if result.retry_exhausted:
            hints.append(
                {
                    "type": "degradation_hint",
                    "strategy": "approval_or_manual_review",
                    "reason": "retry_exhausted",
                    "tool_name": route.tool_call.tool_name,
                }
            )
        if task_control.weight_level in {"high", "urgent", "critical"}:
            hints.append(
                {
                    "type": "degradation_hint",
                    "strategy": "approval_or_manual_review",
                    "reason": "high_weight_failure",
                    "tool_name": route.tool_call.tool_name,
                    "weight_level": task_control.weight_level,
                }
            )
        if task_control.attempt_count >= MANUAL_REVIEW_ATTEMPT_THRESHOLD:
            hints.append(
                {
                    "type": "degradation_hint",
                    "strategy": "approval_or_manual_review",
                    "reason": "task_attempt_threshold_reached",
                    "tool_name": route.tool_call.tool_name,
                    "attempt_count": task_control.attempt_count,
                }
            )
        return replace(result, context_modifiers=tuple(hints))

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

    def _enforce_permission(
        self,
        route: ToolRoute,
        context: ToolUseContext,
    ) -> ToolResultEnvelope | None:
        """在真实工具执行前经过权限层，必要时进入人工审批。"""
        decision = self.permission_gate.decide(route, context)
        self._record_permission_decision(route, context, decision)
        if decision.behavior == "allow":
            return None
        if decision.behavior == "deny":
            return self._permission_denied_envelope(route, decision, "permission_denied")
        approved = self._request_user_approval(route, context, decision)
        if approved:
            self._record_audit(
                context,
                "permission_approved",
                f"tool={route.tool_call.tool_name}; mode={decision.mode}; reason={decision.reason}",
            )
            return None
        self._record_audit(
            context,
            "permission_rejected",
            f"tool={route.tool_call.tool_name}; mode={decision.mode}; reason={decision.reason}",
        )
        return self._permission_denied_envelope(route, decision, "approval_rejected")

    def _request_user_approval(
        self,
        route: ToolRoute,
        context: ToolUseContext,
        decision: PermissionDecision,
    ) -> bool:
        """通过上下文中的审批回调询问用户是否继续执行。"""
        requester = context.approval_requester
        if requester is None:
            return False
        try:
            return bool(
                requester(
                    route.tool_call.tool_name,
                    decision.reason,
                    route.tool_call.tool_input,
                )
            )
        except Exception as exc:  # noqa: BLE001
            self._record_audit(
                context,
                "permission_prompt_failed",
                f"tool={route.tool_call.tool_name}; error={exc}",
            )
            return False

    def _permission_denied_envelope(
        self,
        route: ToolRoute,
        decision: PermissionDecision,
        error_kind: str,
    ) -> ToolResultEnvelope:
        """把权限拒绝或审批拒绝封装为统一错误结果。"""
        call = route.tool_call
        if error_kind == "approval_rejected":
            content = "manual approval rejected: " + decision.reason
        else:
            content = "permission denied: " + decision.reason
        result = error_envelope(call, error_kind, content)
        modifier = {
            "type": "permission_decision",
            "mode": decision.mode,
            "behavior": decision.behavior,
            "reason": decision.reason,
            "tool_name": call.tool_name,
        }
        return replace(result, context_modifiers=(modifier,))

    def _record_permission_decision(
        self,
        route: ToolRoute,
        context: ToolUseContext,
        decision: PermissionDecision,
    ) -> None:
        """把权限层决定写入审计日志，便于后续追踪。"""
        self._record_audit(
            context,
            "permission_decision",
            (
                f"tool={route.tool_call.tool_name}; "
                f"mode={decision.mode}; "
                f"behavior={decision.behavior}; "
                f"reason={decision.reason}"
            ),
        )

    def _record_audit(
        self,
        context: ToolUseContext,
        event_type: str,
        note: str,
    ) -> None:
        """通过上下文中提供的审计接口记录执行层事件。"""
        logger = context.audit_logger
        if logger is None:
            return
        logger.record(
            event_type=event_type,
            note=note,
            task_id=context.agent_state.task_control.task_id,
        )


def _self_test() -> None:
    """验证空 route 执行返回空结果。"""
    registry = ToolRegistry()
    state = create_initial_agent_state("query", "hello")
    state_context = ToolUseContext("query", Path.cwd(), state, registry)
    assert ToolExecutor(registry).execute_routes((), state_context) == ()


if __name__ == "__main__":
    _self_test()
    print("dutyflow tool executor self-test passed")
