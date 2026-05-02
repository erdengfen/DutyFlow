# 本文件负责把上下文投影、压缩和阶段摘要动作写入本地 Compression Journal。

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from dutyflow.agent.state import AgentContentBlock, AgentMessage, AgentState
from dutyflow.context.context_budget import ContextBudgetReport
from dutyflow.storage.file_store import FileStore
from dutyflow.storage.markdown_store import MarkdownDocument, MarkdownStore


COMPRESSION_JOURNAL_SCHEMA = "dutyflow.context_compression_journal.v1"
ACTION_TYPES = frozenset(
    {
        "model_context_projection",
        "micro_compact",
        "phase_boundary",
        "phase_summary",
        "evidence_offload",
        "manual_compress",
        "emergency_compact",
    }
)
# 关键开关：journal notes 只保留 1000 字，避免审计记录复制长工具结果。
NOTES_MAX_CHARS = 1000
ID_PATTERN = re.compile(r"\b(?:task|evt|approval|per|tool|call)_[A-Za-z0-9_:-]+\b")


@dataclass(frozen=True)
class CompressionJournalRecord:
    """表示一次上下文投影或压缩动作的可审计记录。"""

    path: Path
    relative_path: str
    journal_id: str
    action_type: str
    trigger_reason: str
    query_id: str
    task_id: str
    event_id: str
    created_at: str
    source_message_count: int
    projected_message_count: int
    source_tool_result_count: int
    projected_tool_receipt_count: int
    projected_active_tool_result_count: int
    estimated_tokens: int
    compacted_tool_result_ids: tuple[str, ...]
    generated_tool_receipt_ids: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    phase_summary_id: str
    phase_summary_file: str
    health_check_status: str
    preserved_task_ids: tuple[str, ...]
    preserved_event_ids: tuple[str, ...]
    preserved_tool_use_ids: tuple[str, ...]
    preserved_approval_ids: tuple[str, ...]
    notes: str

    def to_dict(self) -> dict[str, object]:
        """返回可序列化调试结构。"""
        payload = asdict(self)
        payload["path"] = str(self.path)
        for key in _TUPLE_FIELDS:
            payload[key] = list(payload[key])
        return payload


class CompressionJournalStore:
    """封装 `data/contexts/journal/ctxj_<id>.md` 的写入和读取。"""

    def __init__(self, project_root: Path, *, markdown_store: MarkdownStore | None = None) -> None:
        """绑定项目目录并准备 journal 目录。"""
        self.project_root = Path(project_root).resolve()
        self.markdown_store = markdown_store or MarkdownStore(FileStore(self.project_root))
        self.journal_dir = self.project_root / "data" / "contexts" / "journal"
        self.markdown_store.file_store.ensure_dir(self.journal_dir)

    def write_projection_change(
        self,
        *,
        state: AgentState,
        source_messages: tuple[AgentMessage, ...],
        projected_messages: tuple[AgentMessage, ...],
        budget: ContextBudgetReport | None,
        trigger_reason: str = "tool_result_clearing",
        health_check_status: str = "not_run",
        notes: str = "",
    ) -> CompressionJournalRecord:
        """记录一次 canonical messages 到 projected messages 的可见变化。"""
        compacted_ids = _compacted_tool_result_ids(source_messages, projected_messages)
        action_type = "micro_compact" if compacted_ids else "model_context_projection"
        record = _build_record(
            self.project_root,
            self.journal_dir,
            state=state,
            action_type=action_type,
            trigger_reason=trigger_reason,
            source_messages=source_messages,
            projected_messages=projected_messages,
            budget=budget,
            compacted_tool_result_ids=compacted_ids,
            generated_tool_receipt_ids=compacted_ids,
            health_check_status=health_check_status,
            notes=notes or "模型上下文投影发生可见变化。",
        )
        return self._write_record(record)

    def write_phase_summary_event(
        self,
        *,
        state: AgentState,
        projected_messages: tuple[AgentMessage, ...],
        budget: ContextBudgetReport | None,
        trigger,
        phase_summary_record=None,
        health_check_status: str = "not_run",
    ) -> CompressionJournalRecord:
        """记录阶段边界或 LLM 阶段摘要动作。"""
        action_type = "phase_summary" if getattr(trigger, "requires_llm", False) else "phase_boundary"
        if getattr(trigger, "reason", "") == "context_overflow":
            action_type = "phase_summary"
        record = _build_record(
            self.project_root,
            self.journal_dir,
            state=state,
            action_type=action_type,
            trigger_reason=getattr(trigger, "reason", "none"),
            source_messages=projected_messages,
            projected_messages=projected_messages,
            budget=budget,
            phase_summary_id=getattr(phase_summary_record, "summary_id", ""),
            phase_summary_file=getattr(phase_summary_record, "relative_path", ""),
            health_check_status=health_check_status,
            notes=_phase_summary_notes(trigger, phase_summary_record),
        )
        return self._write_record(record)

    def read_journal(self, journal_id: str) -> CompressionJournalRecord | None:
        """按 journal ID 读取记录。"""
        path = self.journal_dir / f"{journal_id}.md"
        if not self.markdown_store.exists(path):
            return None
        document = self.markdown_store.read_document(path)
        notes = self.markdown_store.extract_section(path, "Notes")
        return _record_from_document(self.project_root, path, document, notes)

    def list_journals(self) -> tuple[CompressionJournalRecord, ...]:
        """列出当前 journal 目录下的记录。"""
        records = [self.read_journal(path.stem) for path in sorted(self.journal_dir.glob("ctxj_*.md"))]
        return tuple(record for record in records if record is not None)

    def _write_record(self, record: CompressionJournalRecord) -> CompressionJournalRecord:
        """把 journal 记录写入 Markdown。"""
        document = MarkdownDocument(frontmatter=_frontmatter(record), body=_body(record))
        self.markdown_store.write_document(record.path, document)
        return record


def _build_record(
    project_root: Path,
    journal_dir: Path,
    *,
    state: AgentState,
    action_type: str,
    trigger_reason: str,
    source_messages: tuple[AgentMessage, ...],
    projected_messages: tuple[AgentMessage, ...],
    budget: ContextBudgetReport | None,
    compacted_tool_result_ids: tuple[str, ...] = (),
    generated_tool_receipt_ids: tuple[str, ...] = (),
    evidence_refs: tuple[str, ...] = (),
    phase_summary_id: str = "",
    phase_summary_file: str = "",
    health_check_status: str = "not_run",
    notes: str = "",
) -> CompressionJournalRecord:
    """构造 journal 记录并收集锚点。"""
    _validate_action_type(action_type)
    journal_id = _generate_journal_id()
    path = journal_dir / f"{journal_id}.md"
    anchors = _extract_anchors(state, source_messages + projected_messages)
    return CompressionJournalRecord(
        path=path,
        relative_path=_relative_path(project_root, path),
        journal_id=journal_id,
        action_type=action_type,
        trigger_reason=str(trigger_reason).strip() or "none",
        query_id=state.query_id,
        task_id=state.current_task_id or state.task_control.task_id,
        event_id=state.current_event_id,
        created_at=_now_iso(),
        source_message_count=len(source_messages),
        projected_message_count=len(projected_messages),
        source_tool_result_count=_tool_result_count(source_messages),
        projected_tool_receipt_count=_tool_receipt_count(projected_messages),
        projected_active_tool_result_count=_active_tool_result_count(projected_messages),
        estimated_tokens=budget.total_estimated_tokens if budget else 0,
        compacted_tool_result_ids=compacted_tool_result_ids,
        generated_tool_receipt_ids=generated_tool_receipt_ids,
        evidence_refs=evidence_refs,
        phase_summary_id=phase_summary_id,
        phase_summary_file=phase_summary_file,
        health_check_status=health_check_status,
        preserved_task_ids=_ordered_unique((state.current_task_id, state.task_control.task_id, *anchors["task_ids"])),
        preserved_event_ids=_ordered_unique((state.current_event_id, *anchors["event_ids"])),
        preserved_tool_use_ids=anchors["tool_use_ids"],
        preserved_approval_ids=anchors["approval_ids"],
        notes=_clamp_notes(notes),
    )


def _frontmatter(record: CompressionJournalRecord) -> dict[str, str]:
    """构造 journal frontmatter。"""
    return {
        "schema": COMPRESSION_JOURNAL_SCHEMA,
        "id": record.journal_id,
        "action_type": record.action_type,
        "trigger_reason": record.trigger_reason,
        "query_id": record.query_id,
        "task_id": record.task_id,
        "event_id": record.event_id,
        "created_at": record.created_at,
        "source_message_count": str(record.source_message_count),
        "projected_message_count": str(record.projected_message_count),
        "source_tool_result_count": str(record.source_tool_result_count),
        "projected_tool_receipt_count": str(record.projected_tool_receipt_count),
        "projected_active_tool_result_count": str(record.projected_active_tool_result_count),
        "estimated_tokens": str(record.estimated_tokens),
        "compacted_tool_result_ids": _join(record.compacted_tool_result_ids),
        "generated_tool_receipt_ids": _join(record.generated_tool_receipt_ids),
        "evidence_refs": _join(record.evidence_refs),
        "phase_summary_id": record.phase_summary_id,
        "phase_summary_file": record.phase_summary_file,
        "health_check_status": record.health_check_status,
        "preserved_task_ids": _join(record.preserved_task_ids),
        "preserved_event_ids": _join(record.preserved_event_ids),
        "preserved_tool_use_ids": _join(record.preserved_tool_use_ids),
        "preserved_approval_ids": _join(record.preserved_approval_ids),
    }


def _body(record: CompressionJournalRecord) -> str:
    """渲染 journal 正文。"""
    return "\n".join(
        (
            f"# Compression Journal {record.journal_id}",
            "",
            "## Summary",
            "",
            f"- action_type: {record.action_type}",
            f"- trigger_reason: {record.trigger_reason}",
            f"- query_id: {record.query_id}",
            "",
            "## Scope",
            "",
            f"- source_message_count: {record.source_message_count}",
            f"- projected_message_count: {record.projected_message_count}",
            f"- estimated_tokens: {record.estimated_tokens}",
            "",
            "## Preserved Anchors",
            "",
            f"- task_ids: {_join(record.preserved_task_ids)}",
            f"- event_ids: {_join(record.preserved_event_ids)}",
            f"- tool_use_ids: {_join(record.preserved_tool_use_ids)}",
            f"- approval_ids: {_join(record.preserved_approval_ids)}",
            "",
            "## Generated Artifacts",
            "",
            f"- compacted_tool_result_ids: {_join(record.compacted_tool_result_ids)}",
            f"- generated_tool_receipt_ids: {_join(record.generated_tool_receipt_ids)}",
            f"- evidence_refs: {_join(record.evidence_refs)}",
            f"- phase_summary_id: {record.phase_summary_id}",
            f"- phase_summary_file: {record.phase_summary_file}",
            f"- health_check_status: {record.health_check_status}",
            "",
            "## Notes",
            "",
            record.notes,
            "",
        )
    )


def _record_from_document(
    project_root: Path,
    path: Path,
    document: MarkdownDocument,
    notes: str,
) -> CompressionJournalRecord:
    """从 MarkdownDocument 恢复 journal 记录。"""
    meta = document.frontmatter
    return CompressionJournalRecord(
        path=path,
        relative_path=_relative_path(project_root, path),
        journal_id=meta.get("id", ""),
        action_type=meta.get("action_type", ""),
        trigger_reason=meta.get("trigger_reason", ""),
        query_id=meta.get("query_id", ""),
        task_id=meta.get("task_id", ""),
        event_id=meta.get("event_id", ""),
        created_at=meta.get("created_at", ""),
        source_message_count=_to_int(meta.get("source_message_count", "")),
        projected_message_count=_to_int(meta.get("projected_message_count", "")),
        source_tool_result_count=_to_int(meta.get("source_tool_result_count", "")),
        projected_tool_receipt_count=_to_int(meta.get("projected_tool_receipt_count", "")),
        projected_active_tool_result_count=_to_int(meta.get("projected_active_tool_result_count", "")),
        estimated_tokens=_to_int(meta.get("estimated_tokens", "")),
        compacted_tool_result_ids=_split(meta.get("compacted_tool_result_ids", "")),
        generated_tool_receipt_ids=_split(meta.get("generated_tool_receipt_ids", "")),
        evidence_refs=_split(meta.get("evidence_refs", "")),
        phase_summary_id=meta.get("phase_summary_id", ""),
        phase_summary_file=meta.get("phase_summary_file", ""),
        health_check_status=meta.get("health_check_status", ""),
        preserved_task_ids=_split(meta.get("preserved_task_ids", "")),
        preserved_event_ids=_split(meta.get("preserved_event_ids", "")),
        preserved_tool_use_ids=_split(meta.get("preserved_tool_use_ids", "")),
        preserved_approval_ids=_split(meta.get("preserved_approval_ids", "")),
        notes=notes,
    )


def _compacted_tool_result_ids(
    source_messages: tuple[AgentMessage, ...],
    projected_messages: tuple[AgentMessage, ...],
) -> tuple[str, ...]:
    """识别从原文 tool result 变成 Tool Receipt 的工具调用 ID。"""
    ids: list[str] = []
    for source, projected in zip(source_messages, projected_messages, strict=False):
        for source_block, projected_block in zip(source.content, projected.content, strict=False):
            if _is_compacted_tool_result(source_block, projected_block):
                _append_unique(ids, source_block.tool_use_id)
    return tuple(ids)


def _is_compacted_tool_result(source: AgentContentBlock, projected: AgentContentBlock) -> bool:
    """判断一个 block 是否在投影中被收据化。"""
    return (
        source.type == "tool_result"
        and projected.type == "tool_result"
        and source.tool_use_id == projected.tool_use_id
        and not _is_tool_receipt_text(source.content)
        and _is_tool_receipt_text(projected.content)
    )


def _extract_anchors(state: AgentState, messages: tuple[AgentMessage, ...]) -> dict[str, tuple[str, ...]]:
    """从 state 和 messages 中收集不能丢失的锚点。"""
    task_ids: list[str] = []
    event_ids: list[str] = []
    tool_use_ids: list[str] = []
    approval_ids: list[str] = []
    _append_unique(task_ids, state.current_task_id)
    _append_unique(task_ids, state.task_control.task_id)
    _append_unique(event_ids, state.current_event_id)
    for message in messages:
        _collect_message_anchors(message, task_ids, event_ids, tool_use_ids, approval_ids)
    return {
        "task_ids": tuple(task_ids),
        "event_ids": tuple(event_ids),
        "tool_use_ids": tuple(tool_use_ids),
        "approval_ids": tuple(approval_ids),
    }


def _collect_message_anchors(
    message: AgentMessage,
    task_ids: list[str],
    event_ids: list[str],
    tool_use_ids: list[str],
    approval_ids: list[str],
) -> None:
    """收集单条消息内的锚点。"""
    for block in message.content:
        _append_unique(tool_use_ids, block.tool_use_id)
        for value in _ids_from_block(block):
            if value.startswith("task_"):
                _append_unique(task_ids, value)
            elif value.startswith("evt_"):
                _append_unique(event_ids, value)
            elif value.startswith("approval_"):
                _append_unique(approval_ids, value)
            elif value.startswith(("tool_", "call_")):
                _append_unique(tool_use_ids, value)


def _ids_from_block(block: AgentContentBlock) -> tuple[str, ...]:
    """从 block 的可见内容中提取 ID 形态锚点。"""
    text = "\n".join(
        (
            block.text,
            block.content,
            block.tool_name,
            json.dumps(dict(block.tool_input), ensure_ascii=False, sort_keys=True),
        )
    )
    return tuple(ID_PATTERN.findall(text))


def _tool_result_count(messages: tuple[AgentMessage, ...]) -> int:
    """统计 tool_result block 数量。"""
    return sum(1 for message in messages for block in message.content if block.type == "tool_result")


def _tool_receipt_count(messages: tuple[AgentMessage, ...]) -> int:
    """统计投影上下文中的 Tool Receipt 数量。"""
    return sum(1 for message in messages for block in message.content if _is_tool_receipt_block(block))


def _active_tool_result_count(messages: tuple[AgentMessage, ...]) -> int:
    """统计仍保留原文的 tool_result 数量。"""
    return sum(
        1 for message in messages for block in message.content if block.type == "tool_result" and not _is_tool_receipt_block(block)
    )


def _is_tool_receipt_block(block: AgentContentBlock) -> bool:
    """判断 block 是否为 Tool Receipt。"""
    return block.type == "tool_result" and _is_tool_receipt_text(block.content)


def _is_tool_receipt_text(content: str) -> bool:
    """判断文本是否是 Tool Receipt 表示。"""
    return str(content).strip().startswith("ToolReceipt(")


def _phase_summary_notes(trigger, phase_summary_record) -> str:
    """生成阶段摘要 journal 说明。"""
    if phase_summary_record is None:
        return f"阶段边界已记录，trigger_reason={getattr(trigger, 'reason', 'none')}，未调用 LLM 摘要。"
    return (
        f"阶段摘要已生成，trigger_reason={getattr(trigger, 'reason', 'none')}，"
        f"phase_summary_id={getattr(phase_summary_record, 'summary_id', '')}。"
    )


def _validate_action_type(action_type: str) -> None:
    """校验 journal 动作类型。"""
    if action_type not in ACTION_TYPES:
        raise ValueError(f"Unknown compression journal action_type: {action_type}")


def _append_unique(items: list[str], value: str) -> None:
    """追加非空且未出现过的字符串。"""
    normalized = str(value).strip()
    if normalized and normalized not in items:
        items.append(normalized)


def _ordered_unique(values: tuple[str, ...]) -> tuple[str, ...]:
    """按出现顺序去重。"""
    items: list[str] = []
    for value in values:
        _append_unique(items, value)
    return tuple(items)


def _join(values: tuple[str, ...]) -> str:
    """把 tuple 字段写成简单 frontmatter 字符串。"""
    return ",".join(value for value in values if value)


def _split(value: str) -> tuple[str, ...]:
    """解析逗号分隔 frontmatter 字段。"""
    return tuple(item.strip() for item in str(value).split(",") if item.strip())


def _to_int(value: str) -> int:
    """把 frontmatter 字符串恢复为整数。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _clamp_notes(notes: str) -> str:
    """限制 notes 长度。"""
    normalized = str(notes).strip()
    if len(normalized) <= NOTES_MAX_CHARS:
        return normalized
    return normalized[: NOTES_MAX_CHARS - len("\n...[truncated]")] + "\n...[truncated]"


def _generate_journal_id() -> str:
    """生成 journal ID。"""
    return "ctxj_" + uuid4().hex[:12]


def _relative_path(project_root: Path, path: Path) -> str:
    """把绝对路径转换为项目内相对路径。"""
    try:
        return str(path.resolve().relative_to(project_root))
    except ValueError:
        return str(path)


def _now_iso() -> str:
    """返回当前本地时区 ISO-8601 时间字符串。"""
    return datetime.now().astimezone().isoformat(timespec="seconds")


_TUPLE_FIELDS = frozenset(
    {
        "compacted_tool_result_ids",
        "generated_tool_receipt_ids",
        "evidence_refs",
        "preserved_task_ids",
        "preserved_event_ids",
        "preserved_tool_use_ids",
        "preserved_approval_ids",
    }
)


def _self_test() -> None:
    """验证 journal store 可以写入并读回 micro-compact 记录。"""
    import tempfile

    source = (
        AgentMessage(
            role="user",
            content=(
                AgentContentBlock(
                    type="tool_result",
                    tool_use_id="tool_selftest",
                    tool_name="sample_tool",
                    content='{"task_id":"task_selftest"}',
                ),
            ),
        ),
    )
    projected = (
        AgentMessage(
            role="user",
            content=(
                AgentContentBlock(
                    type="tool_result",
                    tool_use_id="tool_selftest",
                    tool_name="sample_tool",
                    content="ToolReceipt(tool_use_id=tool_selftest,status=success)",
                ),
            ),
        ),
    )
    state = AgentState(query_id="query_selftest", messages=source, current_task_id="task_selftest")
    with tempfile.TemporaryDirectory() as temp_dir:
        store = CompressionJournalStore(Path(temp_dir))
        created = store.write_projection_change(
            state=state,
            source_messages=source,
            projected_messages=projected,
            budget=None,
        )
        loaded = store.read_journal(created.journal_id)
    assert loaded is not None
    assert loaded.action_type == "micro_compact"
    assert loaded.compacted_tool_result_ids == ("tool_selftest",)


if __name__ == "__main__":
    _self_test()
    print("dutyflow compression journal self-test passed")
