# 本文件负责把 AgentState 投影为下一次模型调用可见的 messages。

from __future__ import annotations

from dataclasses import asdict, dataclass, replace

from dutyflow.agent.state import AgentContentBlock, AgentMessage, AgentState
from dutyflow.context.compression_journal import CompressionJournalStore
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

    def __init__(self, compression_journal_store: CompressionJournalStore | None = None) -> None:
        """初始化最近一次工作集快照。"""
        self.latest_working_set: WorkingSet | None = None
        self.latest_state_delta: StateDelta | None = None
        self.latest_budget_report: ContextBudgetReport | None = None
        self.latest_phase_summary_trigger = None
        self.latest_phase_summary_record = None
        self.latest_phase_summary_error = ""
        self.compression_journal_store = compression_journal_store
        self.latest_compression_journal_record = None
        self.latest_compression_journal_error = ""
        self._compression_journal_keys: set[str] = set()
        self.budget_estimator = ContextBudgetEstimator()
        self.latest_health_check: ContextHealthCheck | None = None

    def project(self, state: AgentState) -> tuple[AgentMessage, ...]:
        """返回 ModelContextView 概念层对应的现有 messages 表示。"""
        working_set = self.build_working_set(state)
        self.latest_state_delta = self.build_state_delta(self.latest_working_set, working_set)
        self.latest_working_set = working_set
        projected_messages = self.project_messages(state, working_set=working_set)
        self.latest_budget_report = self.estimate_budget(projected_messages)
        self.latest_health_check = run_context_health_check(state, projected_messages)
        self._record_projection_change_journal(state, projected_messages)
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

    def emergency_compact_messages(self, state: AgentState) -> tuple[AgentMessage, ...]:
        """应急压缩：压缩全部 tool result（包含最新一条），比 micro-compact 更激进。"""
        working_set = self.build_working_set(state)
        # 关键开关：应急压缩不保留任何 fresh tool result，全部替换为 Tool Receipt。
        compacted_messages: list[AgentMessage] = []
        changed = False
        for message in state.messages:
            compacted = _micro_compact_message(message, working_set, frozenset())
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

    def record_phase_summary(self, trigger, record=None, error: str = "") -> None:
        """记录最近一次阶段摘要触发状态，供 `/agent state` 等调试入口查看。"""
        self.latest_phase_summary_trigger = trigger
        if record is not None:
            self.latest_phase_summary_record = record
        self.latest_phase_summary_error = str(error)

    def record_compression_journal(self, record, error: str = "") -> None:
        """记录最近一次 Compression Journal 写入结果。"""
        if record is not None:
            self.latest_compression_journal_record = record
        self.latest_compression_journal_error = str(error)

    def reset(self) -> None:
        """清空运行时缓存的投影状态；compression_journal_store 和 budget_estimator 保持不变。"""
        self.latest_working_set = None
        self.latest_state_delta = None
        self.latest_budget_report = None
        self.latest_phase_summary_trigger = None
        self.latest_phase_summary_record = None
        self.latest_phase_summary_error = ""
        self.latest_compression_journal_record = None
        self.latest_compression_journal_error = ""
        self._compression_journal_keys = set()
        self.latest_health_check = None

    def _record_projection_change_journal(self, state: AgentState, projected_messages: tuple[AgentMessage, ...]) -> None:
        """当投影产生可见变化时写入 Compression Journal。"""
        if self.compression_journal_store is None or projected_messages == state.messages:
            return
        compacted_ids = _compacted_tool_result_ids(state.messages, projected_messages)
        if not compacted_ids:
            return
        dedupe_key = f"{state.query_id}:micro_compact:{','.join(compacted_ids)}"
        if dedupe_key in self._compression_journal_keys:
            return
        health_status = "not_run"
        if self.latest_health_check is not None:
            health_status = "passed" if self.latest_health_check.passed else "failed"
        try:
            record = self.compression_journal_store.write_projection_change(
                state=state,
                source_messages=state.messages,
                projected_messages=projected_messages,
                budget=self.latest_budget_report,
                trigger_reason="tool_result_clearing",
                health_check_status=health_status,
                notes="确定性 micro-compact 将旧 tool result 替换为 Tool Receipt。",
            )
        except Exception as exc:  # noqa: BLE001
            self.record_compression_journal(None, error=str(exc))
            return
        self._compression_journal_keys.add(dedupe_key)
        self.record_compression_journal(record)


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


def _compacted_tool_result_ids(
    source_messages: tuple[AgentMessage, ...],
    projected_messages: tuple[AgentMessage, ...],
) -> tuple[str, ...]:
    """返回本次投影中由原文变成 Tool Receipt 的工具结果 ID。"""
    ids: list[str] = []
    for source_message, projected_message in zip(source_messages, projected_messages, strict=False):
        for source_block, projected_block in zip(source_message.content, projected_message.content, strict=False):
            if _block_became_tool_receipt(source_block, projected_block):
                _append_unique(ids, source_block.tool_use_id)
    return tuple(ids)


def _block_became_tool_receipt(source: AgentContentBlock, projected: AgentContentBlock) -> bool:
    """判断单个 block 是否在投影中被收据化。"""
    return (
        source.type == "tool_result"
        and projected.type == "tool_result"
        and source.tool_use_id == projected.tool_use_id
        and not _is_tool_receipt_text(source.content)
        and _is_tool_receipt_text(projected.content)
    )


def _append_unique(items: list[str], value: str) -> None:
    """追加非空且未出现过的工具结果 ID。"""
    normalized = str(value).strip()
    if normalized and normalized not in items:
        items.append(normalized)


_TASK_CONTROL_FIELDS = ("task_weight_level", "approval_status", "retry_status", "next_action")
_RECOVERY_FIELDS = ("latest_interruption_reason", "latest_resume_point")


@dataclass(frozen=True)
class ContextHealthCheckItem:
    """表示一条上下文健康检查项的结果。"""

    name: str
    passed: bool
    reason: str


@dataclass(frozen=True)
class ContextHealthCheck:
    """表示一次完整的上下文健康检查结果。"""

    passed: bool
    checks: tuple[ContextHealthCheckItem, ...]
    failed_checks: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        """返回可序列化调试结构。"""
        return {
            "passed": self.passed,
            "failed_checks": list(self.failed_checks),
            "checks": [{"name": c.name, "passed": c.passed, "reason": c.reason} for c in self.checks],
        }


def run_context_health_check(
    state: AgentState,
    projected_messages: tuple[AgentMessage, ...],
) -> ContextHealthCheck:
    """对 projected_messages 执行确定性健康检查，验证关键锚点仍然可见。"""
    checks: list[ContextHealthCheckItem] = []

    checks.append(_check_message_count(state.messages, projected_messages))
    checks.append(_check_task_id_preserved(state, projected_messages))
    checks.append(_check_event_id_preserved(state, projected_messages))
    checks.append(_check_pending_tool_ids_preserved(state, projected_messages))

    failed = tuple(c.name for c in checks if not c.passed)
    return ContextHealthCheck(passed=not failed, checks=tuple(checks), failed_checks=failed)


def _check_message_count(
    source: tuple[AgentMessage, ...],
    projected: tuple[AgentMessage, ...],
) -> ContextHealthCheckItem:
    """投影后 message 数量不能减少。"""
    passed = len(projected) >= len(source)
    return ContextHealthCheckItem(
        name="message_count_preserved",
        passed=passed,
        reason="" if passed else f"projected={len(projected)} < source={len(source)}",
    )


def _check_task_id_preserved(state: AgentState, projected: tuple[AgentMessage, ...]) -> ContextHealthCheckItem:
    """当前 task_id 必须在 projected_messages 文本中可见（或本身为空）。"""
    task_id = (state.current_task_id or state.task_control.task_id).strip()
    if not task_id:
        return ContextHealthCheckItem(name="task_id_preserved", passed=True, reason="no active task_id")
    visible = _id_visible_in_messages(task_id, projected)
    return ContextHealthCheckItem(
        name="task_id_preserved",
        passed=visible,
        reason="" if visible else f"task_id={task_id!r} not found in projected messages",
    )


def _check_event_id_preserved(state: AgentState, projected: tuple[AgentMessage, ...]) -> ContextHealthCheckItem:
    """当前 event_id 必须在 projected_messages 文本中可见（或本身为空）。"""
    event_id = state.current_event_id.strip()
    if not event_id:
        return ContextHealthCheckItem(name="event_id_preserved", passed=True, reason="no active event_id")
    visible = _id_visible_in_messages(event_id, projected)
    return ContextHealthCheckItem(
        name="event_id_preserved",
        passed=visible,
        reason="" if visible else f"event_id={event_id!r} not found in projected messages",
    )


def _check_pending_tool_ids_preserved(
    state: AgentState,
    projected: tuple[AgentMessage, ...],
) -> ContextHealthCheckItem:
    """state.pending_tool_use_ids 中的 ID 应在 projected 中仍有 tool_use block。"""
    if not state.pending_tool_use_ids:
        return ContextHealthCheckItem(name="pending_tool_ids_preserved", passed=True, reason="no pending tool IDs")
    present = frozenset(
        block.tool_use_id
        for msg in projected
        for block in msg.content
        if block.type in ("tool_use", "tool_result") and block.tool_use_id
    )
    missing = [tid for tid in state.pending_tool_use_ids if tid not in present]
    passed = not missing
    return ContextHealthCheckItem(
        name="pending_tool_ids_preserved",
        passed=passed,
        reason="" if passed else f"missing pending tool IDs: {missing}",
    )


def _id_visible_in_messages(id_value: str, messages: tuple[AgentMessage, ...]) -> bool:
    """检查 id_value 是否在 projected messages 的任意文本或 tool_use_id 字段中可见。"""
    for msg in messages:
        for block in msg.content:
            if id_value in (block.text or ""):
                return True
            if id_value in (block.content or ""):
                return True
            if id_value == block.tool_use_id:
                return True
            if id_value in (block.tool_name or ""):
                return True
    return False


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
