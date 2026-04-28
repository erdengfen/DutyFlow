# 本文件负责 Step 7 第一版审批 Markdown 的创建、读取、列举和完成态写入。

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from dutyflow.storage.file_store import FileStore
from dutyflow.storage.markdown_store import MarkdownDocument, MarkdownStore


@dataclass(frozen=True)
class ApprovalRecord:
    """表示一条已落盘并可被后续审批链消费的审批记录。"""

    path: Path
    approval_id: str
    task_id: str
    status: str
    requested_at: str
    resolved_at: str
    requested_action: str
    risk_level: str
    resume_token: str
    request: str
    reason: str
    risk: str
    original_action: str
    original_tool_name: str
    original_tool_input_preview: str
    context_id: str
    trace_id: str
    decision_result: str
    decided_by: str
    decided_at: str
    comment: str


class ApprovalStore:
    """封装 `data/approvals/` 下待审批与已完成审批的最小存储。"""

    def __init__(
        self,
        project_root: Path,
        *,
        markdown_store: MarkdownStore | None = None,
    ) -> None:
        """绑定工作区并准备待审批和已完成目录。"""
        self.project_root = Path(project_root).resolve()
        self.markdown_store = markdown_store or MarkdownStore(FileStore(self.project_root))
        self.pending_dir = self.project_root / "data" / "approvals" / "pending"
        self.completed_dir = self.project_root / "data" / "approvals" / "completed"
        self.markdown_store.file_store.ensure_dir(self.pending_dir)
        self.markdown_store.file_store.ensure_dir(self.completed_dir)

    def create_approval(
        self,
        *,
        task_id: str,
        requested_action: str,
        risk_level: str,
        request: str,
        reason: str,
        risk: str,
        approval_id: str = "",
        resume_token: str = "",
        original_action: str = "",
        original_tool_name: str = "",
        original_tool_input_preview: str = "",
        context_id: str = "",
        trace_id: str = "",
    ) -> ApprovalRecord:
        """创建一条待审批记录并写入 pending 目录。"""
        now = _now_iso()
        resolved_approval_id = approval_id or _generate_approval_id()
        record = ApprovalRecord(
            path=_build_pending_path(self.pending_dir, resolved_approval_id),
            approval_id=resolved_approval_id,
            task_id=task_id.strip(),
            status="waiting",
            requested_at=now,
            resolved_at="",
            requested_action=requested_action.strip(),
            risk_level=risk_level.strip(),
            resume_token=resume_token.strip(),
            request=request.strip(),
            reason=reason.strip(),
            risk=risk.strip(),
            original_action=original_action.strip(),
            original_tool_name=original_tool_name.strip(),
            original_tool_input_preview=original_tool_input_preview.strip(),
            context_id=context_id.strip(),
            trace_id=trace_id.strip(),
            decision_result="",
            decided_by="",
            decided_at="",
            comment="",
        )
        self._write_record(record)
        return record

    def read_approval(self, approval_id: str) -> ApprovalRecord | None:
        """按审批 ID 读取记录，先查 pending，再查 completed。"""
        pending_path = _build_pending_path(self.pending_dir, approval_id)
        if self.markdown_store.exists(pending_path):
            return self._read_record(pending_path)
        completed_path = _build_completed_path(self.completed_dir, approval_id)
        if self.markdown_store.exists(completed_path):
            return self._read_record(completed_path)
        return None

    def list_pending_approvals(self) -> tuple[ApprovalRecord, ...]:
        """列出全部待审批记录。"""
        return self._list_records(self.pending_dir)

    def list_completed_approvals(self) -> tuple[ApprovalRecord, ...]:
        """列出全部已完成审批记录。"""
        return self._list_records(self.completed_dir)

    def resolve_approval(
        self,
        approval_id: str,
        *,
        result: str,
        decided_by: str,
        comment: str = "",
        decided_at: str = "",
    ) -> ApprovalRecord:
        """将待审批记录更新为完成态，并写入 completed 目录。"""
        record = self.read_approval(approval_id)
        if record is None:
            raise FileNotFoundError(f"approval not found: {approval_id}")
        if record.path.parent == self.completed_dir:
            raise ValueError(f"approval already resolved: {approval_id}")
        resolved = ApprovalRecord(
            path=_build_completed_path(self.completed_dir, approval_id),
            approval_id=record.approval_id,
            task_id=record.task_id,
            status=result.strip(),
            requested_at=record.requested_at,
            resolved_at=_now_iso(),
            requested_action=record.requested_action,
            risk_level=record.risk_level,
            resume_token=record.resume_token,
            request=record.request,
            reason=record.reason,
            risk=record.risk,
            original_action=record.original_action,
            original_tool_name=record.original_tool_name,
            original_tool_input_preview=record.original_tool_input_preview,
            context_id=record.context_id,
            trace_id=record.trace_id,
            decision_result=result.strip(),
            decided_by=decided_by.strip(),
            decided_at=decided_at.strip() or _now_iso(),
            comment=comment.strip(),
        )
        self._write_record(resolved)
        self._delete_if_exists(record.path)
        return resolved

    def _write_record(self, record: ApprovalRecord) -> None:
        """把审批对象渲染为 Markdown 并写入本地。"""
        document = MarkdownDocument(frontmatter=_build_frontmatter(record), body=_build_body(record))
        self.markdown_store.write_document(record.path, document)

    def _read_record(self, path: Path) -> ApprovalRecord:
        """从已落盘 Markdown 重建审批对象。"""
        document = self.markdown_store.read_document(path)
        resume_context = _parse_key_value_section(self.markdown_store.extract_section(path, "Resume Context"))
        user_decision = _parse_key_value_section(self.markdown_store.extract_section(path, "User Decision"))
        return ApprovalRecord(
            path=path,
            approval_id=document.frontmatter.get("id", ""),
            task_id=document.frontmatter.get("task_id", ""),
            status=document.frontmatter.get("status", ""),
            requested_at=document.frontmatter.get("requested_at", ""),
            resolved_at=document.frontmatter.get("resolved_at", ""),
            requested_action=document.frontmatter.get("requested_action", ""),
            risk_level=document.frontmatter.get("risk_level", ""),
            resume_token=document.frontmatter.get("resume_token", ""),
            request=self.markdown_store.extract_section(path, "Request"),
            reason=self.markdown_store.extract_section(path, "Reason"),
            risk=self.markdown_store.extract_section(path, "Risk"),
            original_action=resume_context.get("original_action", ""),
            original_tool_name=resume_context.get("original_tool_name", ""),
            original_tool_input_preview=resume_context.get("original_tool_input_preview", ""),
            context_id=resume_context.get("context_id", ""),
            trace_id=resume_context.get("trace_id", ""),
            decision_result=user_decision.get("result", ""),
            decided_by=user_decision.get("decided_by", ""),
            decided_at=user_decision.get("decided_at", ""),
            comment=user_decision.get("comment", ""),
        )

    def _list_records(self, directory: Path) -> tuple[ApprovalRecord, ...]:
        """按目录枚举审批记录，并保持稳定顺序。"""
        records = [self._read_record(path) for path in sorted(directory.glob("approval_*.md"))]
        records.sort(key=lambda item: (item.requested_at, item.approval_id))
        return tuple(records)

    def _delete_if_exists(self, path: Path) -> None:
        """删除旧审批文件，避免 pending 和 completed 双写。"""
        resolved = self.markdown_store.file_store.resolve(path)
        if resolved.exists():
            resolved.unlink()


def _build_frontmatter(record: ApprovalRecord) -> dict[str, str]:
    """构造审批记录 frontmatter。"""
    return {
        "schema": "dutyflow.approval_record.v1",
        "id": record.approval_id,
        "task_id": record.task_id,
        "status": record.status,
        "requested_at": record.requested_at,
        "resolved_at": record.resolved_at,
        "requested_action": record.requested_action,
        "risk_level": record.risk_level,
        "resume_token": record.resume_token,
    }


def _build_body(record: ApprovalRecord) -> str:
    """渲染审批记录正文，兼顾人工可读和后续 section 抽取。"""
    resume_context_lines = [
        f"- task_id: {record.task_id}",
        f"- original_action: {record.original_action}",
        f"- original_tool_name: {record.original_tool_name}",
        f"- original_tool_input_preview: {record.original_tool_input_preview}",
        f"- context_id: {record.context_id}",
        f"- trace_id: {record.trace_id}",
    ]
    user_decision_lines = [
        f"- result: {record.decision_result}",
        f"- decided_by: {record.decided_by}",
        f"- decided_at: {record.decided_at}",
        f"- comment: {record.comment}",
    ]
    parts = [
        f"# Approval {record.approval_id}",
        "",
        "## Request",
        "",
        record.request,
        "",
        "## Reason",
        "",
        record.reason,
        "",
        "## Risk",
        "",
        record.risk,
        "",
        "## Resume Context",
        "",
        *resume_context_lines,
        "",
        "## User Decision",
        "",
        *user_decision_lines,
        "",
    ]
    return "\n".join(parts)


def _build_pending_path(pending_dir: Path, approval_id: str) -> Path:
    """构造待审批记录文件路径。"""
    return pending_dir / f"{approval_id}.md"


def _build_completed_path(completed_dir: Path, approval_id: str) -> Path:
    """构造已完成审批记录文件路径。"""
    return completed_dir / f"{approval_id}.md"


def _generate_approval_id() -> str:
    """生成新的稳定审批 ID。"""
    return "approval_" + uuid4().hex[:12]


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
    """验证审批记录可创建、读取并转入 completed 目录。"""
    import tempfile

    with tempfile.TemporaryDirectory() as temp_dir:
        store = ApprovalStore(Path(temp_dir))
        created = store.create_approval(
            task_id="task_selftest",
            requested_action="document_write",
            risk_level="high",
            request="需要修改文档内容。",
            reason="该动作会改变外部状态。",
            risk="可能覆盖已有内容。",
            approval_id="approval_selftest",
        )
        loaded = store.read_approval("approval_selftest")
        assert loaded is not None
        assert loaded.status == "waiting"
        resolved = store.resolve_approval(
            "approval_selftest",
            result="approved",
            decided_by="user_selftest",
        )
    assert created.approval_id == "approval_selftest"
    assert resolved.status == "approved"


if __name__ == "__main__":
    _self_test()
    print("dutyflow approval flow self-test passed")
