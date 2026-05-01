# 本文件负责把 AgentState 投影为下一次模型调用可见的 messages。

from __future__ import annotations

from dataclasses import asdict, dataclass, replace

from dutyflow.agent.state import AgentMessage, AgentState


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


_WORKING_SET_TUPLE_FIELDS = frozenset(
    {
        "pending_tool_use_ids",
        "last_tool_result_ids",
        "recent_tool_use_ids",
        "recent_tool_names",
        "waiting_recovery_scope_ids",
    }
)


class RuntimeContextManager:
    """管理模型调用前的运行时上下文投影，不拥有 AgentState 源状态。"""

    def __init__(self) -> None:
        """初始化最近一次工作集快照。"""
        self.latest_working_set: WorkingSet | None = None

    def project(self, state: AgentState) -> tuple[AgentMessage, ...]:
        """返回 ModelContextView 概念层对应的现有 messages 表示。"""
        self.latest_working_set = self.build_working_set(state)
        return self.project_messages(state)

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

    def project_messages(self, state: AgentState) -> tuple[AgentMessage, ...]:
        """返回模型下一次调用应看到的 AgentMessage 序列。"""
        return state.messages

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


def _self_test() -> None:
    """验证第一版投影层保持 messages 结构不变。"""
    from dutyflow.agent.state import create_initial_agent_state

    state = create_initial_agent_state("ctx_self_test", "hello")
    manager = RuntimeContextManager()
    projected = manager.project_state_for_model(state)
    assert manager.project(state) == state.messages
    assert projected.messages == state.messages
    assert manager.latest_working_set is not None
    assert manager.latest_working_set.latest_user_text == "hello"


if __name__ == "__main__":
    _self_test()
    print("dutyflow runtime context self-test passed")
