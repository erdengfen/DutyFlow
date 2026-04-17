# 本文件负责按天写入 Markdown 审计日志，不记录密钥和敏感配置。

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from dutyflow.storage.markdown_store import MarkdownDocument, MarkdownStore


@dataclass
class AuditRecord:
    """表示一条可追加到 Markdown 日志的审计记录。"""

    event_type: str
    note: str
    task_id: str = ""
    trace_id: str = ""


class AuditLogger:
    """按天维护本地 Markdown 审计日志。"""

    def __init__(self, markdown_store: MarkdownStore, log_dir: Path) -> None:
        """绑定 Markdown 存储和日志目录。"""
        self.markdown_store = markdown_store
        self.log_dir = log_dir

    def record(self, event_type: str, note: str, task_id: str = "", trace_id: str = "") -> Path:
        """追加一条审计日志记录。"""
        record = AuditRecord(
            event_type=event_type,
            note=self._redact(note),
            task_id=task_id,
            trace_id=trace_id,
        )
        path = self._today_path()
        document = self._load_or_create(path)
        document.frontmatter["updated_at"] = datetime.now().astimezone().isoformat(
            timespec="seconds"
        )
        document.body = document.body.rstrip() + "\n\n" + self._render_record(record)
        return self.markdown_store.write_document(path, document)

    def _today_path(self) -> Path:
        """返回当天日志文件路径。"""
        today = datetime.now().astimezone().date().isoformat()
        return self.log_dir / f"{today}.md"

    def _load_or_create(self, path: Path) -> MarkdownDocument:
        """读取当天日志，不存在则创建新文档。"""
        if self.markdown_store.exists(path):
            return self.markdown_store.read_document(path)
        return MarkdownDocument(
            frontmatter={
                "schema": "dutyflow.audit_log.v1",
                "id": f"audit_log_{datetime.now().astimezone().date().isoformat()}",
                "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            },
            body="# Audit Log\n",
        )

    def _render_record(self, record: AuditRecord) -> str:
        """渲染单条日志记录。"""
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        return (
            f"## {now} {record.event_type}\n\n"
            f"- task_id: {record.task_id}\n"
            f"- trace_id: {record.trace_id}\n"
            f"- note: {record.note}\n"
        )

    def _redact(self, text: str) -> str:
        """遮蔽常见敏感键名，避免日志泄露配置。"""
        redacted = text
        for marker in ("api_key", "secret", "token", "encrypt_key"):
            redacted = redacted.replace(marker, "[redacted-key]")
        return redacted


def _self_test() -> None:
    """验证敏感词遮蔽逻辑。"""
    store = MarkdownStore.__new__(MarkdownStore)
    logger = AuditLogger(store, Path("data/logs"))
    assert "api_key" not in logger._redact("api_key=abc")


if __name__ == "__main__":
    _self_test()
    print("dutyflow audit log self-test passed")
