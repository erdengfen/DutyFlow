# 本文件负责维护 agent loop 内部的多轮运行状态，不读写本地快照文件。

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from dutyflow.agent.recovery import (
    RECOVERY_INTERRUPTION_REASONS,
    RECOVERY_RESUME_POINTS,
    RecoveryScope,
)

ALLOWED_BLOCK_TYPES = frozenset({"text", "tool_use", "tool_result", "placeholder"})
ALLOWED_ROLES = frozenset({"user", "assistant", "system"})
APPROVAL_STATUSES = frozenset({"none", "waiting", "approved", "rejected", "deferred"})
RETRY_STATUSES = frozenset({"none", "retrying", "exhausted"})
TRANSITION_REASONS = frozenset(
    {
        "start",
        "tool_result_continuation",
        "max_tokens_recovery",
        "compact_retry",
        "emergency_compact_retry",
        "transport_retry",
        "stop_hook_continuation",
        "user_continuation",
        "finished",
        "failed",
    }
)
RECOVERY_ATTEMPT_FAILURE_KINDS = {
    "model_max_tokens": "continuation_attempts",
    "context_overflow": "compact_attempts",
    "context_compaction_failed": "compact_attempts",
    "model_transport_error": "transport_attempts",
    "tool_timeout": "tool_error_attempts",
    "tool_transient_error": "tool_error_attempts",
    "tool_retry_exhausted": "tool_error_attempts",
    "tool_side_effect_uncertain": "tool_error_attempts",
    "permission_denied": "tool_error_attempts",
    "approval_waiting": "tool_error_attempts",
    "approval_rejected": "tool_error_attempts",
    "feedback_delivery_failed": "tool_error_attempts",
    "persistence_write_failed": "tool_error_attempts",
}
TASK_CONTROL_ATTEMPT_FAILURE_KINDS = frozenset(RECOVERY_ATTEMPT_FAILURE_KINDS) - {"approval_rejected"}
TASK_CONTROL_RETRYING_FAILURE_KINDS = frozenset(
    {
        "model_max_tokens",
        "model_transport_error",
        "context_overflow",
        "tool_timeout",
        "tool_transient_error",
        "feedback_delivery_failed",
        "persistence_write_failed",
    }
)
TASK_CONTROL_EXHAUSTED_FAILURE_KINDS = frozenset(
    {
        "tool_retry_exhausted",
        "tool_side_effect_uncertain",
    }
)
TASK_CONTROL_RECOVERABLE_FAILURE_KINDS = TASK_CONTROL_RETRYING_FAILURE_KINDS | TASK_CONTROL_EXHAUSTED_FAILURE_KINDS


def _now() -> str:
    """返回 UTC ISO 时间，用于运行状态更新时间。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class AgentContentBlock:
    """表示模型消息中的文本、工具调用或工具结果块。"""

    type: str
    text: str = ""
    tool_use_id: str = ""
    tool_name: str = ""
    tool_input: Mapping[str, Any] = field(default_factory=dict)
    content: str = ""
    is_error: bool = False


@dataclass(frozen=True)
class AgentMessage:
    """表示 agent loop 中可送入下一轮模型调用的消息。"""

    role: str
    content: tuple[AgentContentBlock, ...]


@dataclass(frozen=True)
class AgentTaskControl:
    """记录当前任务相关的权重、审批和重试控制信息。"""

    task_id: str = ""
    weight_level: str = ""
    # 关键计数：记录当前任务已尝试次数，供后续硬规则或审批升级使用。
    attempt_count: int = 0
    approval_status: str = "none"
    retry_status: str = "none"
    next_action: str = ""


@dataclass(frozen=True)
class AgentRecoveryState:
    """记录主循环错误恢复路径的尝试次数。"""

    # 关键计数：以下字段当前只做状态留痕，不代表对应重试策略已经实现。
    continuation_attempts: int = 0
    compact_attempts: int = 0
    transport_attempts: int = 0
    tool_error_attempts: int = 0
    latest_interruption_reason: str = ""
    latest_resume_point: str = ""
    recovery_scopes: tuple[RecoveryScope, ...] = ()


@dataclass(frozen=True)
class AgentState:
    """保存一条 query 在多轮 agent loop 中持续更新的运行状态。"""

    query_id: str
    messages: tuple[AgentMessage, ...]
    # 关键计数：AgentState 当前已进入的轮数，从 1 开始累计。
    turn_count: int = 1
    transition_reason: str = "start"
    current_event_id: str = ""
    current_task_id: str = ""
    pending_tool_use_ids: tuple[str, ...] = ()
    last_tool_result_ids: tuple[str, ...] = ()
    task_control: AgentTaskControl = field(default_factory=AgentTaskControl)
    recovery: AgentRecoveryState = field(default_factory=AgentRecoveryState)
    # 关键开关：状态层允许的最大轮数上限；超过后由状态校验直接拒绝继续推进。
    max_turns: int = 20
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)


def create_initial_agent_state(
    query_id: str,
    user_text: str,
    current_event_id: str = "",
) -> AgentState:
    """创建一条 query 的初始 Agent State。"""
    message = AgentMessage(
        role="user",
        content=(AgentContentBlock(type="text", text=user_text),),
    )
    state = AgentState(
        query_id=query_id,
        messages=(message,),
        current_event_id=current_event_id,
    )
    return validate_agent_state(state)


def append_user_message(state: AgentState, text: str) -> AgentState:
    """向 Agent State 追加用户消息并返回新状态。"""
    message = AgentMessage(
        role="user",
        content=(AgentContentBlock(type="text", text=text),),
    )
    return validate_agent_state(_append_message(state, message))


def append_assistant_message(
    state: AgentState,
    blocks: Sequence[AgentContentBlock],
) -> AgentState:
    """追加 assistant 消息，并记录其中未完成的工具调用。"""
    normalized = _normalize_blocks(blocks)
    pending_ids = _pending_ids_from_blocks(normalized)
    message = AgentMessage(role="assistant", content=normalized)
    updated = _append_message(state, message)
    updated = replace(
        updated,
        pending_tool_use_ids=state.pending_tool_use_ids + pending_ids,
    )
    return validate_agent_state(updated)


def append_tool_results(
    state: AgentState,
    results: Sequence[AgentContentBlock],
) -> AgentState:
    """把工具结果写回消息流，并推进到下一轮。"""
    normalized = _normalize_blocks(results)
    _validate_tool_results(state, normalized)
    result_ids = tuple(block.tool_use_id for block in normalized)
    message = AgentMessage(role="user", content=normalized)
    updated = _append_message(state, message)
    updated = replace(
        updated,
        pending_tool_use_ids=_remove_pending_ids(state, result_ids),
        last_tool_result_ids=result_ids,
    )
    return increment_turn(mark_transition(updated, "tool_result_continuation"))


def mark_transition(state: AgentState, reason: str) -> AgentState:
    """记录当前 loop 继续或结束的明确原因。"""
    if reason not in TRANSITION_REASONS:
        raise ValueError(f"Unknown transition reason: {reason}")
    return validate_agent_state(replace(state, transition_reason=reason, updated_at=_now()))


def increment_turn(state: AgentState) -> AgentState:
    """进入下一轮时增加 turn_count。"""
    next_turn = state.turn_count + 1
    if state.max_turns and next_turn > state.max_turns:
        raise ValueError("Agent State exceeded max_turns")
    return validate_agent_state(replace(state, turn_count=next_turn, updated_at=_now()))


def record_recovery_attempt(
    state: AgentState,
    failure_kind: str,
    interruption_reason: str = "",
    resume_point: str = "",
) -> AgentState:
    """记录一次恢复尝试，并同步更新聚合计数。"""
    counter_name = RECOVERY_ATTEMPT_FAILURE_KINDS.get(failure_kind)
    if counter_name is None:
        raise ValueError(f"Unknown recovery failure kind: {failure_kind}")
    current_value = getattr(state.recovery, counter_name)
    recovery = replace(
        state.recovery,
        **{
            counter_name: current_value + 1,
            "latest_interruption_reason": interruption_reason,
            "latest_resume_point": resume_point,
        },
    )
    task_control = _task_control_for_recovery_attempt(
        state.task_control,
        failure_kind,
        interruption_reason,
        resume_point,
    )
    return validate_agent_state(
        replace(state, recovery=recovery, task_control=task_control, updated_at=_now())
    )


def upsert_recovery_scope(
    state: AgentState,
    scope: RecoveryScope,
) -> AgentState:
    """新增或更新一个 scope 级恢复记录。"""
    scopes = [item for item in state.recovery.recovery_scopes if item.recovery_id != scope.recovery_id]
    scopes.append(scope)
    recovery = replace(
        state.recovery,
        latest_interruption_reason=scope.interruption_reason,
        latest_resume_point=scope.resume_point,
        recovery_scopes=tuple(scopes),
    )
    return validate_agent_state(replace(state, recovery=recovery, updated_at=_now()))


def resolve_recovery_scope(
    state: AgentState,
    recovery_id: str,
    status: str = "resolved",
    last_error: str = "",
) -> AgentState:
    """将指定 recovery scope 标记为已解决或已耗尽。"""
    scopes: list[RecoveryScope] = []
    found = False
    resolved_scope: RecoveryScope | None = None
    for scope in state.recovery.recovery_scopes:
        if scope.recovery_id != recovery_id:
            scopes.append(scope)
            continue
        found = True
        resolved_scope = replace(
            scope,
            status=status,
            last_error=last_error or scope.last_error,
            updated_at=_now(),
        )
        scopes.append(resolved_scope)
    if not found:
        raise ValueError(f"Unknown recovery_id: {recovery_id}")
    recovery = replace(state.recovery, recovery_scopes=tuple(scopes))
    task_control = _task_control_for_scope_resolution(
        state.task_control,
        resolved_scope,
        status,
    )
    return validate_agent_state(
        replace(state, recovery=recovery, task_control=task_control, updated_at=_now())
    )


def validate_agent_state(state: AgentState) -> AgentState:
    """验证 Agent State 的核心不变量。"""
    _validate_state_header(state)
    _validate_task_control(state.task_control)
    for message in state.messages:
        _validate_message(message)
    _validate_pending_ids(state.pending_tool_use_ids)
    _validate_recovery_state(state.recovery)
    return state


def to_dict(state: AgentState) -> dict[str, Any]:
    """把 Agent State 转换为可测试和未来恢复使用的字典。"""
    return {
        "query_id": state.query_id,
        "messages": [_message_to_dict(message) for message in state.messages],
        "turn_count": state.turn_count,
        "transition_reason": state.transition_reason,
        "current_event_id": state.current_event_id,
        "current_task_id": state.current_task_id,
        "pending_tool_use_ids": list(state.pending_tool_use_ids),
        "last_tool_result_ids": list(state.last_tool_result_ids),
        "task_control": _task_control_to_dict(state.task_control),
        "recovery": _recovery_to_dict(state.recovery),
        "max_turns": state.max_turns,
        "created_at": state.created_at,
        "updated_at": state.updated_at,
    }


def from_dict(payload: Mapping[str, Any]) -> AgentState:
    """从字典恢复 Agent State，不从磁盘读取任何快照文件。"""
    messages = tuple(_message_from_dict(item) for item in payload.get("messages", ()))
    state = AgentState(
        query_id=str(payload.get("query_id", "")),
        messages=messages,
        turn_count=int(payload.get("turn_count", 1)),
        transition_reason=str(payload.get("transition_reason", "start")),
        current_event_id=str(payload.get("current_event_id", "")),
        current_task_id=str(payload.get("current_task_id", "")),
        pending_tool_use_ids=tuple(payload.get("pending_tool_use_ids", ())),
        last_tool_result_ids=tuple(payload.get("last_tool_result_ids", ())),
        task_control=_task_control_from_dict(payload.get("task_control", {})),
        recovery=_recovery_from_dict(payload.get("recovery", {})),
        max_turns=int(payload.get("max_turns", 20)),
        created_at=str(payload.get("created_at", _now())),
        updated_at=str(payload.get("updated_at", _now())),
    )
    return validate_agent_state(state)


def save_agent_state(state: AgentState) -> dict[str, Any]:
    """返回 Agent State 的序列化结果，不执行磁盘写入。"""
    return to_dict(state)


def load_agent_state(payload: Mapping[str, Any]) -> AgentState:
    """从序列化结果加载 Agent State，不读取本地运行快照。"""
    return from_dict(payload)


def _append_message(state: AgentState, message: AgentMessage) -> AgentState:
    """追加消息并刷新更新时间。"""
    return replace(state, messages=state.messages + (message,), updated_at=_now())


def _normalize_blocks(blocks: Sequence[AgentContentBlock]) -> tuple[AgentContentBlock, ...]:
    """将消息块序列转换为不可变结构并校验非空。"""
    normalized = tuple(blocks)
    if not normalized:
        raise ValueError("Agent message content cannot be empty")
    for block in normalized:
        _validate_block(block)
    return normalized


def _pending_ids_from_blocks(blocks: Sequence[AgentContentBlock]) -> tuple[str, ...]:
    """从 assistant 消息块中提取待完成工具调用 ID。"""
    ids: list[str] = []
    for block in blocks:
        if block.type == "tool_use":
            ids.append(block.tool_use_id)
    return tuple(ids)


def _validate_tool_results(
    state: AgentState,
    blocks: Sequence[AgentContentBlock],
) -> None:
    """验证工具结果都能匹配未完成的工具调用。"""
    pending = set(state.pending_tool_use_ids)
    for block in blocks:
        if block.type != "tool_result":
            raise ValueError("append_tool_results only accepts tool_result blocks")
        if not block.tool_use_id:
            raise ValueError("tool_result requires tool_use_id")
        if block.tool_use_id not in pending:
            raise ValueError(f"Unknown tool_use_id: {block.tool_use_id}")


def _remove_pending_ids(state: AgentState, result_ids: Sequence[str]) -> tuple[str, ...]:
    """从待完成工具调用中移除已有结果的 ID。"""
    done = set(result_ids)
    return tuple(item for item in state.pending_tool_use_ids if item not in done)


def _validate_state_header(state: AgentState) -> None:
    """验证 Agent State 顶层控制字段。"""
    if not state.query_id:
        raise ValueError("query_id is required")
    if state.turn_count < 1:
        raise ValueError("turn_count must be >= 1")
    if state.transition_reason not in TRANSITION_REASONS:
        raise ValueError("transition_reason is invalid")
    if not state.messages:
        raise ValueError("messages cannot be empty")


def _validate_message(message: AgentMessage) -> None:
    """验证单条消息的角色和内容块。"""
    if message.role not in ALLOWED_ROLES:
        raise ValueError(f"Unknown message role: {message.role}")
    if not message.content:
        raise ValueError("message content cannot be empty")
    for block in message.content:
        _validate_block(block)


def _validate_task_control(task_control: AgentTaskControl) -> None:
    """验证任务控制状态中的稳定枚举字段。"""
    if task_control.attempt_count < 0:
        raise ValueError("task_control.attempt_count must be >= 0")
    if task_control.approval_status not in APPROVAL_STATUSES:
        raise ValueError("task_control.approval_status is invalid")
    if task_control.retry_status not in RETRY_STATUSES:
        raise ValueError("task_control.retry_status is invalid")


def _validate_block(block: AgentContentBlock) -> None:
    """验证消息块的类型和工具 ID 约束。"""
    if block.type not in ALLOWED_BLOCK_TYPES:
        raise ValueError(f"Unknown block type: {block.type}")
    if block.type in {"tool_use", "tool_result"} and not block.tool_use_id:
        raise ValueError(f"{block.type} requires tool_use_id")


def _validate_pending_ids(ids: Sequence[str]) -> None:
    """验证待完成工具调用 ID 不重复。"""
    if len(tuple(ids)) != len(set(ids)):
        raise ValueError("pending_tool_use_ids cannot contain duplicates")


def _message_to_dict(message: AgentMessage) -> dict[str, Any]:
    """序列化单条消息。"""
    return {
        "role": message.role,
        "content": [_block_to_dict(block) for block in message.content],
    }


def _message_from_dict(payload: Mapping[str, Any]) -> AgentMessage:
    """从字典恢复单条消息。"""
    blocks = tuple(_block_from_dict(item) for item in payload.get("content", ()))
    return AgentMessage(role=str(payload.get("role", "")), content=blocks)


def _block_to_dict(block: AgentContentBlock) -> dict[str, Any]:
    """序列化消息内容块。"""
    return {
        "type": block.type,
        "text": block.text,
        "tool_use_id": block.tool_use_id,
        "tool_name": block.tool_name,
        "tool_input": dict(block.tool_input),
        "content": block.content,
        "is_error": block.is_error,
    }


def _block_from_dict(payload: Mapping[str, Any]) -> AgentContentBlock:
    """从字典恢复消息内容块。"""
    return AgentContentBlock(
        type=str(payload.get("type", "")),
        text=str(payload.get("text", "")),
        tool_use_id=str(payload.get("tool_use_id", "")),
        tool_name=str(payload.get("tool_name", "")),
        tool_input=dict(payload.get("tool_input", {})),
        content=str(payload.get("content", "")),
        is_error=bool(payload.get("is_error", False)),
    )


def _task_control_to_dict(task_control: AgentTaskControl) -> dict[str, Any]:
    """序列化任务控制状态。"""
    return {
        "task_id": task_control.task_id,
        "weight_level": task_control.weight_level,
        "attempt_count": task_control.attempt_count,
        "approval_status": task_control.approval_status,
        "retry_status": task_control.retry_status,
        "next_action": task_control.next_action,
    }


def _task_control_from_dict(payload: Mapping[str, Any]) -> AgentTaskControl:
    """从字典恢复任务控制状态。"""
    return AgentTaskControl(
        task_id=str(payload.get("task_id", "")),
        weight_level=str(payload.get("weight_level", "")),
        attempt_count=int(payload.get("attempt_count", 0)),
        approval_status=str(payload.get("approval_status", "none")),
        retry_status=str(payload.get("retry_status", "none")),
        next_action=str(payload.get("next_action", "")),
    )


def _recovery_to_dict(recovery: AgentRecoveryState) -> dict[str, Any]:
    """序列化恢复计数。"""
    return {
        "continuation_attempts": recovery.continuation_attempts,
        "compact_attempts": recovery.compact_attempts,
        "transport_attempts": recovery.transport_attempts,
        "tool_error_attempts": recovery.tool_error_attempts,
        "latest_interruption_reason": recovery.latest_interruption_reason,
        "latest_resume_point": recovery.latest_resume_point,
        "recovery_scopes": [_recovery_scope_to_dict(item) for item in recovery.recovery_scopes],
    }


def _recovery_from_dict(payload: Mapping[str, Any]) -> AgentRecoveryState:
    """从字典恢复错误恢复计数。"""
    return AgentRecoveryState(
        continuation_attempts=int(payload.get("continuation_attempts", 0)),
        compact_attempts=int(payload.get("compact_attempts", 0)),
        transport_attempts=int(payload.get("transport_attempts", 0)),
        tool_error_attempts=int(payload.get("tool_error_attempts", 0)),
        latest_interruption_reason=str(payload.get("latest_interruption_reason", "")),
        latest_resume_point=str(payload.get("latest_resume_point", "")),
        recovery_scopes=tuple(
            _recovery_scope_from_dict(item) for item in payload.get("recovery_scopes", ())
        ),
    )


def _validate_recovery_state(recovery: AgentRecoveryState) -> None:
    """验证恢复状态中的聚合字段和 scope 记录。"""
    for field_name in (
        "continuation_attempts",
        "compact_attempts",
        "transport_attempts",
        "tool_error_attempts",
    ):
        if getattr(recovery, field_name) < 0:
            raise ValueError(f"{field_name} must be >= 0")
    if (
        recovery.latest_interruption_reason
        and recovery.latest_interruption_reason not in RECOVERY_INTERRUPTION_REASONS
    ):
        raise ValueError("latest_interruption_reason is invalid")
    if recovery.latest_resume_point and recovery.latest_resume_point not in RECOVERY_RESUME_POINTS:
        raise ValueError("latest_resume_point is invalid")
    _validate_recovery_scope_ids(recovery.recovery_scopes)


def _validate_recovery_scope_ids(scopes: Sequence[RecoveryScope]) -> None:
    """验证 recovery scope 的稳定 ID 不重复。"""
    ids = tuple(scope.recovery_id for scope in scopes)
    if len(ids) != len(set(ids)):
        raise ValueError("recovery_scopes cannot contain duplicate recovery_id")


def _task_control_for_recovery_attempt(
    task_control: AgentTaskControl,
    failure_kind: str,
    interruption_reason: str,
    resume_point: str,
) -> AgentTaskControl:
    """根据恢复事件摘要回写任务控制字段。"""
    return replace(
        task_control,
        attempt_count=_attempt_count_for_failure(task_control, failure_kind),
        approval_status=_approval_status_for_recovery_attempt(task_control, failure_kind),
        retry_status=_retry_status_for_recovery_attempt(task_control, failure_kind),
        next_action=_next_action_for_recovery_attempt(failure_kind, interruption_reason, resume_point),
    )


def _task_control_for_scope_resolution(
    task_control: AgentTaskControl,
    scope: RecoveryScope | None,
    status: str,
) -> AgentTaskControl:
    """根据恢复 scope 的终态刷新任务控制摘要。"""
    if scope is None:
        return task_control
    if status == "resolved":
        return _task_control_for_resolved_scope(task_control, scope)
    if status == "exhausted":
        return _task_control_for_exhausted_scope(task_control, scope)
    return task_control


def _attempt_count_for_failure(task_control: AgentTaskControl, failure_kind: str) -> int:
    """返回当前失败类型应写回的任务尝试次数。"""
    if failure_kind not in TASK_CONTROL_ATTEMPT_FAILURE_KINDS:
        return task_control.attempt_count
    return task_control.attempt_count + 1


def _approval_status_for_recovery_attempt(
    task_control: AgentTaskControl,
    failure_kind: str,
) -> str:
    """根据恢复事件返回新的审批摘要状态。"""
    if failure_kind == "approval_waiting":
        return "waiting"
    if failure_kind == "approval_rejected":
        return "rejected"
    return task_control.approval_status


def _retry_status_for_recovery_attempt(
    task_control: AgentTaskControl,
    failure_kind: str,
) -> str:
    """根据恢复事件返回新的重试摘要状态。"""
    if failure_kind in TASK_CONTROL_RETRYING_FAILURE_KINDS:
        return "retrying"
    if failure_kind in TASK_CONTROL_EXHAUSTED_FAILURE_KINDS:
        return "exhausted"
    return task_control.retry_status


def _next_action_for_recovery_attempt(
    failure_kind: str,
    interruption_reason: str,
    resume_point: str,
) -> str:
    """根据恢复事件生成稳定的下一步动作摘要。"""
    action_map = {
        "approval_waiting": "wait_for_approval",
        "approval_rejected": "report_approval_rejected",
        "permission_denied": "report_permission_denied",
        "model_max_tokens": "continue_model_generation",
        "context_overflow": "compact_context_then_retry",
        "tool_retry_exhausted": "manual_review_required",
        "tool_side_effect_uncertain": "manual_review_required",
        "feedback_delivery_failed": "retry_feedback_delivery",
        "persistence_write_failed": "retry_persistence_write",
    }
    if failure_kind in action_map:
        return action_map[failure_kind]
    if interruption_reason == "wait_next_retry_window" and resume_point == "before_tool_execute":
        return "retry_tool_call_later"
    if interruption_reason == "wait_next_retry_window":
        return "retry_model_call_later"
    if resume_point == "before_tool_execute":
        return "retry_tool_call"
    if resume_point == "before_model_call":
        return "retry_model_call"
    return ""


def _task_control_for_resolved_scope(
    task_control: AgentTaskControl,
    scope: RecoveryScope,
) -> AgentTaskControl:
    """在恢复 scope 成功解决后清理或推进任务控制摘要。"""
    if scope.failure_kind == "approval_waiting":
        return replace(
            task_control,
            approval_status="approved",
            retry_status="none",
            next_action="resume_after_approval",
        )
    if scope.failure_kind in TASK_CONTROL_RECOVERABLE_FAILURE_KINDS:
        return replace(task_control, retry_status="none", next_action="")
    return task_control


def _task_control_for_exhausted_scope(
    task_control: AgentTaskControl,
    scope: RecoveryScope,
) -> AgentTaskControl:
    """在恢复 scope 耗尽后写回最终的任务控制摘要。"""
    if scope.failure_kind == "approval_waiting":
        return replace(task_control, approval_status="rejected", next_action="report_approval_rejected")
    if scope.failure_kind == "permission_denied":
        return replace(task_control, next_action="report_permission_denied")
    if scope.failure_kind in TASK_CONTROL_RECOVERABLE_FAILURE_KINDS:
        return replace(task_control, retry_status="exhausted", next_action="manual_review_required")
    return task_control


def _recovery_scope_to_dict(scope: RecoveryScope) -> dict[str, Any]:
    """序列化单条恢复 scope 记录。"""
    return {
        "recovery_id": scope.recovery_id,
        "scope_type": scope.scope_type,
        "scope_id": scope.scope_id,
        "status": scope.status,
        "failure_kind": scope.failure_kind,
        "interruption_reason": scope.interruption_reason,
        "strategy": scope.strategy,
        "attempt_count": scope.attempt_count,
        "max_attempts": scope.max_attempts,
        "next_retry_at": scope.next_retry_at,
        "resume_point": scope.resume_point,
        "resume_payload": dict(scope.resume_payload),
        "last_error": scope.last_error,
        "updated_at": scope.updated_at,
    }


def _recovery_scope_from_dict(payload: Mapping[str, Any]) -> RecoveryScope:
    """从字典恢复单条恢复 scope 记录。"""
    return RecoveryScope(
        recovery_id=str(payload.get("recovery_id", "")),
        scope_type=str(payload.get("scope_type", "")),
        scope_id=str(payload.get("scope_id", "")),
        status=str(payload.get("status", "")),
        failure_kind=str(payload.get("failure_kind", "")),
        interruption_reason=str(payload.get("interruption_reason", "")),
        strategy=str(payload.get("strategy", "manual_review")),
        attempt_count=int(payload.get("attempt_count", 0)),
        max_attempts=int(payload.get("max_attempts", 0)),
        next_retry_at=str(payload.get("next_retry_at", "")),
        resume_point=str(payload.get("resume_point", "")),
        resume_payload=dict(payload.get("resume_payload", {})),
        last_error=str(payload.get("last_error", "")),
        updated_at=str(payload.get("updated_at", _now())),
    )


def _self_test() -> None:
    """验证 Agent State 可完成一次工具结果回写。"""
    state = create_initial_agent_state("query_self_test", "hello")
    state = append_assistant_message(
        state,
        (
            AgentContentBlock(
                type="tool_use",
                tool_use_id="tool_1",
                tool_name="demo",
            ),
        ),
    )
    state = append_tool_results(
        state,
        (AgentContentBlock(type="tool_result", tool_use_id="tool_1", content="ok"),),
    )
    state = record_recovery_attempt(
        state,
        "tool_timeout",
        interruption_reason="wait_next_retry_window",
        resume_point="before_tool_execute",
    )
    assert state.turn_count == 2
    assert not state.pending_tool_use_ids
    assert state.recovery.tool_error_attempts == 1


if __name__ == "__main__":
    _self_test()
    print("dutyflow agent state self-test passed")
