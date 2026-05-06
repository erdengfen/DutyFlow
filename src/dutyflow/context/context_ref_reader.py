# 本文件负责按稳定引用读取本地已落盘上下文，供 Agent 和后台任务只读使用。

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dutyflow.approval.approval_flow import ApprovalStore
from dutyflow.context.evidence_store import EvidenceStore
from dutyflow.feishu.ambient_context import AmbientContextStore
from dutyflow.perception.store import PerceptionRecordService
from dutyflow.tasks.task_result import TaskResultStore
from dutyflow.tasks.task_state import TaskStore

# 关键开关：read_context_ref 返回给模型的正文预览最多 1200 字，完整内容仍留在原 Markdown 文件中。
MAX_CONTEXT_REF_PREVIEW_CHARS = 1200

_REF_TYPE_ALIASES = {
    "perception": "perception",
    "perceived_event": "perception",
    "ambient": "ambient_context",
    "ambient_context": "ambient_context",
    "evidence": "evidence",
    "task": "task",
    "approval": "approval",
}


@dataclass(frozen=True)
class ContextRefReadResult:
    """表示一次 context_ref 读取结果，不承载超长正文。"""

    ok: bool
    status: str
    ref_type: str
    ref_id: str
    detail_file: str = ""
    summary: str = ""
    text_preview: str = ""
    anchors: dict[str, str] | None = None
    payload: dict[str, Any] | None = None

    def to_payload(self) -> dict[str, Any]:
        """转换为工具层稳定 JSON 结构。"""
        return {
            "ok": self.ok,
            "status": self.status,
            "ref_type": self.ref_type,
            "ref_id": self.ref_id,
            "detail_file": self.detail_file,
            "summary": self.summary,
            "text_preview": self.text_preview,
            "anchors": dict(self.anchors or {}),
            "payload": dict(self.payload or {}),
        }


class ContextRefReader:
    """只读读取项目内关键上下文引用，不访问外部 API 和项目外文件。"""

    def __init__(self, project_root: Path) -> None:
        """绑定项目根目录和各类 Markdown store。"""
        self.project_root = Path(project_root).resolve()
        self.perception_store = PerceptionRecordService(self.project_root)
        self.ambient_store = AmbientContextStore(self.project_root)
        self.evidence_store = EvidenceStore(self.project_root)
        self.task_store = TaskStore(self.project_root)
        self.task_result_store = TaskResultStore(self.project_root)
        self.approval_store = ApprovalStore(self.project_root)

    def read(self, ref_type: str, ref_id: str) -> ContextRefReadResult:
        """按 ref_type 和 ref_id 读取对应上下文。"""
        normalized_type = _normalize_ref_type(ref_type)
        normalized_id = _normalize_ref_id(normalized_type, ref_id)
        if not normalized_type:
            return _missing("invalid_ref_type", ref_type, ref_id)
        if not normalized_id:
            return _missing("invalid_ref_id", normalized_type, ref_id)
        readers = {
            "perception": self._read_perception,
            "ambient_context": self._read_ambient_context,
            "evidence": self._read_evidence,
            "task": self._read_task,
            "approval": self._read_approval,
        }
        return readers[normalized_type](normalized_id)

    def _read_perception(self, ref_id: str) -> ContextRefReadResult:
        """读取感知事件记录。"""
        record = self.perception_store.read_by_record_id(ref_id)
        if record is None:
            return _missing("not_found", "perception", ref_id)
        return ContextRefReadResult(
            True,
            "ok",
            "perception",
            ref_id,
            _relative_path(self.project_root, record.path),
            _trim(record.content_preview or record.raw_text),
            _trim(record.raw_text or record.content_preview),
            _perception_anchors(record),
            {"loop_input": record.to_loop_input()},
        )

    def _read_ambient_context(self, ref_id: str) -> ContextRefReadResult:
        """读取主动感知记录。"""
        record = self.ambient_store.read_by_record_id(ref_id)
        if record is None:
            return _missing("not_found", "ambient_context", ref_id)
        detail_file = _ambient_detail_file(self.project_root, record)
        return ContextRefReadResult(
            True,
            "ok",
            "ambient_context",
            ref_id,
            detail_file,
            _trim(record.summary),
            _trim(record.text or record.text_preview),
            _ambient_anchors(record),
            _ambient_payload(record),
        )

    def _read_evidence(self, ref_id: str) -> ContextRefReadResult:
        """读取 Evidence 证据记录。"""
        record = self.evidence_store.read_evidence(ref_id)
        if record is None:
            return _missing("not_found", "evidence", ref_id)
        return ContextRefReadResult(
            True,
            "ok",
            "evidence",
            ref_id,
            record.relative_path,
            _trim(record.summary),
            _trim(record.content),
            _evidence_anchors(record),
            {"content_format": record.content_format, "content_size": record.content_size},
        )

    def _read_task(self, ref_id: str) -> ContextRefReadResult:
        """读取任务状态，并附带任务结果摘要。"""
        record = self.task_store.read_task(ref_id)
        if record is None:
            return _missing("not_found", "task", ref_id)
        result = self.task_result_store.read_result(ref_id)
        return ContextRefReadResult(
            True,
            "ok",
            "task",
            ref_id,
            _relative_path(self.project_root, record.path),
            _trim(record.summary or record.last_result_summary),
            _trim(_task_preview(record, result)),
            _task_anchors(record, result),
            _task_payload(record, result),
        )

    def _read_approval(self, ref_id: str) -> ContextRefReadResult:
        """读取审批记录。"""
        record = self.approval_store.read_approval(ref_id)
        if record is None:
            return _missing("not_found", "approval", ref_id)
        return ContextRefReadResult(
            True,
            "ok",
            "approval",
            ref_id,
            _relative_path(self.project_root, record.path),
            _trim(record.request),
            _trim("\n".join((record.request, record.reason, record.risk))),
            _approval_anchors(record),
            _approval_payload(record),
        )


def _perception_anchors(record) -> dict[str, str]:
    """生成感知记录锚点。"""
    return {
        "perception_id": record.record_id,
        "event_id": record.source_event_id,
        "message_id": record.message_id,
        "chat_id": record.chat_id,
        "sender_open_id": record.sender_open_id,
    }


def _ambient_anchors(record) -> dict[str, str]:
    """生成主动感知记录锚点。"""
    return {
        "ambient_record_id": record.record_id,
        "source_id": record.source_id,
        "sync_scope_id": record.sync_scope_id,
        "raw_message_ref": record.raw_message_ref,
        "sync_state_ref": record.sync_state_ref,
    }


def _evidence_anchors(record) -> dict[str, str]:
    """生成 Evidence 记录锚点。"""
    return {
        "evidence_id": record.evidence_id,
        "source_id": record.source_id,
        "tool_use_id": record.tool_use_id,
        "task_id": record.task_id,
        "event_id": record.event_id,
    }


def _task_anchors(record, result) -> dict[str, str]:
    """生成任务记录锚点。"""
    return {
        "task_id": record.task_id,
        "source_event_id": record.source_event_id,
        "source_id": record.source_id,
        "approval_id": record.approval_id,
        "task_result_id": getattr(result, "result_id", "") if result else "",
    }


def _approval_anchors(record) -> dict[str, str]:
    """生成审批记录锚点。"""
    return {
        "approval_id": record.approval_id,
        "task_id": record.task_id,
        "context_id": record.context_id,
        "trace_id": record.trace_id,
        "resume_token": record.resume_token,
    }


def _ambient_payload(record) -> dict[str, Any]:
    """构造主动感知记录的结构化摘要。"""
    return {
        "source_type": record.source_type,
        "collector_name": record.collector_name,
        "created_at": record.created_at,
        "fetched_at": record.fetched_at,
        "doc_links": [link.__dict__ for link in record.doc_links],
        "file_clues": [clue.__dict__ for clue in record.file_clues],
        "frontmatter_extra": dict(record.frontmatter_extra),
    }


def _task_payload(record, result) -> dict[str, Any]:
    """构造任务状态和结果摘要。"""
    return {
        "title": record.title,
        "status": record.status,
        "run_mode": record.run_mode,
        "scheduled_for": record.scheduled_for,
        "approval_status": record.approval_status,
        "resolved_tools": record.resolved_tools,
        "resume_point": record.resume_point,
        "resume_payload": record.resume_payload,
        "task_result": _task_result_payload(result),
    }


def _approval_payload(record) -> dict[str, str]:
    """构造审批记录结构化摘要。"""
    return {
        "status": record.status,
        "requested_action": record.requested_action,
        "risk_level": record.risk_level,
        "decision_result": record.decision_result,
        "decided_by": record.decided_by,
        "decided_at": record.decided_at,
        "original_tool_name": record.original_tool_name,
    }


def _task_result_payload(result) -> dict[str, str]:
    """构造任务结果摘要；不存在时返回空对象。"""
    if result is None:
        return {}
    return {
        "result_id": result.result_id,
        "status": result.status,
        "summary": result.summary,
        "user_visible_final_text": _trim(result.user_visible_final_text),
    }


def _task_preview(record, result) -> str:
    """生成任务读取预览。"""
    parts = [record.summary, record.last_result_summary, record.next_action]
    if result is not None:
        parts.extend([result.summary, result.user_visible_final_text])
    return "\n".join(item for item in parts if item)


def _ambient_detail_file(project_root: Path, record) -> str:
    """根据 ambient record 的稳定字段还原详情文件路径。"""
    path = AmbientContextStore(project_root).path_for(record)
    return _relative_path(project_root, path)


def _normalize_ref_type(ref_type: str) -> str:
    """标准化 ref_type，并支持少量别名。"""
    return _REF_TYPE_ALIASES.get(str(ref_type).strip().lower(), "")


def _normalize_ref_id(ref_type: str, ref_id: str) -> str:
    """从 evidence:path 这类引用中提取稳定 ID。"""
    text = str(ref_id).strip()
    if ref_type == "evidence" and text.startswith("evidence:"):
        return Path(text.removeprefix("evidence:")).stem
    if ref_type == "evidence" and text.endswith(".md"):
        return Path(text).stem
    return text


def _missing(status: str, ref_type: str, ref_id: str) -> ContextRefReadResult:
    """生成读取失败结果，不抛出异常穿透工具层。"""
    return ContextRefReadResult(False, status, ref_type, ref_id)


def _trim(text: str, limit: int = MAX_CONTEXT_REF_PREVIEW_CHARS) -> str:
    """把返回给模型的文本压到固定上限。"""
    normalized = str(text).strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3] + "..."


def _relative_path(project_root: Path, path: Path) -> str:
    """把项目内路径转换为相对路径。"""
    try:
        return str(path.resolve().relative_to(project_root))
    except ValueError:
        return str(path)


def _self_test() -> None:
    """验证非法 ref_type 会返回稳定错误结果。"""
    import tempfile

    with tempfile.TemporaryDirectory() as temp_dir:
        result = ContextRefReader(Path(temp_dir)).read("unknown", "x")
    assert result.ok is False
    assert result.status == "invalid_ref_type"


if __name__ == "__main__":
    _self_test()
    print("dutyflow context ref reader self-test passed")
