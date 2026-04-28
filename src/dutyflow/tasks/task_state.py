# 本文件负责 Step 7 第一版任务 Markdown 的创建、读取、更新和枚举。

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from dutyflow.storage.file_store import FileStore
from dutyflow.storage.markdown_store import MarkdownDocument, MarkdownStore


@dataclass(frozen=True)
class TaskRecord:
    """表示一条已落盘并可被后续任务系统消费的任务记录。"""

    path: Path
    task_id: str
    title: str
    status: str
    weight_level: str
    source_event_id: str
    sender_contact_id: str
    source_id: str
    approval_id: str
    run_mode: str
    scheduled_for: str
    execution_profile: str
    requested_capabilities: str
    resolved_skills: str
    resolved_tools: str
    resume_point: str
    resume_payload: str
    next_retry_at: str
    created_at: str
    updated_at: str
    summary: str
    attempt_count: str
    retry_status: str
    approval_status: str
    last_result_summary: str
    identity_and_responsibility: str
    decision_trace: str
    next_action: str


class TaskStore:
    """封装 `data/tasks/task_<id>.md` 的最小任务状态存储。"""

    def __init__(
        self,
        project_root: Path,
        *,
        markdown_store: MarkdownStore | None = None,
    ) -> None:
        """绑定工作区并准备任务目录。"""
        self.project_root = Path(project_root).resolve()
        self.markdown_store = markdown_store or MarkdownStore(FileStore(self.project_root))
        self.tasks_dir = self.project_root / "data" / "tasks"
        self.markdown_store.file_store.ensure_dir(self.tasks_dir)

    def create_task(
        self,
        *,
        title: str,
        task_id: str = "",
        status: str = "queued",
        weight_level: str = "normal",
        source_event_id: str = "",
        sender_contact_id: str = "",
        source_id: str = "",
        approval_id: str = "",
        run_mode: str = "async_now",
        scheduled_for: str = "",
        execution_profile: str = "",
        requested_capabilities: str = "",
        resolved_skills: str = "",
        resolved_tools: str = "",
        resume_point: str = "",
        resume_payload: str = "",
        next_retry_at: str = "",
        summary: str = "",
        attempt_count: str = "0",
        retry_status: str = "none",
        approval_status: str = "none",
        last_result_summary: str = "",
        identity_and_responsibility: str = "",
        decision_trace: str = "",
        next_action: str = "",
    ) -> TaskRecord:
        """创建一条新的任务 Markdown 记录并立即落盘。"""
        now = _now_iso()
        record = TaskRecord(
            path=_build_task_path(self.tasks_dir, task_id or _generate_task_id()),
            task_id=task_id or _generate_task_id(),
            title=title.strip(),
            status=status.strip() or "queued",
            weight_level=weight_level.strip() or "normal",
            source_event_id=source_event_id.strip(),
            sender_contact_id=sender_contact_id.strip(),
            source_id=source_id.strip(),
            approval_id=approval_id.strip(),
            run_mode=run_mode.strip() or "async_now",
            scheduled_for=scheduled_for.strip(),
            execution_profile=execution_profile.strip(),
            requested_capabilities=requested_capabilities.strip(),
            resolved_skills=resolved_skills.strip(),
            resolved_tools=resolved_tools.strip(),
            resume_point=resume_point.strip(),
            resume_payload=resume_payload.strip(),
            next_retry_at=next_retry_at.strip(),
            created_at=now,
            updated_at=now,
            summary=summary.strip(),
            attempt_count=attempt_count.strip() or "0",
            retry_status=retry_status.strip() or "none",
            approval_status=approval_status.strip() or "none",
            last_result_summary=last_result_summary.strip(),
            identity_and_responsibility=identity_and_responsibility.strip(),
            decision_trace=decision_trace.strip(),
            next_action=next_action.strip(),
        )
        self._write_record(record)
        return record

    def read_task(self, task_id: str) -> TaskRecord | None:
        """按稳定任务 ID 读取一条任务记录。"""
        path = _build_task_path(self.tasks_dir, task_id)
        if not self.markdown_store.exists(path):
            return None
        return self._read_record(path)

    def update_task(
        self,
        task_id: str,
        *,
        frontmatter_updates: dict[str, str] | None = None,
        state_updates: dict[str, str] | None = None,
        section_updates: dict[str, str] | None = None,
    ) -> TaskRecord:
        """更新任务 frontmatter、当前状态字段或正文 section。"""
        record = self.read_task(task_id)
        if record is None:
            raise FileNotFoundError(f"task not found: {task_id}")
        updates = _build_record_updates(record, frontmatter_updates, state_updates, section_updates)
        updated = _replace_record(record, updates)
        self._write_record(updated)
        return updated

    def list_tasks(self) -> tuple[TaskRecord, ...]:
        """枚举当前工作区内全部任务记录。"""
        records = [self._read_record(path) for path in sorted(self.tasks_dir.glob("task_*.md"))]
        records.sort(key=lambda item: (item.created_at, item.task_id))
        return tuple(records)

    def _write_record(self, record: TaskRecord) -> None:
        """把任务对象渲染为 Markdown 并写入本地。"""
        document = MarkdownDocument(frontmatter=_build_frontmatter(record), body=_build_body(record))
        self.markdown_store.write_document(record.path, document)

    def _read_record(self, path: Path) -> TaskRecord:
        """从已落盘 Markdown 重建任务对象。"""
        document = self.markdown_store.read_document(path)
        current_state = _parse_key_value_section(self.markdown_store.extract_section(path, "Current State"))
        return TaskRecord(
            path=path,
            task_id=document.frontmatter.get("id", ""),
            title=document.frontmatter.get("title", ""),
            status=document.frontmatter.get("status", ""),
            weight_level=document.frontmatter.get("weight_level", ""),
            source_event_id=document.frontmatter.get("source_event_id", ""),
            sender_contact_id=document.frontmatter.get("sender_contact_id", ""),
            source_id=document.frontmatter.get("source_id", ""),
            approval_id=document.frontmatter.get("approval_id", ""),
            run_mode=document.frontmatter.get("run_mode", ""),
            scheduled_for=document.frontmatter.get("scheduled_for", ""),
            execution_profile=document.frontmatter.get("execution_profile", ""),
            requested_capabilities=document.frontmatter.get("requested_capabilities", ""),
            resolved_skills=document.frontmatter.get("resolved_skills", ""),
            resolved_tools=document.frontmatter.get("resolved_tools", ""),
            resume_point=document.frontmatter.get("resume_point", ""),
            resume_payload=document.frontmatter.get("resume_payload", ""),
            next_retry_at=document.frontmatter.get("next_retry_at", ""),
            created_at=document.frontmatter.get("created_at", ""),
            updated_at=document.frontmatter.get("updated_at", ""),
            summary=self.markdown_store.extract_section(path, "Summary"),
            attempt_count=current_state.get("attempt_count", ""),
            retry_status=current_state.get("retry_status", ""),
            approval_status=current_state.get("approval_status", ""),
            last_result_summary=current_state.get("last_result_summary", ""),
            identity_and_responsibility=self.markdown_store.extract_section(path, "Identity And Responsibility"),
            decision_trace=self.markdown_store.extract_section(path, "Decision Trace"),
            next_action=self.markdown_store.extract_section(path, "Next Action"),
        )


def _build_record_updates(
    record: TaskRecord,
    frontmatter_updates: dict[str, str] | None,
    state_updates: dict[str, str] | None,
    section_updates: dict[str, str] | None,
) -> dict[str, str]:
    """把三类更新请求合并成统一字段字典。"""
    updates = {"updated_at": _now_iso()}
    _merge_text_updates(updates, frontmatter_updates)
    _merge_text_updates(updates, state_updates)
    _merge_text_updates(updates, section_updates)
    updates.setdefault("title", record.title)
    updates.setdefault("status", record.status)
    updates.setdefault("weight_level", record.weight_level)
    updates.setdefault("attempt_count", record.attempt_count)
    updates.setdefault("retry_status", record.retry_status)
    updates.setdefault("approval_status", record.approval_status)
    updates.setdefault("last_result_summary", record.last_result_summary)
    return updates


def _merge_text_updates(target: dict[str, str], source: dict[str, str] | None) -> None:
    """只合并显式传入的字符串更新，避免空对象污染记录。"""
    if not source:
        return
    for key, value in source.items():
        target[key] = value.strip()


def _replace_record(record: TaskRecord, updates: dict[str, str]) -> TaskRecord:
    """基于旧任务对象和统一更新字典构造新任务对象。"""
    return TaskRecord(
        path=record.path,
        task_id=record.task_id,
        title=updates.get("title", record.title),
        status=updates.get("status", record.status),
        weight_level=updates.get("weight_level", record.weight_level),
        source_event_id=updates.get("source_event_id", record.source_event_id),
        sender_contact_id=updates.get("sender_contact_id", record.sender_contact_id),
        source_id=updates.get("source_id", record.source_id),
        approval_id=updates.get("approval_id", record.approval_id),
        run_mode=updates.get("run_mode", record.run_mode),
        scheduled_for=updates.get("scheduled_for", record.scheduled_for),
        execution_profile=updates.get("execution_profile", record.execution_profile),
        requested_capabilities=updates.get("requested_capabilities", record.requested_capabilities),
        resolved_skills=updates.get("resolved_skills", record.resolved_skills),
        resolved_tools=updates.get("resolved_tools", record.resolved_tools),
        resume_point=updates.get("resume_point", record.resume_point),
        resume_payload=updates.get("resume_payload", record.resume_payload),
        next_retry_at=updates.get("next_retry_at", record.next_retry_at),
        created_at=record.created_at,
        updated_at=updates["updated_at"],
        summary=updates.get("summary", record.summary),
        attempt_count=updates.get("attempt_count", record.attempt_count),
        retry_status=updates.get("retry_status", record.retry_status),
        approval_status=updates.get("approval_status", record.approval_status),
        last_result_summary=updates.get("last_result_summary", record.last_result_summary),
        identity_and_responsibility=updates.get(
            "identity_and_responsibility",
            record.identity_and_responsibility,
        ),
        decision_trace=updates.get("decision_trace", record.decision_trace),
        next_action=updates.get("next_action", record.next_action),
    )


def _build_frontmatter(record: TaskRecord) -> dict[str, str]:
    """构造任务记录 frontmatter，保留机器可稳定读取的状态字段。"""
    return {
        "schema": "dutyflow.task_state.v1",
        "id": record.task_id,
        "title": record.title,
        "status": record.status,
        "weight_level": record.weight_level,
        "source_event_id": record.source_event_id,
        "sender_contact_id": record.sender_contact_id,
        "source_id": record.source_id,
        "approval_id": record.approval_id,
        "run_mode": record.run_mode,
        "scheduled_for": record.scheduled_for,
        "execution_profile": record.execution_profile,
        "requested_capabilities": record.requested_capabilities,
        "resolved_skills": record.resolved_skills,
        "resolved_tools": record.resolved_tools,
        "resume_point": record.resume_point,
        "resume_payload": record.resume_payload,
        "next_retry_at": record.next_retry_at,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


def _build_body(record: TaskRecord) -> str:
    """渲染任务记录正文，兼顾人工可读和后续 section 抽取。"""
    state_lines = [
        f"- status: {record.status}",
        f"- weight_level: {record.weight_level}",
        f"- attempt_count: {record.attempt_count}",
        f"- retry_status: {record.retry_status}",
        f"- approval_status: {record.approval_status}",
        f"- scheduled_for: {record.scheduled_for}",
        f"- last_result_summary: {record.last_result_summary}",
    ]
    parts = [
        f"# Task {record.task_id}",
        "",
        "## Summary",
        "",
        record.summary,
        "",
        "## Current State",
        "",
        *state_lines,
        "",
        "## Identity And Responsibility",
        "",
        record.identity_and_responsibility,
        "",
        "## Decision Trace",
        "",
        record.decision_trace,
        "",
        "## Next Action",
        "",
        record.next_action,
        "",
    ]
    return "\n".join(parts)


def _build_task_path(tasks_dir: Path, task_id: str) -> Path:
    """构造单条任务记录的标准文件路径。"""
    return tasks_dir / f"{task_id}.md"


def _generate_task_id() -> str:
    """生成新的稳定任务 ID。"""
    return "task_" + uuid4().hex[:12]


def _now_iso() -> str:
    """生成当前时区下的 ISO-8601 时间字符串。"""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _parse_key_value_section(section_text: str) -> dict[str, str]:
    """解析 `- key: value` 风格的 section 内容。"""
    parsed: dict[str, str] = {}
    for raw_line in section_text.splitlines():
        line = raw_line.strip()
        if not line.startswith("- ") or ":" not in line:
            continue
        key, value = line[2:].split(":", 1)
        parsed[key.strip()] = value.strip()
    return parsed


def _self_test() -> None:
    """验证任务存储最小创建与读取能力。"""
    store = TaskStore(Path.cwd())
    record = store.create_task(title="self test task", task_id="task_selftest")
    loaded = store.read_task("task_selftest")
    assert loaded is not None
    assert loaded.task_id == record.task_id
    assert loaded.title == "self test task"


if __name__ == "__main__":
    _self_test()
    print("dutyflow task state self-test passed")
