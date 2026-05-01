# 本文件负责 Step 8 的阶段摘要触发、LLM 摘要生成和 context summary 落盘。

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from dutyflow.agent.model_client import ModelClient
from dutyflow.agent.state import AgentContentBlock, AgentMessage, AgentState
from dutyflow.context.context_budget import ContextBudgetReport
from dutyflow.context.runtime_context import StateDelta, WorkingSet
from dutyflow.storage.file_store import FileStore
from dutyflow.storage.markdown_store import MarkdownDocument, MarkdownStore


CONTEXT_SUMMARY_SCHEMA = "dutyflow.context_summary.v1"
SUMMARY_KIND_PHASE = "phase_summary"
TRIGGER_REASONS = frozenset(
    {
        "context_overflow",
        "budget_hard_limit",
        "phase_boundary_budget",
        "manual_compress",
        "phase_boundary_only",
        "none",
    }
)
TRIGGER_MODES = frozenset({"emergency", "normal", "manual", "record_only", "none"})
# 关键开关：常规阶段摘要软阈值；达到阶段边界且估算 token 超过该值时才调用 LLM。
DEFAULT_SOFT_TOKEN_LIMIT = 6000
# 关键开关：上下文预算硬阈值；估算 token 超过该值时即使没有阶段边界也调用 LLM。
DEFAULT_HARD_TOKEN_LIMIT = 10000
# 关键开关：摘要 LLM 输入最多携带 12000 字符的模型可见上下文预览，避免摘要调用本身继续膨胀。
SUMMARY_CONTEXT_PREVIEW_MAX_CHARS = 12000
# 关键开关：落盘摘要正文兜底限制 8000 字符，防止异常模型输出撑大上下文摘要文件。
SUMMARY_TEXT_MAX_CHARS = 8000
IDENTITY_CONTEXT_TOOLS = frozenset(
    {
        "lookup_contact_identity",
        "lookup_source_context",
        "lookup_responsibility_context",
        "search_contact_knowledge_headers",
        "get_contact_knowledge_detail",
    }
)
APPROVAL_TOOLS = frozenset({"create_approval_request", "resume_after_approval"})
BACKGROUND_TASK_TOOLS = frozenset({"create_background_task", "schedule_background_task"})
ID_PATTERN = re.compile(r"\b(?:task|evt|approval|per|tool|call)_[A-Za-z0-9_:-]+\b")


@dataclass(frozen=True)
class PhaseSummaryTrigger:
    """表示一次阶段摘要触发判断结果。"""

    reason: str
    mode: str
    phase: str
    estimated_tokens: int
    soft_token_limit: int
    hard_token_limit: int
    phase_boundary_detected: bool
    requires_llm: bool
    should_record_boundary: bool
    dedupe_key: str

    def to_dict(self) -> dict[str, object]:
        """返回便于调试和测试的稳定字典。"""
        return asdict(self)


@dataclass(frozen=True)
class PhaseSummaryRecord:
    """表示一条已经生成并可追溯的阶段摘要记录。"""

    path: Path
    relative_path: str
    summary_id: str
    summary_kind: str
    phase: str
    trigger_reason: str
    trigger_mode: str
    source_query_id: str
    task_id: str
    event_ids: tuple[str, ...]
    created_at: str
    compact_level: str
    source_message_count: int
    estimated_tokens: int
    soft_token_limit: int
    hard_token_limit: int
    phase_boundary_detected: bool
    requires_llm: bool
    anchor_task_ids: tuple[str, ...]
    anchor_event_ids: tuple[str, ...]
    anchor_tool_use_ids: tuple[str, ...]
    anchor_approval_ids: tuple[str, ...]
    summary_text: str

    def to_dict(self) -> dict[str, object]:
        """返回可序列化调试结构。"""
        payload = asdict(self)
        payload["path"] = str(self.path)
        payload["event_ids"] = list(self.event_ids)
        payload["anchor_task_ids"] = list(self.anchor_task_ids)
        payload["anchor_event_ids"] = list(self.anchor_event_ids)
        payload["anchor_tool_use_ids"] = list(self.anchor_tool_use_ids)
        payload["anchor_approval_ids"] = list(self.anchor_approval_ids)
        return payload


class PhaseBoundaryDetector:
    """根据 AgentState 的确定性信号识别阶段边界。"""

    def detect(self, state: AgentState, working_set: WorkingSet, delta: StateDelta | None) -> tuple[bool, str]:
        """返回是否检测到阶段边界及阶段名称。"""
        del state
        if working_set.approval_status in {"waiting", "approved", "rejected", "deferred"}:
            return True, "approval_boundary"
        if working_set.latest_interruption_reason or working_set.waiting_recovery_scope_ids:
            return True, "tool_failure_recovery"
        recent_tools = set(working_set.recent_tool_names)
        if recent_tools & IDENTITY_CONTEXT_TOOLS:
            return True, "completed_context_lookup"
        if recent_tools & APPROVAL_TOOLS:
            return True, "approval_boundary"
        if recent_tools & BACKGROUND_TASK_TOOLS or working_set.current_task_id:
            return True, "background_task_boundary"
        if recent_tools:
            return True, "completed_tool_read"
        if delta and (delta.new_user_text or delta.turn_advanced and working_set.transition_reason == "user_continuation"):
            return True, "received_user_request"
        if working_set.transition_reason == "start":
            return True, "received_user_request"
        return False, "none"


class PhaseSummaryPolicy:
    """把阶段边界、预算和强制原因转成摘要触发决策。"""

    def __init__(
        self,
        *,
        soft_token_limit: int = DEFAULT_SOFT_TOKEN_LIMIT,
        hard_token_limit: int = DEFAULT_HARD_TOKEN_LIMIT,
        boundary_detector: PhaseBoundaryDetector | None = None,
    ) -> None:
        """设置摘要触发阈值。"""
        if soft_token_limit < 1:
            raise ValueError("soft_token_limit must be >= 1")
        if hard_token_limit < soft_token_limit:
            raise ValueError("hard_token_limit must be >= soft_token_limit")
        self.soft_token_limit = soft_token_limit
        self.hard_token_limit = hard_token_limit
        self.boundary_detector = boundary_detector or PhaseBoundaryDetector()

    def evaluate(
        self,
        *,
        state: AgentState,
        working_set: WorkingSet,
        delta: StateDelta | None,
        budget: ContextBudgetReport | None,
        forced_reason: str = "",
    ) -> PhaseSummaryTrigger:
        """根据当前运行时状态判断是否需要生成阶段摘要。"""
        estimated_tokens = budget.total_estimated_tokens if budget else 0
        phase_boundary_detected, phase = self.boundary_detector.detect(state, working_set, delta)
        if forced_reason:
            return self._forced_trigger(forced_reason, state, phase, estimated_tokens, phase_boundary_detected)
        if estimated_tokens >= self.hard_token_limit:
            return self._trigger("budget_hard_limit", "normal", phase, state, estimated_tokens, phase_boundary_detected)
        if phase_boundary_detected and estimated_tokens >= self.soft_token_limit:
            return self._trigger("phase_boundary_budget", "normal", phase, state, estimated_tokens, True)
        if phase_boundary_detected:
            return self._record_only("phase_boundary_only", phase, state, estimated_tokens)
        return self._none(state, estimated_tokens)

    def _forced_trigger(
        self,
        reason: str,
        state: AgentState,
        phase: str,
        estimated_tokens: int,
        phase_boundary_detected: bool,
    ) -> PhaseSummaryTrigger:
        """构造强制触发决策。"""
        if reason not in {"context_overflow", "manual_compress"}:
            raise ValueError(f"Unknown forced phase summary reason: {reason}")
        mode = "emergency" if reason == "context_overflow" else "manual"
        return self._trigger(reason, mode, phase, state, estimated_tokens, phase_boundary_detected)

    def _trigger(
        self,
        reason: str,
        mode: str,
        phase: str,
        state: AgentState,
        estimated_tokens: int,
        phase_boundary_detected: bool,
    ) -> PhaseSummaryTrigger:
        """构造需要 LLM 摘要的触发记录。"""
        _validate_trigger(reason, mode)
        return PhaseSummaryTrigger(
            reason=reason,
            mode=mode,
            phase=phase or "unknown",
            estimated_tokens=estimated_tokens,
            soft_token_limit=self.soft_token_limit,
            hard_token_limit=self.hard_token_limit,
            phase_boundary_detected=phase_boundary_detected,
            requires_llm=True,
            should_record_boundary=True,
            dedupe_key=_dedupe_key(state, reason, phase or "unknown", estimated_tokens),
        )

    def _record_only(self, reason: str, phase: str, state: AgentState, estimated_tokens: int) -> PhaseSummaryTrigger:
        """构造只记录阶段边界、不调用 LLM 的触发记录。"""
        _validate_trigger(reason, "record_only")
        return PhaseSummaryTrigger(
            reason=reason,
            mode="record_only",
            phase=phase or "unknown",
            estimated_tokens=estimated_tokens,
            soft_token_limit=self.soft_token_limit,
            hard_token_limit=self.hard_token_limit,
            phase_boundary_detected=True,
            requires_llm=False,
            should_record_boundary=True,
            dedupe_key=_dedupe_key(state, reason, phase or "unknown", estimated_tokens),
        )

    def _none(self, state: AgentState, estimated_tokens: int) -> PhaseSummaryTrigger:
        """构造无触发记录。"""
        return PhaseSummaryTrigger(
            reason="none",
            mode="none",
            phase="none",
            estimated_tokens=estimated_tokens,
            soft_token_limit=self.soft_token_limit,
            hard_token_limit=self.hard_token_limit,
            phase_boundary_detected=False,
            requires_llm=False,
            should_record_boundary=False,
            dedupe_key=_dedupe_key(state, "none", "none", estimated_tokens),
        )


class PhaseSummaryService:
    """负责执行阶段摘要策略，并在需要时调用 LLM 生成摘要。"""

    def __init__(
        self,
        *,
        policy: PhaseSummaryPolicy | None = None,
        store: "PhaseSummaryStore | None" = None,
    ) -> None:
        """绑定触发策略和可选落盘 store。"""
        self.policy = policy or PhaseSummaryPolicy()
        self.store = store
        self._completed_keys: set[str] = set()

    def maybe_create_summary(
        self,
        *,
        model_client: ModelClient,
        state: AgentState,
        projected_messages: tuple[AgentMessage, ...],
        working_set: WorkingSet,
        delta: StateDelta | None,
        budget: ContextBudgetReport | None,
        forced_reason: str = "",
    ) -> tuple[PhaseSummaryTrigger, PhaseSummaryRecord | None]:
        """评估触发条件，并在必要时调用 LLM 生成阶段摘要。"""
        trigger = self.policy.evaluate(
            state=state,
            working_set=working_set,
            delta=delta,
            budget=budget,
            forced_reason=forced_reason,
        )
        if not trigger.requires_llm:
            return trigger, None
        if trigger.dedupe_key in self._completed_keys:
            return trigger, None
        summary_text = self._call_summary_model(
            model_client=model_client,
            state=state,
            projected_messages=projected_messages,
            working_set=working_set,
            delta=delta,
            budget=budget,
            trigger=trigger,
        )
        record = self._build_record(state, projected_messages, trigger, summary_text)
        if self.store is not None:
            record = self.store.write_summary(record)
        self._completed_keys.add(trigger.dedupe_key)
        return trigger, record

    def _call_summary_model(
        self,
        *,
        model_client: ModelClient,
        state: AgentState,
        projected_messages: tuple[AgentMessage, ...],
        working_set: WorkingSet,
        delta: StateDelta | None,
        budget: ContextBudgetReport | None,
        trigger: PhaseSummaryTrigger,
    ) -> str:
        """调用模型生成只供运行时使用的阶段摘要。"""
        summary_state = AgentState(
            query_id=f"{state.query_id}_phase_summary",
            messages=(
                AgentMessage(
                    role="system",
                    content=(AgentContentBlock(type="text", text=_summary_system_prompt()),),
                ),
                AgentMessage(
                    role="user",
                    content=(
                        AgentContentBlock(
                            type="text",
                            text=_summary_user_prompt(
                                state=state,
                                projected_messages=projected_messages,
                                working_set=working_set,
                                delta=delta,
                                budget=budget,
                                trigger=trigger,
                            ),
                        ),
                    ),
                ),
            ),
        )
        response = model_client.call_model(summary_state, ())
        return _clamp_summary_text(_extract_text(response.assistant_blocks))

    def _build_record(
        self,
        state: AgentState,
        projected_messages: tuple[AgentMessage, ...],
        trigger: PhaseSummaryTrigger,
        summary_text: str,
    ) -> PhaseSummaryRecord:
        """构造阶段摘要记录，实际路径由 store 补齐。"""
        anchors = _extract_anchors(state, projected_messages)
        event_ids = _ordered_unique((state.current_event_id, *anchors["event_ids"]))
        task_id = state.current_task_id or state.task_control.task_id
        return PhaseSummaryRecord(
            path=Path(),
            relative_path="",
            summary_id=_generate_context_id(),
            summary_kind=SUMMARY_KIND_PHASE,
            phase=trigger.phase,
            trigger_reason=trigger.reason,
            trigger_mode=trigger.mode,
            source_query_id=state.query_id,
            task_id=task_id,
            event_ids=event_ids,
            created_at=_now_iso(),
            compact_level="phase",
            source_message_count=len(projected_messages),
            estimated_tokens=trigger.estimated_tokens,
            soft_token_limit=trigger.soft_token_limit,
            hard_token_limit=trigger.hard_token_limit,
            phase_boundary_detected=trigger.phase_boundary_detected,
            requires_llm=trigger.requires_llm,
            anchor_task_ids=_ordered_unique((task_id, *anchors["task_ids"])),
            anchor_event_ids=event_ids,
            anchor_tool_use_ids=anchors["tool_use_ids"],
            anchor_approval_ids=anchors["approval_ids"],
            summary_text=summary_text,
        )


class PhaseSummaryStore:
    """封装 `data/contexts/ctx_<id>.md` 阶段摘要读写。"""

    def __init__(self, project_root: Path, *, markdown_store: MarkdownStore | None = None) -> None:
        """绑定项目目录并准备上下文目录。"""
        self.project_root = Path(project_root).resolve()
        self.markdown_store = markdown_store or MarkdownStore(FileStore(self.project_root))
        self.context_dir = self.project_root / "data" / "contexts"
        self.markdown_store.file_store.ensure_dir(self.context_dir)

    def write_summary(self, record: PhaseSummaryRecord) -> PhaseSummaryRecord:
        """把阶段摘要写入本地 Markdown 并返回带路径的记录。"""
        path = self.context_dir / f"{record.summary_id}.md"
        with_path = PhaseSummaryRecord(
            **{
                **asdict(record),
                "path": path,
                "relative_path": _relative_path(self.project_root, path),
            }
        )
        document = MarkdownDocument(frontmatter=_record_frontmatter(with_path), body=_record_body(with_path))
        self.markdown_store.write_document(path, document)
        return with_path

    def read_summary(self, summary_id: str) -> PhaseSummaryRecord | None:
        """按摘要 ID 读取阶段摘要。"""
        path = self.context_dir / f"{summary_id}.md"
        if not self.markdown_store.exists(path):
            return None
        document = self.markdown_store.read_document(path)
        summary_text = self.markdown_store.extract_section(path, "Phase Summary")
        return _record_from_document(self.project_root, path, document, summary_text)


def _validate_trigger(reason: str, mode: str) -> None:
    """校验阶段摘要触发字段。"""
    if reason not in TRIGGER_REASONS:
        raise ValueError(f"Unknown phase summary reason: {reason}")
    if mode not in TRIGGER_MODES:
        raise ValueError(f"Unknown phase summary mode: {mode}")


def _dedupe_key(state: AgentState, reason: str, phase: str, estimated_tokens: int) -> str:
    """生成同一运行阶段的摘要去重键。"""
    return f"{state.query_id}:{state.turn_count}:{len(state.messages)}:{reason}:{phase}:{estimated_tokens}"


def _summary_system_prompt() -> str:
    """返回阶段摘要专用系统提示词。"""
    return (
        "You create concise runtime phase summaries for DutyFlow. "
        "Preserve stable anchors such as task_id, event_id, approval_id and tool_use_id. "
        "Do not invent facts. Summarize only what is visible in the supplied runtime context. "
        "Write Chinese output with clear section labels."
    )


def _summary_user_prompt(
    *,
    state: AgentState,
    projected_messages: tuple[AgentMessage, ...],
    working_set: WorkingSet,
    delta: StateDelta | None,
    budget: ContextBudgetReport | None,
    trigger: PhaseSummaryTrigger,
) -> str:
    """构造给摘要模型的输入。"""
    payload = {
        "trigger": trigger.to_dict(),
        "working_set": working_set.to_dict(),
        "state_delta": delta.to_dict() if delta else {},
        "budget": budget.to_dict() if budget else {},
        "source_query_id": state.query_id,
    }
    return "\n".join(
        (
            "请为以下 DutyFlow 运行阶段生成阶段摘要。",
            "必须覆盖：Current Goal、Known Facts、Identity Context、Decision Context、Next Step、Anchors。",
            "摘要只供运行时压缩和审计使用，不要给用户写寒暄。",
            "",
            "## Runtime Control",
            "",
            json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2),
            "",
            "## Projected Messages Preview",
            "",
            _render_messages_preview(projected_messages),
        )
    )


def _render_messages_preview(messages: tuple[AgentMessage, ...]) -> str:
    """把模型可见 messages 渲染为摘要输入预览。"""
    chunks: list[str] = []
    total = 0
    for index, message in enumerate(messages):
        item = _render_message(index, message)
        remaining = SUMMARY_CONTEXT_PREVIEW_MAX_CHARS - total
        if remaining <= 0:
            break
        if len(item) > remaining:
            chunks.append(item[: max(0, remaining - 20)] + "\n...[truncated]")
            break
        chunks.append(item)
        total += len(item)
    return "\n\n".join(chunks)


def _render_message(index: int, message: AgentMessage) -> str:
    """渲染单条消息。"""
    blocks = "\n".join(_render_block(block) for block in message.content)
    return f"[{index}] role={message.role}\n{blocks}"


def _render_block(block: AgentContentBlock) -> str:
    """渲染单个 block 的摘要输入文本。"""
    if block.type == "text":
        return block.text
    if block.type == "tool_use":
        return (
            f"tool_use id={block.tool_use_id} name={block.tool_name} "
            f"input={json.dumps(dict(block.tool_input), ensure_ascii=False, sort_keys=True)}"
        )
    if block.type == "tool_result":
        return (
            f"tool_result id={block.tool_use_id} name={block.tool_name} "
            f"is_error={block.is_error} content={block.content}"
        )
    return block.text or block.content


def _extract_text(blocks: tuple[AgentContentBlock, ...]) -> str:
    """提取模型摘要响应文本。"""
    return "\n".join(block.text for block in blocks if block.type == "text" and block.text).strip()


def _clamp_summary_text(text: str) -> str:
    """限制摘要正文长度。"""
    normalized = str(text).strip()
    if not normalized:
        return "未生成阶段摘要。"
    if len(normalized) <= SUMMARY_TEXT_MAX_CHARS:
        return normalized
    return normalized[: SUMMARY_TEXT_MAX_CHARS - 14] + "\n...[truncated]"


def _extract_anchors(state: AgentState, messages: tuple[AgentMessage, ...]) -> dict[str, tuple[str, ...]]:
    """从 state 和 messages 中提取关键锚点。"""
    task_ids: list[str] = []
    event_ids: list[str] = []
    tool_use_ids: list[str] = []
    approval_ids: list[str] = []
    _append_if_present(task_ids, state.current_task_id)
    _append_if_present(task_ids, state.task_control.task_id)
    _append_if_present(event_ids, state.current_event_id)
    for message in messages:
        for block in message.content:
            _append_if_present(tool_use_ids, block.tool_use_id)
            for value in _ids_from_block(block):
                if value.startswith("task_"):
                    _append_if_present(task_ids, value)
                elif value.startswith("evt_"):
                    _append_if_present(event_ids, value)
                elif value.startswith("approval_"):
                    _append_if_present(approval_ids, value)
                elif value.startswith(("tool_", "call_")):
                    _append_if_present(tool_use_ids, value)
    return {
        "task_ids": tuple(task_ids),
        "event_ids": tuple(event_ids),
        "tool_use_ids": tuple(tool_use_ids),
        "approval_ids": tuple(approval_ids),
    }


def _ids_from_block(block: AgentContentBlock) -> tuple[str, ...]:
    """从 block 可见文本和输入 JSON 中提取 ID 形态锚点。"""
    text = "\n".join(
        (
            block.text,
            block.content,
            block.tool_name,
            json.dumps(dict(block.tool_input), ensure_ascii=False, sort_keys=True),
        )
    )
    return tuple(ID_PATTERN.findall(text))


def _append_if_present(items: list[str], value: str) -> None:
    """追加非空且未出现过的值。"""
    normalized = str(value).strip()
    if normalized and normalized not in items:
        items.append(normalized)


def _ordered_unique(values: tuple[str, ...]) -> tuple[str, ...]:
    """按出现顺序去重。"""
    items: list[str] = []
    for value in values:
        _append_if_present(items, value)
    return tuple(items)


def _record_frontmatter(record: PhaseSummaryRecord) -> dict[str, str]:
    """构造 context summary frontmatter。"""
    return {
        "schema": CONTEXT_SUMMARY_SCHEMA,
        "id": record.summary_id,
        "task_id": record.task_id,
        "event_ids": _join_ids(record.event_ids),
        "created_at": record.created_at,
        "compact_level": record.compact_level,
        "summary_kind": record.summary_kind,
        "phase": record.phase,
        "trigger_reason": record.trigger_reason,
        "trigger_mode": record.trigger_mode,
        "source_query_id": record.source_query_id,
        "source_message_count": str(record.source_message_count),
        "estimated_tokens": str(record.estimated_tokens),
        "soft_token_limit": str(record.soft_token_limit),
        "hard_token_limit": str(record.hard_token_limit),
        "phase_boundary_detected": str(record.phase_boundary_detected).lower(),
        "requires_llm": str(record.requires_llm).lower(),
        "anchor_task_ids": _join_ids(record.anchor_task_ids),
        "anchor_event_ids": _join_ids(record.anchor_event_ids),
        "anchor_tool_use_ids": _join_ids(record.anchor_tool_use_ids),
        "anchor_approval_ids": _join_ids(record.anchor_approval_ids),
    }


def _record_body(record: PhaseSummaryRecord) -> str:
    """渲染阶段摘要正文。"""
    return "\n".join(
        (
            f"# Context {record.summary_id}",
            "",
            "## Phase Summary",
            "",
            record.summary_text,
            "",
            "## Trigger",
            "",
            f"- phase: {record.phase}",
            f"- trigger_reason: {record.trigger_reason}",
            f"- trigger_mode: {record.trigger_mode}",
            f"- estimated_tokens: {record.estimated_tokens}",
            "",
            "## Anchors",
            "",
            f"- task_ids: {_join_ids(record.anchor_task_ids)}",
            f"- event_ids: {_join_ids(record.anchor_event_ids)}",
            f"- tool_use_ids: {_join_ids(record.anchor_tool_use_ids)}",
            f"- approval_ids: {_join_ids(record.anchor_approval_ids)}",
            "",
        )
    )


def _record_from_document(
    project_root: Path,
    path: Path,
    document: MarkdownDocument,
    summary_text: str,
) -> PhaseSummaryRecord:
    """从 MarkdownDocument 重建 PhaseSummaryRecord。"""
    meta = document.frontmatter
    return PhaseSummaryRecord(
        path=path,
        relative_path=_relative_path(project_root, path),
        summary_id=meta.get("id", ""),
        summary_kind=meta.get("summary_kind", ""),
        phase=meta.get("phase", ""),
        trigger_reason=meta.get("trigger_reason", ""),
        trigger_mode=meta.get("trigger_mode", ""),
        source_query_id=meta.get("source_query_id", ""),
        task_id=meta.get("task_id", ""),
        event_ids=_split_ids(meta.get("event_ids", "")),
        created_at=meta.get("created_at", ""),
        compact_level=meta.get("compact_level", ""),
        source_message_count=_to_int(meta.get("source_message_count", "")),
        estimated_tokens=_to_int(meta.get("estimated_tokens", "")),
        soft_token_limit=_to_int(meta.get("soft_token_limit", "")),
        hard_token_limit=_to_int(meta.get("hard_token_limit", "")),
        phase_boundary_detected=meta.get("phase_boundary_detected", "") == "true",
        requires_llm=meta.get("requires_llm", "") == "true",
        anchor_task_ids=_split_ids(meta.get("anchor_task_ids", "")),
        anchor_event_ids=_split_ids(meta.get("anchor_event_ids", "")),
        anchor_tool_use_ids=_split_ids(meta.get("anchor_tool_use_ids", "")),
        anchor_approval_ids=_split_ids(meta.get("anchor_approval_ids", "")),
        summary_text=summary_text,
    )


def _join_ids(values: tuple[str, ...]) -> str:
    """把 ID tuple 写成简单 frontmatter 字符串。"""
    return ",".join(value for value in values if value)


def _split_ids(value: str) -> tuple[str, ...]:
    """解析逗号分隔 ID 字段。"""
    return tuple(item.strip() for item in str(value).split(",") if item.strip())


def _to_int(value: str) -> int:
    """把 frontmatter 字符串恢复为 int。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _generate_context_id() -> str:
    """生成 context summary ID。"""
    return "ctx_" + uuid4().hex[:12]


def _relative_path(project_root: Path, path: Path) -> str:
    """把路径转换成项目内相对路径。"""
    try:
        return str(path.resolve().relative_to(project_root))
    except ValueError:
        return str(path)


def _now_iso() -> str:
    """返回当前本地时区 ISO-8601 时间字符串。"""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _self_test() -> None:
    """验证阶段摘要策略和落盘结构。"""
    import tempfile

    state = AgentState(
        query_id="query_phase_selftest",
        messages=(
            AgentMessage(role="user", content=(AgentContentBlock(type="text", text="hello task_001"),)),
        ),
    )
    working_set = WorkingSet(
        query_id="query_phase_selftest",
        turn_count=1,
        transition_reason="start",
        current_event_id="evt_001",
        current_task_id="task_001",
        latest_user_text="hello",
        latest_assistant_text="",
        pending_tool_use_ids=(),
        last_tool_result_ids=(),
        recent_tool_use_ids=(),
        recent_tool_names=(),
        task_weight_level="",
        approval_status="none",
        retry_status="none",
        next_action="",
        latest_interruption_reason="",
        latest_resume_point="",
        waiting_recovery_scope_ids=(),
    )
    policy = PhaseSummaryPolicy(soft_token_limit=10, hard_token_limit=20)
    trigger = policy.evaluate(state=state, working_set=working_set, delta=None, budget=None)
    assert trigger.reason == "phase_boundary_only"
    with tempfile.TemporaryDirectory() as temp_dir:
        store = PhaseSummaryStore(Path(temp_dir))
        service = PhaseSummaryService(store=store)
        record = service._build_record(state, state.messages, trigger, "摘要")
        saved = store.write_summary(record)
        loaded = store.read_summary(saved.summary_id)
    assert loaded is not None
    assert loaded.summary_text == "摘要"


if __name__ == "__main__":
    _self_test()
    print("dutyflow phase summary self-test passed")
