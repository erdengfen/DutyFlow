# 本文件负责把 AgentState 投影为下一次模型调用可见的 messages。

from __future__ import annotations

from dataclasses import asdict, dataclass, replace

from dutyflow.agent.state import AgentContentBlock, AgentMessage, AgentState
from dutyflow.context.context_budget import ContextBudgetEstimator, ContextBudgetReport


@dataclass(frozen=True)
class WorkingSet:
    """表示模型下一步决策所需的最小运行时工作集。"""

    query_id: str
    turn_count: int
    transition_reason: str
    current_event_id: str
    current_task_id: str
    latest_user_text: str
    latest_assistant_text: str
    pending_tool_use_ids: tuple[str, ...]
    last_tool_result_ids: tuple[str, ...]
    recent_tool_use_ids: tuple[str, ...]
    recent_tool_names: tuple[str, ...]
    task_weight_level: str
    approval_status: str
    retry_status: str
    next_action: str
    latest_interruption_reason: str
    latest_resume_point: str
    waiting_recovery_scope_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        """返回可用于测试和后续日志记录的稳定字典。"""
        payload = asdict(self)
        for key in _WORKING_SET_TUPLE_FIELDS:
            payload[key] = list(payload[key])
        return payload


@dataclass(frozen=True)
class StateDelta:
    """表示两次 Working Set 之间的最小运行时增量。"""

    query_id: str
    previous_turn_count: int
    current_turn_count: int
    turn_advanced: bool
    transition_changed: bool
    new_user_text: str
    new_assistant_text: str
    current_event_id_changed: bool
    current_task_id_changed: bool
    new_pending_tool_use_ids: tuple[str, ...]
    resolved_tool_use_ids: tuple[str, ...]
    new_tool_result_ids: tuple[str, ...]
    new_recent_tool_use_ids: tuple[str, ...]
    new_recent_tool_names: tuple[str, ...]
    task_control_changed_fields: tuple[str, ...]
    recovery_changed_fields: tuple[str, ...]
    new_waiting_recovery_scope_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        """返回可用于测试和后续日志记录的稳定字典。"""
        payload = asdict(self)
        for key in _STATE_DELTA_TUPLE_FIELDS:
            payload[key] = list(payload[key])
        return payload


_WORKING_SET_TUPLE_FIELDS = frozenset(
    {
        "pending_tool_use_ids",
        "last_tool_result_ids",
        "recent_tool_use_ids",
        "recent_tool_names",
        "waiting_recovery_scope_ids",
    }
)
_STATE_DELTA_TUPLE_FIELDS = frozenset(
    {
        "new_pending_tool_use_ids",
        "resolved_tool_use_ids",
        "new_tool_result_ids",
        "new_recent_tool_use_ids",
        "new_recent_tool_names",
        "task_control_changed_fields",
        "recovery_changed_fields",
        "new_waiting_recovery_scope_ids",
    }
)


class RuntimeContextManager:
    """管理模型调用前的运行时上下文投影，不拥有 AgentState 源状态。"""

    def __init__(self) -> None:
        """初始化最近一次工作集快照。"""
        self.latest_working_set: WorkingSet | None = None
        self.latest_state_delta: StateDelta | None = None
        self.latest_budget_report: ContextBudgetReport | None = None
        self.budget_estimator = ContextBudgetEstimator()

    def project(self, state: AgentState) -> tuple[AgentMessage, ...]:
        """返回 ModelContextView 概念层对应的现有 messages 表示。"""
        working_set = self.build_working_set(state)
        self.latest_state_delta = self.build_state_delta(self.latest_working_set, working_set)
        self.latest_working_set = working_set
        projected_messages = self.project_messages(state, working_set=working_set)
        self.latest_budget_report = self.estimate_budget(projected_messages)
        return projected_messages

    def build_working_set(self, state: AgentState) -> WorkingSet:
        """从 AgentState 确定性构造当前模型调用前的工作集。"""
        return WorkingSet(
            query_id=state.query_id,
            turn_count=state.turn_count,
            transition_reason=state.transition_reason,
            current_event_id=state.current_event_id,
            current_task_id=state.current_task_id or state.task_control.task_id,
            latest_user_text=_latest_text_by_role(state.messages, "user"),
            latest_assistant_text=_latest_text_by_role(state.messages, "assistant"),
            pending_tool_use_ids=state.pending_tool_use_ids,
            last_tool_result_ids=state.last_tool_result_ids,
            recent_tool_use_ids=_recent_tool_use_ids(state.messages),
            recent_tool_names=_recent_tool_names(state.messages),
            task_weight_level=state.task_control.weight_level,
            approval_status=state.task_control.approval_status,
            retry_status=state.task_control.retry_status,
            next_action=state.task_control.next_action,
            latest_interruption_reason=state.recovery.latest_interruption_reason,
            latest_resume_point=state.recovery.latest_resume_point,
            waiting_recovery_scope_ids=_waiting_recovery_scope_ids(state),
        )

    def build_state_delta(self, previous: WorkingSet | None, current: WorkingSet) -> StateDelta:
        """对比两次 Working Set，构造模型下一步可用的状态增量。"""
        return StateDelta(
            query_id=current.query_id,
            previous_turn_count=previous.turn_count if previous else 0,
            current_turn_count=current.turn_count,
            turn_advanced=_field_changed(previous, current, "turn_count"),
            transition_changed=_field_changed(previous, current, "transition_reason"),
            new_user_text=_changed_text(previous, current, "latest_user_text"),
            new_assistant_text=_changed_text(previous, current, "latest_assistant_text"),
            current_event_id_changed=_field_changed(previous, current, "current_event_id"),
            current_task_id_changed=_field_changed(previous, current, "current_task_id"),
            new_pending_tool_use_ids=_new_values(previous, current, "pending_tool_use_ids"),
            resolved_tool_use_ids=_resolved_values(previous, current, "pending_tool_use_ids"),
            new_tool_result_ids=_new_values(previous, current, "last_tool_result_ids"),
            new_recent_tool_use_ids=_new_values(previous, current, "recent_tool_use_ids"),
            new_recent_tool_names=_new_values(previous, current, "recent_tool_names"),
            task_control_changed_fields=_changed_fields(previous, current, _TASK_CONTROL_FIELDS),
            recovery_changed_fields=_changed_fields(previous, current, _RECOVERY_FIELDS),
            new_waiting_recovery_scope_ids=_new_values(previous, current, "waiting_recovery_scope_ids"),
        )

    def project_messages(
        self,
        state: AgentState,
        working_set: WorkingSet | None = None,
    ) -> tuple[AgentMessage, ...]:
        """返回模型下一次调用应看到的 AgentMessage 序列。"""
        return self.micro_compact_messages(state, working_set=working_set)

    def estimate_budget(self, messages: tuple[AgentMessage, ...]) -> ContextBudgetReport:
        """估算模型可见 messages 的上下文预算占用。"""
        return self.budget_estimator.estimate_messages(messages)

    def micro_compact_messages(
        self,
        state: AgentState,
        working_set: WorkingSet | None = None,
    ) -> tuple[AgentMessage, ...]:
        """把旧 tool_result 原文确定性替换为 Tool Receipt，不修改源状态。"""
        active_working_set = working_set or self.build_working_set(state)
        fresh_tool_result_ids = _fresh_tool_result_ids(state.messages)
        compacted_messages: list[AgentMessage] = []
        changed = False
        for message in state.messages:
            compacted = _micro_compact_message(message, active_working_set, fresh_tool_result_ids)
            changed = changed or compacted is not message
            compacted_messages.append(compacted)
        if not changed:
            return state.messages
        return tuple(compacted_messages)

    def project_state_for_model(self, state: AgentState) -> AgentState:
        """把投影后的 messages 渲染回现有 AgentState 结构供模型客户端消费。"""
        projected_messages = self.project(state)
        if projected_messages is state.messages:
            return state
        return replace(state, messages=projected_messages)


def _latest_text_by_role(messages: tuple[AgentMessage, ...], role: str) -> str:
    """提取指定角色最近一条文本内容。"""
    for message in reversed(messages):
        if message.role != role:
            continue
        text = _message_text(message)
        if text:
            return text
    return ""


def _field_changed(previous: WorkingSet | None, current: WorkingSet, field_name: str) -> bool:
    """判断 Working Set 的指定字段是否发生变化。"""
    if previous is None:
        return bool(getattr(current, field_name))
    return getattr(previous, field_name) != getattr(current, field_name)


def _changed_text(previous: WorkingSet | None, current: WorkingSet, field_name: str) -> str:
    """返回发生变化的短文本字段；无变化时返回空字符串。"""
    current_value = str(getattr(current, field_name))
    if previous is None:
        return current_value
    if getattr(previous, field_name) == current_value:
        return ""
    return current_value


def _new_values(previous: WorkingSet | None, current: WorkingSet, field_name: str) -> tuple[str, ...]:
    """返回当前 tuple 字段中相对上次新增的值。"""
    current_values = tuple(getattr(current, field_name))
    if previous is None:
        return current_values
    previous_values = set(getattr(previous, field_name))
    return tuple(value for value in current_values if value not in previous_values)


def _resolved_values(previous: WorkingSet | None, current: WorkingSet, field_name: str) -> tuple[str, ...]:
    """返回上次存在而当前已经消失的 tuple 字段值。"""
    if previous is None:
        return ()
    current_values = set(getattr(current, field_name))
    return tuple(value for value in getattr(previous, field_name) if value not in current_values)


def _changed_fields(
    previous: WorkingSet | None,
    current: WorkingSet,
    field_names: tuple[str, ...],
) -> tuple[str, ...]:
    """返回一组字段中发生变化的字段名。"""
    return tuple(name for name in field_names if _field_changed(previous, current, name))


def _message_text(message: AgentMessage) -> str:
    """合并单条消息中的文本块，不把工具结果伪装成用户输入。"""
    texts = [block.text for block in message.content if block.type == "text" and block.text]
    return "\n".join(texts)


def _recent_tool_use_ids(messages: tuple[AgentMessage, ...], limit: int = 8) -> tuple[str, ...]:
    """返回最近若干工具调用 ID，保留顺序且去重。"""
    ids: list[str] = []
    for message in reversed(messages):
        for block in reversed(message.content):
            if block.type == "tool_use" and block.tool_use_id:
                _append_recent_unique(ids, block.tool_use_id, limit)
    return tuple(reversed(ids))


def _recent_tool_names(messages: tuple[AgentMessage, ...], limit: int = 8) -> tuple[str, ...]:
    """返回最近若干工具名，保留顺序且去重。"""
    names: list[str] = []
    for message in reversed(messages):
        for block in reversed(message.content):
            if block.type == "tool_use" and block.tool_name:
                _append_recent_unique(names, block.tool_name, limit)
    return tuple(reversed(names))


def _append_recent_unique(items: list[str], value: str, limit: int) -> None:
    """从后向前收集最近值时保持去重和数量上限。"""
    if value in items or len(items) >= limit:
        return
    items.append(value)


def _waiting_recovery_scope_ids(state: AgentState) -> tuple[str, ...]:
    """返回当前处于 waiting 状态的恢复 scope ID。"""
    return tuple(scope.scope_id for scope in state.recovery.recovery_scopes if scope.status == "waiting")


def _fresh_tool_result_ids(messages: tuple[AgentMessage, ...]) -> frozenset[str]:
    """识别下一轮模型必须原文消费的最新 tool_result。"""
    if not messages:
        return frozenset()
    latest = messages[-1]
    if latest.role != "user" or not latest.content:
        return frozenset()
    if not all(block.type == "tool_result" for block in latest.content):
        return frozenset()
    return frozenset(block.tool_use_id for block in latest.content if block.tool_use_id)


def _micro_compact_message(
    message: AgentMessage,
    working_set: WorkingSet,
    fresh_tool_result_ids: frozenset[str],
) -> AgentMessage:
    """压缩单条消息里的旧 tool_result block。"""
    compacted_blocks: list[AgentContentBlock] = []
    changed = False
    for block in message.content:
        compacted = _micro_compact_block(block, working_set, fresh_tool_result_ids)
        changed = changed or compacted is not block
        compacted_blocks.append(compacted)
    if not changed:
        return message
    return replace(message, content=tuple(compacted_blocks))


def _micro_compact_block(
    block: AgentContentBlock,
    working_set: WorkingSet,
    fresh_tool_result_ids: frozenset[str],
) -> AgentContentBlock:
    """把旧工具结果块替换为单行 Tool Receipt。"""
    if block.type != "tool_result" or block.tool_use_id in fresh_tool_result_ids:
        return block
    if _is_tool_receipt_text(block.content):
        return block
    from dutyflow.context.tool_receipt import ToolReceiptBuilder

    receipt = ToolReceiptBuilder().from_agent_block(block, working_set=working_set)
    return replace(block, content=receipt.to_context_text())


def _is_tool_receipt_text(content: str) -> bool:
    """判断工具结果是否已经被收据化，确保 micro-compact 幂等。"""
    return str(content).strip().startswith("ToolReceipt(")


_TASK_CONTROL_FIELDS = ("task_weight_level", "approval_status", "retry_status", "next_action")
_RECOVERY_FIELDS = ("latest_interruption_reason", "latest_resume_point")


def _self_test() -> None:
    """验证第一版投影层保持 messages 结构不变。"""
    from dutyflow.agent.state import (
        append_assistant_message,
        append_tool_results,
        append_user_message,
        create_initial_agent_state,
    )

    state = create_initial_agent_state("ctx_self_test", "hello")
    manager = RuntimeContextManager()
    projected = manager.project_state_for_model(state)
    assert projected.messages == state.messages
    assert manager.latest_working_set is not None
    assert manager.latest_working_set.latest_user_text == "hello"
    assert manager.latest_state_delta is not None
    assert manager.latest_state_delta.new_user_text == "hello"
    assert manager.latest_budget_report is not None
    assert manager.latest_budget_report.total_estimated_tokens > 0
    assert manager.project_messages(state) == state.messages
    state = append_assistant_message(
        state,
        (AgentContentBlock(type="tool_use", tool_use_id="tool_1", tool_name="sample_tool"),),
    )
    state = append_tool_results(
        state,
        (
            AgentContentBlock(
                type="tool_result",
                tool_use_id="tool_1",
                tool_name="sample_tool",
                content="raw result",
            ),
        ),
    )
    assert manager.project_state_for_model(state).messages[-1].content[0].content == "raw result"
    state = append_user_message(state, "continue")
    compacted = manager.project_state_for_model(state)
    assert compacted.messages[-2].content[0].content.startswith("ToolReceipt(")


if __name__ == "__main__":
    _self_test()
    print("dutyflow runtime context self-test passed")
