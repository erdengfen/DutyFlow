# 本文件负责 Step 7 第一版任务中断记录的创建、读取、查找和枚举。

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from dutyflow.storage.file_store import FileStore
from dutyflow.storage.markdown_store import MarkdownDocument, MarkdownStore


@dataclass(frozen=True)
class TaskInterruptRecord:
    """表示一条审批恢复阶段使用的任务中断记录。"""

    path: Path
    interrupt_id: str
    approval_id: str
    task_id: str
    original_tool_name: str
    original_tool_input_preview: str
    original_action_kind: str
    context_id: str
    trace_id: str
    resume_token: str
    created_at: str
    expires_at: str
    summary: str


class TaskInterruptStore:
    """封装 `data/approvals/interrupts/interrupt_<id>.md` 的最小中断记录存储。"""

    def __init__(
        self,
        project_root: Path,
        *,
        markdown_store: MarkdownStore | None = None,
    ) -> None:
        """绑定工作区并准备中断记录目录。"""
        self.project_root = Path(project_root).resolve()
        self.markdown_store = markdown_store or MarkdownStore(FileStore(self.project_root))
        self.interrupts_dir = self.project_root / "data" / "approvals" / "interrupts"
        self.markdown_store.file_store.ensure_dir(self.interrupts_dir)

    def create_interrupt(
        self,
        *,
        approval_id: str,
        task_id: str,
        original_tool_name: str,
        original_tool_input_preview: str,
        original_action_kind: str,
        context_id: str,
        trace_id: str,
        resume_token: str,
        expires_at: str,
        interrupt_id: str = "",
        summary: str = "",
    ) -> TaskInterruptRecord:
        """创建一条新的任务中断记录并立即落盘。"""
        resolved_interrupt_id = interrupt_id or _generate_interrupt_id()
        record = TaskInterruptRecord(
            path=_build_interrupt_path(self.interrupts_dir, resolved_interrupt_id),
            interrupt_id=resolved_interrupt_id,
            approval_id=approval_id.strip(),
            task_id=task_id.strip(),
            original_tool_name=original_tool_name.strip(),
            original_tool_input_preview=original_tool_input_preview.strip(),
            original_action_kind=original_action_kind.strip(),
            context_id=context_id.strip(),
            trace_id=trace_id.strip(),
            resume_token=resume_token.strip(),
            created_at=_now_iso(),
            expires_at=expires_at.strip(),
            summary=summary.strip(),
        )
        self._write_record(record)
        return record

    def read_interrupt(self, interrupt_id: str) -> TaskInterruptRecord | None:
        """按稳定中断 ID 读取一条任务中断记录。"""
        path = _build_interrupt_path(self.interrupts_dir, interrupt_id)
        if not self.markdown_store.exists(path):
            return None
        return self._read_record(path)

    def find_by_approval_id(self, approval_id: str) -> TaskInterruptRecord | None:
        """按审批 ID 查找唯一任务中断记录。"""
        return self._find_one("approval_id", approval_id.strip())

    def find_by_resume_token(self, resume_token: str) -> TaskInterruptRecord | None:
        """按 resume_token 查找唯一任务中断记录。"""
        return self._find_one("resume_token", resume_token.strip())

    def list_interrupts(self) -> tuple[TaskInterruptRecord, ...]:
        """枚举当前工作区内全部任务中断记录。"""
        records = [self._read_record(path) for path in sorted(self.interrupts_dir.glob("interrupt_*.md"))]
        records.sort(key=lambda item: (item.created_at, item.interrupt_id))
        return tuple(records)

    def _find_one(self, field_name: str, expected: str) -> TaskInterruptRecord | None:
        """按单个 frontmatter 字段做精确匹配。"""
        if not expected:
            return None
        for record in self.list_interrupts():
            value = getattr(record, field_name, "")
            if value == expected:
                return record
        return None

    def _write_record(self, record: TaskInterruptRecord) -> None:
        """把中断记录对象渲染为 Markdown 并写入本地。"""
        document = MarkdownDocument(frontmatter=_build_frontmatter(record), body=_build_body(record))
        self.markdown_store.write_document(record.path, document)

    def _read_record(self, path: Path) -> TaskInterruptRecord:
        """从已落盘 Markdown 重建任务中断对象。"""
        document = self.markdown_store.read_document(path)
        resume_context = _parse_key_value_section(self.markdown_store.extract_section(path, "Resume Context"))
        return TaskInterruptRecord(
            path=path,
            interrupt_id=document.frontmatter.get("id", ""),
            approval_id=document.frontmatter.get("approval_id", ""),
            task_id=document.frontmatter.get("task_id", ""),
            original_tool_name=document.frontmatter.get("original_tool_name", ""),
            original_tool_input_preview=resume_context.get("original_tool_input_preview", ""),
            original_action_kind=document.frontmatter.get("original_action_kind", ""),
            context_id=document.frontmatter.get("context_id", ""),
            trace_id=document.frontmatter.get("trace_id", ""),
            resume_token=document.frontmatter.get("resume_token", ""),
            created_at=document.frontmatter.get("created_at", ""),
            expires_at=document.frontmatter.get("expires_at", ""),
            summary=self.markdown_store.extract_section(path, "Summary"),
        )


def _build_frontmatter(record: TaskInterruptRecord) -> dict[str, str]:
    """构造任务中断记录 frontmatter。"""
    return {
        "schema": "dutyflow.task_interrupt.v1",
        "id": record.interrupt_id,
        "approval_id": record.approval_id,
        "task_id": record.task_id,
        "original_tool_name": record.original_tool_name,
        "original_action_kind": record.original_action_kind,
        "context_id": record.context_id,
        "trace_id": record.trace_id,
        "resume_token": record.resume_token,
        "created_at": record.created_at,
        "expires_at": record.expires_at,
    }


def _build_body(record: TaskInterruptRecord) -> str:
    """渲染任务中断记录正文，兼顾人工可读和后续恢复读取。"""
    resume_context_lines = [
        f"- approval_id: {record.approval_id}",
        f"- task_id: {record.task_id}",
        f"- original_tool_name: {record.original_tool_name}",
        f"- original_tool_input_preview: {record.original_tool_input_preview}",
        f"- original_action_kind: {record.original_action_kind}",
        f"- context_id: {record.context_id}",
        f"- trace_id: {record.trace_id}",
        f"- resume_token: {record.resume_token}",
        f"- expires_at: {record.expires_at}",
    ]
    parts = [
        f"# Task Interrupt {record.interrupt_id}",
        "",
        "## Summary",
        "",
        record.summary,
        "",
        "## Resume Context",
        "",
        *resume_context_lines,
        "",
    ]
    return "\n".join(parts)


def _build_interrupt_path(interrupts_dir: Path, interrupt_id: str) -> Path:
    """构造单条任务中断记录的标准文件路径。"""
    return interrupts_dir / f"{interrupt_id}.md"


def _generate_interrupt_id() -> str:
    """生成新的稳定任务中断记录 ID。"""
    return "interrupt_" + uuid4().hex[:12]


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


def _now_iso() -> str:
    """返回当前本地时区 ISO-8601 时间字符串。"""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _self_test() -> None:
    """验证任务中断记录可创建并按 resume_token 找回。"""
    import tempfile

    with tempfile.TemporaryDirectory() as temp_dir:
        store = TaskInterruptStore(Path(temp_dir))
        created = store.create_interrupt(
            approval_id="approval_selftest",
            task_id="task_selftest",
            original_tool_name="write_doc",
            original_tool_input_preview="doc=weekly",
            original_action_kind="document_write",
            context_id="ctx_selftest",
            trace_id="trace_selftest",
            resume_token="resume_selftest",
            expires_at="2026-04-30T12:00:00+08:00",
            interrupt_id="interrupt_selftest",
        )
        loaded = store.find_by_resume_token("resume_selftest")
    assert created.interrupt_id == "interrupt_selftest"
    assert loaded is not None
    assert loaded.task_id == "task_selftest"


if __name__ == "__main__":
    _self_test()
    print("dutyflow task interrupt self-test passed")
