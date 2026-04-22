# 本文件负责 Step 2.4 的最小多轮调试 loop，不代表最终生产 agent loop。

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping, Sequence
from uuid import uuid4

from dutyflow.agent.model_client import ModelClient
from dutyflow.agent.recovery import (
    RecoveryDecision,
    RecoveryEvent,
    RecoveryManager,
    RecoveryRestartDescriptor,
)
from dutyflow.agent.state import (
    AgentContentBlock,
    AgentState,
    append_user_message,
    append_assistant_message,
    append_tool_results,
    create_initial_agent_state,
    increment_turn,
    mark_transition,
    record_recovery_attempt,
    resolve_recovery_scope,
    to_dict,
    upsert_recovery_scope,
)
from dutyflow.agent.tools.context import ToolUseContext
from dutyflow.agent.tools.executor import ToolExecutor
from dutyflow.agent.tools.registry import ToolRegistry
from dutyflow.agent.tools.router import ToolRouter
from dutyflow.agent.tools.types import ToolCall, ToolResultEnvelope


@dataclass(frozen=True)
class AgentLoopResult:
    """保存 /chat 调试 loop 的完整可见结果。"""

    state: AgentState
    final_text: str
    stop_reason: str
    turn_count: int
    tool_results: tuple[ToolResultEnvelope, ...]
    pending_restarts: tuple[RecoveryRestartDescriptor, ...] = ()

    @property
    def tool_result_count(self) -> int:
        """返回工具结果数量。"""
        return len(self.tool_results)

    def to_debug_text(self) -> str:
        """返回 CLI 可打印的完整调试文本。"""
        payload = {
            "final_text": self.final_text,
            "stop_reason": self.stop_reason,
            "turn_count": self.turn_count,
            "tool_result_count": self.tool_result_count,
            "tool_results": [_tool_result_to_dict(item) for item in self.tool_results],
            "pending_restarts": [_restart_descriptor_to_dict(item) for item in self.pending_restarts],
            "agent_state": to_dict(self.state),
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)


@dataclass
class ChatDebugSession:
    """维护 CLI /chat 子会话中的持续 Agent State。"""

    loop: "AgentLoop"
    state: AgentState | None = None

    def run_turn(self, user_text: str) -> AgentLoopResult:
        """执行 chat 子会话的一轮用户输入并更新当前状态。"""
        result = self.loop.run_until_stop(user_text, state=self.state)
        self.state = result.state
        return result


class AgentLoop:
    """执行基于 Agent State 和工具控制层的最小多轮调试链路。"""

    def __init__(
        self,
        model_client: ModelClient,
        registry: ToolRegistry,
        cwd: Path,
        max_turns: int = 6,
        permission_mode: str = "default",
        approval_requester=None,
        audit_logger=None,
        recovery_manager: RecoveryManager | None = None,
        max_model_recovery_attempts: int = 3,
    ) -> None:
        """绑定模型客户端、工具注册表和运行目录。"""
        self.model_client = model_client
        self.registry = registry
        self.router = ToolRouter(registry)
        self.recovery_manager = recovery_manager or RecoveryManager()
        self.executor = ToolExecutor(registry, recovery_manager=self.recovery_manager)
        self.cwd = cwd
        self.permission_mode = permission_mode
        self.approval_requester = approval_requester
        self.audit_logger = audit_logger
        # 关键开关：CLI /chat 调试链路允许的最大工具续转轮数；超过后直接停止，防止无限循环。
        self.max_turns = max_turns
        # 关键开关：单轮模型调用在当前进程内允许的最大恢复次数；当前默认最多 3 次。
        self.max_model_recovery_attempts = max_model_recovery_attempts

    def run_until_stop(
        self,
        user_text: str,
        query_id: str | None = None,
        tool_content: Mapping[str, Any] | None = None,
        state: AgentState | None = None,
    ) -> AgentLoopResult:
        """运行一轮 /chat 调试输入，直到模型停止或达到轮数限制。"""
        state = self._prepare_state(user_text, query_id, state)
        tool_results: list[ToolResultEnvelope] = []
        local_turns = 0
        model_transport_attempts = 0
        active_recovery_ids: list[str] = []
        while True:
            try:
                response = self.model_client.call_model(state, self.registry.list_specs())
            except Exception as exc:  # noqa: BLE001
                failure_kind = self._classify_model_failure(exc)
                state, decision, recovery_id = self._register_model_recovery(
                    state=state,
                    failure_kind=failure_kind,
                    scope_id=f"turn_{state.turn_count}",
                    attempt_count=model_transport_attempts + 1,
                    max_attempts=self.max_model_recovery_attempts,
                    error_message=str(exc),
                    retryable=True,
                )
                active_recovery_ids.append(recovery_id)
                if decision.strategy == "retry_now":
                    model_transport_attempts += 1
                    if model_transport_attempts > self.max_model_recovery_attempts:
                        state = self._finalize_recovery_ids(state, active_recovery_ids, "exhausted", str(exc))
                        return _failed_result(
                            state,
                            failure_kind,
                            tool_results,
                            self._pending_restart_descriptions(state),
                        )
                    state = mark_transition(state, "transport_retry")
                    continue
                return _failed_result(
                    state,
                    failure_kind,
                    tool_results,
                    self._pending_restart_descriptions(state),
                )
            if active_recovery_ids:
                state = self._finalize_recovery_ids(state, active_recovery_ids, "resolved", "model call recovered")
                active_recovery_ids.clear()
                model_transport_attempts = 0
            state = append_assistant_message(state, response.assistant_blocks)
            if response.stop_reason == "max_tokens":
                state, _, recovery_id = self._register_model_recovery(
                    state=state,
                    failure_kind="model_max_tokens",
                    scope_id=f"turn_{state.turn_count}",
                    attempt_count=state.recovery.continuation_attempts + 1,
                    max_attempts=self.max_model_recovery_attempts,
                    error_message="model output truncated at max_tokens",
                    retryable=True,
                )
                active_recovery_ids.append(recovery_id)
                state = append_user_message(state, _continuation_message())
                state = increment_turn(mark_transition(state, "max_tokens_recovery"))
                continue
            tool_calls = extract_tool_calls(state)
            if not tool_calls:
                return _finish_result(
                    state,
                    _final_text(response.assistant_blocks),
                    response.stop_reason,
                    tool_results,
                    self._pending_restart_descriptions(state),
                )
            local_turns += 1
            if local_turns >= self.max_turns:
                return _failed_result(
                    state,
                    "max_turns_reached",
                    tool_results,
                    self._pending_restart_descriptions(state),
                )
            state, envelopes = self._execute_tool_calls(state, tool_calls, tool_content or {})
            tool_results.extend(envelopes)
            state = append_tool_results(state, tuple(item.to_agent_block() for item in envelopes))

    def _prepare_state(
        self,
        user_text: str,
        query_id: str | None,
        state: AgentState | None,
    ) -> AgentState:
        """创建或复用 Agent State，并追加本轮用户输入。"""
        if state is None:
            prepared = create_initial_agent_state(query_id or _new_query_id(), user_text)
        else:
            prepared = append_user_message(state, user_text)
            prepared = replace(prepared, turn_count=prepared.turn_count + 1)
            prepared = mark_transition(prepared, "user_continuation")
        # 关键开关：把本次 loop 允许追加的最大轮数写回 AgentState，供状态层统一兜底校验。
        return replace(prepared, max_turns=prepared.turn_count + self.max_turns)

    def _execute_tool_calls(
        self,
        state: AgentState,
        tool_calls: Sequence[ToolCall],
        tool_content: Mapping[str, Any],
    ) -> tuple[AgentState, tuple[ToolResultEnvelope, ...]]:
        """通过 Router 和 Executor 执行工具调用。"""
        routes = self.router.route_many(tuple(tool_calls))
        context = ToolUseContext(
            query_id=state.query_id,
            cwd=self.cwd,
            agent_state=state,
            registry=self.registry,
            permission_mode=self.permission_mode,
            approval_requester=self.approval_requester,
            audit_logger=self.audit_logger,
            tool_content=tool_content,
        )
        envelopes = self.executor.execute_routes(routes, context)
        return context.agent_state, envelopes

    def _register_model_recovery(
        self,
        state: AgentState,
        failure_kind: str,
        scope_id: str,
        attempt_count: int,
        max_attempts: int,
        error_message: str,
        retryable: bool,
    ) -> tuple[AgentState, RecoveryDecision, str]:
        """把模型中断注册为恢复事件，并回写到 AgentState。"""
        event = RecoveryEvent(
            scope_type="turn",
            scope_id=scope_id,
            failure_kind=failure_kind,
            attempt_count=attempt_count,
            max_attempts=max_attempts,
            error_message=error_message,
            retryable=retryable,
            metadata={"query_id": state.query_id, "turn_count": state.turn_count},
        )
        decision = self.recovery_manager.decide(event)
        state = record_recovery_attempt(
            state,
            failure_kind,
            interruption_reason=decision.interruption_reason,
            resume_point=decision.resume_point,
        )
        recovery_id = self._new_recovery_id(event)
        scope = self.recovery_manager.create_scope(recovery_id, event, decision)
        state = upsert_recovery_scope(state, scope)
        return state, decision, recovery_id

    def _finalize_recovery_ids(
        self,
        state: AgentState,
        recovery_ids: Sequence[str],
        status: str,
        last_error: str,
    ) -> AgentState:
        """把一组活动中的恢复 scope 批量标记为最终状态。"""
        updated = state
        for recovery_id in recovery_ids:
            updated = resolve_recovery_scope(updated, recovery_id, status=status, last_error=last_error)
        return updated

    def _classify_model_failure(self, exc: Exception) -> str:
        """把模型异常映射为恢复层 failure_kind。"""
        message = str(exc).lower()
        if "context" in message or "prompt" in message:
            return "context_overflow"
        return "model_transport_error"

    def _new_recovery_id(self, event: RecoveryEvent) -> str:
        """生成模型恢复 scope 的本地 ID。"""
        return "_".join(("rec", event.scope_type, event.scope_id, event.failure_kind, uuid4().hex[:8]))

    def _pending_restart_descriptions(
        self,
        state: AgentState,
    ) -> tuple[RecoveryRestartDescriptor, ...]:
        """返回当前 AgentState 中仍处于挂起中的 restart 描述。"""
        return self.recovery_manager.collect_restart_descriptions(state.recovery.recovery_scopes)


def extract_tool_calls(state: AgentState) -> tuple[ToolCall, ...]:
    """从最后一条 assistant 消息中提取 ToolCall。"""
    message_index = len(state.messages) - 1
    message = state.messages[message_index]
    calls: list[ToolCall] = []
    for block_index, block in enumerate(message.content):
        if block.type == "tool_use":
            calls.append(ToolCall.from_agent_block(block, message_index, block_index))
    return tuple(calls)


def _finish_result(
    state: AgentState,
    final_text: str,
    stop_reason: str,
    tool_results: Sequence[ToolResultEnvelope],
    pending_restarts: Sequence[RecoveryRestartDescriptor],
) -> AgentLoopResult:
    """生成成功结束结果。"""
    finished = mark_transition(state, "finished")
    return AgentLoopResult(
        finished,
        final_text,
        stop_reason or "stop",
        finished.turn_count,
        tuple(tool_results),
        tuple(pending_restarts),
    )


def _failed_result(
    state: AgentState,
    reason: str,
    tool_results: Sequence[ToolResultEnvelope],
    pending_restarts: Sequence[RecoveryRestartDescriptor],
) -> AgentLoopResult:
    """生成失败结束结果。"""
    failed = mark_transition(state, "failed")
    return AgentLoopResult(
        failed,
        reason,
        reason,
        failed.turn_count,
        tuple(tool_results),
        tuple(pending_restarts),
    )


def _final_text(blocks: Sequence[AgentContentBlock]) -> str:
    """提取 assistant 文本结果。"""
    texts = [block.text for block in blocks if block.type == "text" and block.text]
    return "\n".join(texts)


def _tool_result_to_dict(result: ToolResultEnvelope) -> dict[str, Any]:
    """把工具结果信封转换为调试字典。"""
    return {
        "tool_use_id": result.tool_use_id,
        "tool_name": result.tool_name,
        "ok": result.ok,
        "content": result.content,
        "is_error": result.is_error,
        "error_kind": result.error_kind,
        "call_index": result.call_index,
        "attempt_count": result.attempt_count,
        "retryable": result.retryable,
        "retry_exhausted": result.retry_exhausted,
        "context_modifiers": [dict(item) for item in result.context_modifiers],
    }


def _restart_descriptor_to_dict(descriptor: RecoveryRestartDescriptor) -> dict[str, Any]:
    """把 restart 描述转换为 CLI 调试可读字典。"""
    return {
        "recovery_id": descriptor.recovery_id,
        "resume_token": descriptor.resume_token,
        "scope_type": descriptor.scope_type,
        "scope_id": descriptor.scope_id,
        "status": descriptor.status,
        "interruption_reason": descriptor.interruption_reason,
        "resume_point": descriptor.resume_point,
        "restart_action": descriptor.restart_action,
        "can_restart_now": descriptor.can_restart_now,
        "next_retry_at": descriptor.next_retry_at,
    }


def _new_query_id() -> str:
    """生成本地调试 query id。"""
    return "chat_" + uuid4().hex


def _continuation_message() -> str:
    """返回模型输出截断后的继续提示。"""
    return "Output limit hit. Continue directly from where you stopped."


def _self_test() -> None:
    """验证 extract_tool_calls 能读取最后一条 assistant 消息。"""
    state = create_initial_agent_state("query_loop", "hello")
    block = AgentContentBlock(type="tool_use", tool_use_id="tool_1", tool_name="echo_text")
    state = append_assistant_message(state, (block,))
    assert extract_tool_calls(state)[0].tool_name == "echo_text"


if __name__ == "__main__":
    _self_test()
    print("dutyflow agent loop self-test passed")
