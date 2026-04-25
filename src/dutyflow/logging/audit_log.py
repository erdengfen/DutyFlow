# 本文件负责按天写入结构化 Markdown 审计日志，不记录密钥和敏感配置。

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from dutyflow.storage.markdown_store import MarkdownDocument, MarkdownStore

AUDIT_CATEGORIES = frozenset(
    {"system", "agent_turn", "tool_execution", "permission", "recovery", "task_control", "feedback"}
)
AUDIT_OUTCOMES = frozenset(
    {"info", "success", "failed", "waiting", "denied", "approved", "rejected", "exhausted"}
)
SENSITIVE_FIELD_MARKERS = (
    "api_key",
    "token",
    "secret",
    "encrypt_key",
    "authorization",
    "app_secret",
)


def build_audit_preview(value: Any, max_chars: int = 200) -> str:
    """返回经脱敏和裁剪后的稳定预览字符串。"""
    sanitized = _redact_value(value)
    if isinstance(sanitized, str):
        text = sanitized
    else:
        text = json.dumps(sanitized, ensure_ascii=False, sort_keys=True)
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "...(truncated)"


@dataclass(frozen=True)
class AuditRecord:
    """表示一条结构化审计记录。"""

    record_id: str
    created_at: str
    category: str
    event_type: str
    outcome: str
    query_id: str = ""
    task_id: str = ""
    trace_id: str = ""
    recovery_id: str = ""
    tool_use_id: str = ""
    tool_name: str = ""
    permission_mode: str = ""
    turn_count: int = 0
    note: str = ""
    payload: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """校验结构化审计记录的稳定字段。"""
        if not self.record_id:
            raise ValueError("AuditRecord.record_id is required")
        if not self.created_at:
            raise ValueError("AuditRecord.created_at is required")
        if self.category not in AUDIT_CATEGORIES:
            raise ValueError(f"Unknown audit category: {self.category}")
        if not self.event_type:
            raise ValueError("AuditRecord.event_type is required")
        if self.outcome not in AUDIT_OUTCOMES:
            raise ValueError(f"Unknown audit outcome: {self.outcome}")
        if self.turn_count < 0:
            raise ValueError("AuditRecord.turn_count must be >= 0")


class AuditLogger:
    """按天维护 Agent 控制链路的结构化 Markdown 审计日志。"""

    def __init__(self, markdown_store: MarkdownStore, log_dir: Path, max_preview_chars: int = 200) -> None:
        """绑定 Markdown 存储、日志目录和统一预览裁剪上限。"""
        self.markdown_store = markdown_store
        self.log_dir = log_dir
        # 关键开关：审计日志中单条输入预览最多保留 200 个字符，超过后统一截断。
        self.max_preview_chars = max(40, max_preview_chars)

    def record(
        self,
        record_or_event_type: AuditRecord | str = "",
        note: str = "",
        task_id: str = "",
        trace_id: str = "",
        **legacy_kwargs,
    ) -> Path:
        """兼容旧接口或直接写入结构化审计记录。"""
        if isinstance(record_or_event_type, AuditRecord):
            record = self._sanitize_record(record_or_event_type)
        else:
            event_type = str(legacy_kwargs.pop("event_type", record_or_event_type))
            note_text = str(legacy_kwargs.pop("note", note))
            task_id_text = str(legacy_kwargs.pop("task_id", task_id))
            trace_id_text = str(legacy_kwargs.pop("trace_id", trace_id))
            record = self._sanitize_record(
                self._build_record(
                    category="system",
                    event_type=event_type,
                    outcome="info",
                    note=note_text,
                    task_id=task_id_text,
                    trace_id=trace_id_text,
                )
            )
        return self._write_record(record)

    def record_event(
        self,
        *,
        category: str,
        event_type: str,
        outcome: str,
        note: str,
        query_id: str = "",
        task_id: str = "",
        trace_id: str = "",
        recovery_id: str = "",
        tool_use_id: str = "",
        tool_name: str = "",
        permission_mode: str = "",
        turn_count: int = 0,
        payload: Mapping[str, Any] | None = None,
    ) -> Path:
        """按结构化字段直接记录一条审计事件。"""
        record = self._build_record(
            category=category,
            event_type=event_type,
            outcome=outcome,
            query_id=query_id,
            task_id=task_id,
            trace_id=trace_id,
            recovery_id=recovery_id,
            tool_use_id=tool_use_id,
            tool_name=tool_name,
            permission_mode=permission_mode,
            turn_count=turn_count,
            note=note,
            payload=payload or {},
        )
        return self.record(record)

    def preview(self, value: Any) -> str:
        """生成和审计日志一致的统一预览字符串。"""
        return build_audit_preview(value, max_chars=self.max_preview_chars)

    def _build_record(
        self,
        *,
        category: str,
        event_type: str,
        outcome: str,
        note: str,
        query_id: str = "",
        task_id: str = "",
        trace_id: str = "",
        recovery_id: str = "",
        tool_use_id: str = "",
        tool_name: str = "",
        permission_mode: str = "",
        turn_count: int = 0,
        payload: Mapping[str, Any] | None = None,
    ) -> AuditRecord:
        """构造带时间和稳定 ID 的审计记录。"""
        return AuditRecord(
            record_id="audit_" + uuid4().hex[:12],
            created_at=_now_text(),
            category=category,
            event_type=event_type,
            outcome=outcome,
            query_id=query_id,
            task_id=task_id,
            trace_id=trace_id,
            recovery_id=recovery_id,
            tool_use_id=tool_use_id,
            tool_name=tool_name,
            permission_mode=permission_mode,
            turn_count=turn_count,
            note=note,
            payload=payload or {},
        )

    def _sanitize_record(self, record: AuditRecord) -> AuditRecord:
        """对 note 和 payload 执行统一脱敏。"""
        return AuditRecord(
            record_id=record.record_id,
            created_at=record.created_at,
            category=record.category,
            event_type=record.event_type,
            outcome=record.outcome,
            query_id=record.query_id,
            task_id=record.task_id,
            trace_id=record.trace_id,
            recovery_id=record.recovery_id,
            tool_use_id=record.tool_use_id,
            tool_name=record.tool_name,
            permission_mode=record.permission_mode,
            turn_count=record.turn_count,
            note=self._redact_text(record.note),
            payload=self._sanitize_payload(record.payload),
        )

    def _sanitize_payload(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """递归脱敏 payload，并截断过长字符串。"""
        return _coerce_mapping(_redact_value(payload, max_chars=self.max_preview_chars))

    def _write_record(self, record: AuditRecord) -> Path:
        """把审计记录写入当天 Markdown 文件。"""
        path = self._today_path()
        document = self._load_or_create(path)
        document.frontmatter["updated_at"] = _now_text()
        document.body = document.body.rstrip() + "\n\n" + self._render_record(record)
        return self.markdown_store.write_document(path, document)

    def _today_path(self) -> Path:
        """返回当天日志文件路径。"""
        today = datetime.now().astimezone().date().isoformat()
        return self.log_dir / f"{today}.md"

    def _load_or_create(self, path: Path) -> MarkdownDocument:
        """读取当天日志，不存在则创建新文档。"""
        if self.markdown_store.exists(path):
            try:
                return self.markdown_store.read_document(path)
            except UnicodeDecodeError:
                return self._repair_corrupted_document(path)
        return self._new_document()

    def _repair_corrupted_document(self, path: Path) -> MarkdownDocument:
        """把包含非法 UTF-8 字节的日志文件修复为可继续追加的文档。"""
        resolved = self.markdown_store.file_store.resolve(path)
        text = resolved.read_bytes().decode("utf-8", errors="replace")
        parser = getattr(self.markdown_store, "_parse", None)
        if callable(parser):
            document = parser(text)
        else:
            document = MarkdownDocument(frontmatter={}, body=text)
        frontmatter = dict(document.frontmatter) if isinstance(document.frontmatter, Mapping) else {}
        frontmatter.setdefault("schema", "dutyflow.audit_log.v1")
        frontmatter.setdefault("id", f"audit_log_{datetime.now().astimezone().date().isoformat()}")
        frontmatter["updated_at"] = _now_text()
        body = document.body.rstrip()
        repair_note = (
            "## "
            + _now_text()
            + " audit_log_repaired\n\n"
            + "- note: previous invalid utf-8 bytes were replaced so the daily audit log can continue accepting new records.\n\n"
            + "```json\n"
            + '{\n  "repair_action": "utf8_replacement_decode"\n}\n'
            + "```"
        )
        if "audit_log_repaired" not in body:
            body = body + "\n\n" + repair_note if body else "# Audit Log\n\n" + repair_note
        return MarkdownDocument(frontmatter=frontmatter, body=body)

    def _new_document(self) -> MarkdownDocument:
        """创建新的当天日志文档。"""
        return MarkdownDocument(
            frontmatter={
                "schema": "dutyflow.audit_log.v1",
                "id": f"audit_log_{datetime.now().astimezone().date().isoformat()}",
                "updated_at": _now_text(),
            },
            body="# Audit Log\n",
        )

    def _render_record(self, record: AuditRecord) -> str:
        """渲染单条结构化审计记录。"""
        return (
            f"## {record.created_at} {record.event_type}\n\n"
            f"- record_id: {record.record_id}\n"
            f"- category: {record.category}\n"
            f"- outcome: {record.outcome}\n"
            f"- query_id: {record.query_id}\n"
            f"- task_id: {record.task_id}\n"
            f"- trace_id: {record.trace_id}\n"
            f"- recovery_id: {record.recovery_id}\n"
            f"- tool_use_id: {record.tool_use_id}\n"
            f"- tool_name: {record.tool_name}\n"
            f"- permission_mode: {record.permission_mode}\n"
            f"- turn_count: {record.turn_count}\n"
            f"- note: {record.note}\n\n"
            "```json\n"
            f"{json.dumps(record.payload, ensure_ascii=False, indent=2, sort_keys=True)}\n"
            "```"
        )

    def _redact_text(self, text: str) -> str:
        """遮蔽文本中的常见敏感键名。"""
        redacted = text
        for marker in SENSITIVE_FIELD_MARKERS:
            redacted = redacted.replace(marker, "[redacted-key]")
        return redacted


def _coerce_mapping(value: Any) -> dict[str, Any]:
    """把脱敏后的 payload 强制收敛为字典。"""
    if isinstance(value, Mapping):
        return dict(value)
    return {"value": value}


def _now_text() -> str:
    """返回当前本地时区 ISO 时间。"""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _redact_value(value: Any, max_chars: int = 200) -> Any:
    """递归脱敏并裁剪任意 JSON 风格值。"""
    if isinstance(value, Mapping):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if _looks_sensitive(key_text):
                sanitized[key_text] = "[redacted]"
            else:
                sanitized[key_text] = _redact_value(item, max_chars=max_chars)
        return sanitized
    if isinstance(value, tuple):
        return [_redact_value(item, max_chars=max_chars) for item in value]
    if isinstance(value, list):
        return [_redact_value(item, max_chars=max_chars) for item in value]
    if isinstance(value, str):
        redacted = value
        for marker in SENSITIVE_FIELD_MARKERS:
            redacted = redacted.replace(marker, "[redacted-key]")
        if len(redacted) <= max_chars:
            return redacted
        return redacted[:max_chars] + "...(truncated)"
    return value


def _looks_sensitive(key: str) -> bool:
    """判断字段名是否属于需要整体遮蔽的敏感字段。"""
    normalized = key.lower()
    return any(marker in normalized for marker in SENSITIVE_FIELD_MARKERS)


def _self_test() -> None:
    """验证敏感词遮蔽和结构化预览逻辑。"""
    store = MarkdownStore.__new__(MarkdownStore)
    logger = AuditLogger(store, Path("data/logs"))
    assert "api_key" not in logger._redact_text("api_key=abc")
    preview = logger.preview({"token": "secret-value", "text": "hello"})
    assert "secret-value" not in preview
    assert "hello" in preview


if __name__ == "__main__":
    _self_test()
    print("dutyflow audit log self-test passed")
