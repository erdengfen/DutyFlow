# 本文件负责维护 agent loop 内部的多轮运行状态，不读写本地快照文件。

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

ALLOWED_BLOCK_TYPES = frozenset({"text", "tool_use", "tool_result", "placeholder"})
ALLOWED_ROLES = frozenset({"user", "assistant", "system"})
TRANSITION_REASONS = frozenset(
    {
        "start",
        "tool_result_continuation",
        "max_tokens_recovery",
        "compact_retry",
        "transport_retry",
        "stop_hook_continuation",
        "finished",
        "failed",
    }
)


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
    attempt_count: int = 0
    approval_status: str = "none"
    retry_status: str = "none"
    next_action: str = ""


@dataclass(frozen=True)
class AgentRecoveryState:
    """记录主循环错误恢复路径的尝试次数。"""

    continuation_attempts: int = 0
    compact_attempts: int = 0
    transport_attempts: int = 0
    tool_error_attempts: int = 0


@dataclass(frozen=True)
class AgentState:
    """保存一条 query 在多轮 agent loop 中持续更新的运行状态。"""

    query_id: str
    messages: tuple[AgentMessage, ...]
    turn_count: int = 1
    transition_reason: str = "start"
    current_event_id: str = ""
    current_task_id: str = ""
    pending_tool_use_ids: tuple[str, ...] = ()
    last_tool_result_ids: tuple[str, ...] = ()
    task_control: AgentTaskControl = field(default_factory=AgentTaskControl)
    recovery: AgentRecoveryState = field(default_factory=AgentRecoveryState)
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


def validate_agent_state(state: AgentState) -> AgentState:
    """验证 Agent State 的核心不变量。"""
    _validate_state_header(state)
    for message in state.messages:
        _validate_message(message)
    _validate_pending_ids(state.pending_tool_use_ids)
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


def _recovery_to_dict(recovery: AgentRecoveryState) -> dict[str, int]:
    """序列化恢复计数。"""
    return {
        "continuation_attempts": recovery.continuation_attempts,
        "compact_attempts": recovery.compact_attempts,
        "transport_attempts": recovery.transport_attempts,
        "tool_error_attempts": recovery.tool_error_attempts,
    }


def _recovery_from_dict(payload: Mapping[str, Any]) -> AgentRecoveryState:
    """从字典恢复错误恢复计数。"""
    return AgentRecoveryState(
        continuation_attempts=int(payload.get("continuation_attempts", 0)),
        compact_attempts=int(payload.get("compact_attempts", 0)),
        transport_attempts=int(payload.get("transport_attempts", 0)),
        tool_error_attempts=int(payload.get("tool_error_attempts", 0)),
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
    assert state.turn_count == 2
    assert not state.pending_tool_use_ids


if __name__ == "__main__":
    _self_test()
    print("dutyflow agent state self-test passed")
