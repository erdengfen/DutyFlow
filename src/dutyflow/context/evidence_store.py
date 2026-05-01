# 本文件负责把运行时长工具结果和大对象内容外置为本地 Evidence Markdown。

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from dutyflow.agent.tools.types import ToolResultEnvelope
from dutyflow.storage.file_store import FileStore
from dutyflow.storage.markdown_store import MarkdownDocument, MarkdownStore


# 关键开关：Evidence 摘要预览只保留 160 字，避免 frontmatter 变成大字段。
SUMMARY_PREVIEW_MAX_CHARS = 160
# 关键开关：缺省摘要只截取 500 字，完整内容仍保存在 Content 区域。
SUMMARY_MAX_CHARS = 500
EVIDENCE_SCHEMA = "dutyflow.context_evidence.v1"
EVIDENCE_SOURCE_TYPES = frozenset({"tool_result", "file_result", "observation", "manual"})
EVIDENCE_CONTENT_FORMATS = frozenset({"text", "json", "markdown"})
CONTENT_START_MARKER = "<!-- dutyflow:evidence-content:start -->"
CONTENT_END_MARKER = "<!-- dutyflow:evidence-content:end -->"


@dataclass(frozen=True)
class EvidenceRecord:
    """表示一条运行时上下文证据文件记录。"""

    path: Path
    relative_path: str
    evidence_id: str
    source_type: str
    source_id: str
    tool_use_id: str
    tool_name: str
    task_id: str
    event_id: str
    source_path: str
    content_format: str
    content_size: str
    content_sha256: str
    created_at: str
    summary: str
    content: str

    def to_ref(self) -> str:
        """返回模型上下文可引用的证据句柄。"""
        return f"evidence:{self.relative_path}"


class EvidenceStore:
    """封装 `data/contexts/evidence/evid_<id>.md` 的显式证据写入和读取。"""

    def __init__(
        self,
        project_root: Path,
        *,
        markdown_store: MarkdownStore | None = None,
    ) -> None:
        """绑定工作区并准备 Evidence 目录。"""
        self.project_root = Path(project_root).resolve()
        self.markdown_store = markdown_store or MarkdownStore(FileStore(self.project_root))
        self.evidence_dir = self.project_root / "data" / "contexts" / "evidence"
        self.markdown_store.file_store.ensure_dir(self.evidence_dir)

    def save_tool_result(
        self,
        result: ToolResultEnvelope,
        *,
        summary: str = "",
        evidence_id: str = "",
        task_id: str = "",
        event_id: str = "",
        source_path: str = "",
    ) -> EvidenceRecord:
        """把工具结果内容显式外置为 Evidence 记录。"""
        return self.save_content(
            source_type="tool_result",
            source_id=result.tool_use_id,
            content=result.content,
            summary=summary,
            evidence_id=evidence_id,
            tool_use_id=result.tool_use_id,
            tool_name=result.tool_name,
            task_id=task_id,
            event_id=event_id,
            source_path=source_path,
            content_format=_guess_content_format(result.content),
        )

    def save_content(
        self,
        *,
        source_type: str,
        source_id: str,
        content: str,
        summary: str = "",
        evidence_id: str = "",
        tool_use_id: str = "",
        tool_name: str = "",
        task_id: str = "",
        event_id: str = "",
        source_path: str = "",
        content_format: str = "text",
    ) -> EvidenceRecord:
        """保存调用方显式传入的大内容，不扫描其它业务目录。"""
        record = _build_record(
            self.project_root,
            self.evidence_dir,
            source_type=source_type,
            source_id=source_id,
            content=content,
            summary=summary,
            evidence_id=evidence_id,
            tool_use_id=tool_use_id,
            tool_name=tool_name,
            task_id=task_id,
            event_id=event_id,
            source_path=source_path,
            content_format=content_format,
        )
        self._write_record(record)
        return record

    def read_evidence(self, evidence_id: str) -> EvidenceRecord | None:
        """按 Evidence ID 读取证据记录。"""
        path = _build_evidence_path(self.evidence_dir, evidence_id)
        if not self.markdown_store.exists(path):
            return None
        return self._read_record(path)

    def list_evidence(self) -> tuple[EvidenceRecord, ...]:
        """只枚举 Evidence 目录内由本 store 创建的证据文件。"""
        records = [self._read_record(path) for path in sorted(self.evidence_dir.glob("evid_*.md"))]
        records.sort(key=lambda item: (item.created_at, item.evidence_id))
        return tuple(records)

    def _write_record(self, record: EvidenceRecord) -> None:
        """把证据记录渲染为 Markdown 并写入本地。"""
        document = MarkdownDocument(frontmatter=_build_frontmatter(record), body=_build_body(record))
        self.markdown_store.write_document(record.path, document)

    def _read_record(self, path: Path) -> EvidenceRecord:
        """从已落盘 Markdown 重建 EvidenceRecord。"""
        document = self.markdown_store.read_document(path)
        metadata = _parse_key_value_section(self.markdown_store.extract_section(path, "Source"))
        content = _extract_content(document.body)
        return EvidenceRecord(
            path=path,
            relative_path=_relative_path(self.project_root, path),
            evidence_id=document.frontmatter.get("id", ""),
            source_type=document.frontmatter.get("source_type", ""),
            source_id=document.frontmatter.get("source_id", ""),
            tool_use_id=document.frontmatter.get("tool_use_id", ""),
            tool_name=document.frontmatter.get("tool_name", ""),
            task_id=document.frontmatter.get("task_id", ""),
            event_id=document.frontmatter.get("event_id", ""),
            source_path=document.frontmatter.get("source_path", ""),
            content_format=metadata.get("content_format", ""),
            content_size=metadata.get("content_size", ""),
            content_sha256=document.frontmatter.get("content_sha256", ""),
            created_at=document.frontmatter.get("created_at", ""),
            summary=self.markdown_store.extract_section(path, "Summary"),
            content=content,
        )


def _build_record(
    project_root: Path,
    evidence_dir: Path,
    *,
    source_type: str,
    source_id: str,
    content: str,
    summary: str,
    evidence_id: str,
    tool_use_id: str,
    tool_name: str,
    task_id: str,
    event_id: str,
    source_path: str,
    content_format: str,
) -> EvidenceRecord:
    """构造并校验证据记录。"""
    normalized_id = evidence_id.strip() or _generate_evidence_id()
    _validate_evidence_id(normalized_id)
    _validate_source_type(source_type)
    _validate_content_format(content_format)
    _validate_content(content)
    path = _build_evidence_path(evidence_dir, normalized_id)
    normalized_content = str(content)
    return EvidenceRecord(
        path=path,
        relative_path=_relative_path(project_root, path),
        evidence_id=normalized_id,
        source_type=source_type.strip(),
        source_id=source_id.strip(),
        tool_use_id=tool_use_id.strip(),
        tool_name=tool_name.strip(),
        task_id=task_id.strip(),
        event_id=event_id.strip(),
        source_path=source_path.strip(),
        content_format=content_format.strip(),
        content_size=str(len(normalized_content)),
        content_sha256=_content_sha256(normalized_content),
        created_at=_now_iso(),
        summary=_summary_text(summary, normalized_content),
        content=normalized_content,
    )


def _build_frontmatter(record: EvidenceRecord) -> dict[str, str]:
    """构造 Evidence frontmatter，便于按来源锚点人工检查。"""
    return {
        "schema": EVIDENCE_SCHEMA,
        "id": record.evidence_id,
        "source_type": record.source_type,
        "source_id": record.source_id,
        "tool_use_id": record.tool_use_id,
        "tool_name": record.tool_name,
        "task_id": record.task_id,
        "event_id": record.event_id,
        "source_path": record.source_path,
        "content_format": record.content_format,
        "content_size": record.content_size,
        "content_sha256": record.content_sha256,
        "created_at": record.created_at,
        "summary_preview": _frontmatter_preview(record.summary),
    }


def _build_body(record: EvidenceRecord) -> str:
    """渲染 Evidence 正文，Content 使用 marker 避免 Markdown 标题干扰读取。"""
    return "\n".join(
        (
            f"# Evidence {record.evidence_id}",
            "",
            "## Summary",
            "",
            record.summary,
            "",
            "## Source",
            "",
            f"- source_type: {record.source_type}",
            f"- source_id: {record.source_id}",
            f"- tool_use_id: {record.tool_use_id}",
            f"- tool_name: {record.tool_name}",
            f"- task_id: {record.task_id}",
            f"- event_id: {record.event_id}",
            f"- source_path: {record.source_path}",
            f"- content_format: {record.content_format}",
            f"- content_size: {record.content_size}",
            f"- content_sha256: {record.content_sha256}",
            "",
            "## Content",
            "",
            CONTENT_START_MARKER,
            record.content,
            CONTENT_END_MARKER,
            "",
        )
    )


def _build_evidence_path(evidence_dir: Path, evidence_id: str) -> Path:
    """构造 Evidence 标准文件路径。"""
    _validate_evidence_id(evidence_id)
    return evidence_dir / f"{evidence_id}.md"


def _generate_evidence_id() -> str:
    """生成新的 Evidence ID。"""
    return "evid_" + uuid4().hex[:12]


def _validate_evidence_id(evidence_id: str) -> None:
    """校验 Evidence ID，避免构造异常文件名。"""
    if not evidence_id.startswith("evid_"):
        raise ValueError("Evidence id must start with evid_")
    suffix = evidence_id.removeprefix("evid_")
    if not suffix or not all(char.isalnum() or char == "_" for char in suffix):
        raise ValueError("Evidence id contains invalid characters")


def _validate_source_type(source_type: str) -> None:
    """校验 Evidence 来源类型。"""
    if source_type.strip() not in EVIDENCE_SOURCE_TYPES:
        raise ValueError(f"Unknown evidence source_type: {source_type}")


def _validate_content_format(content_format: str) -> None:
    """校验 Evidence 内容格式。"""
    if content_format.strip() not in EVIDENCE_CONTENT_FORMATS:
        raise ValueError(f"Unknown evidence content_format: {content_format}")


def _validate_content(content: str) -> None:
    """避免原文包含内部 marker 导致读取边界歧义。"""
    if CONTENT_START_MARKER in str(content) or CONTENT_END_MARKER in str(content):
        raise ValueError("Evidence content contains reserved marker")


def _summary_text(summary: str, content: str) -> str:
    """返回调用方摘要；未提供时用原文确定性截断。"""
    normalized = _trim_single_line(summary, SUMMARY_MAX_CHARS)
    if normalized:
        return normalized
    return _trim_single_line(content, SUMMARY_MAX_CHARS)


def _trim_single_line(text: str, max_chars: int) -> str:
    """把多行文本压缩成固定上限的单行预览。"""
    normalized = " ".join(str(text).split())
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max_chars - 3] + "..."


def _frontmatter_preview(summary: str) -> str:
    """生成符合 MarkdownStore 简单 frontmatter 约束的摘要预览。"""
    preview = _trim_single_line(summary, SUMMARY_PREVIEW_MAX_CHARS)
    if preview.startswith(("[", "{", "-")):
        return "preview: " + preview
    return preview


def _content_sha256(content: str) -> str:
    """计算原始内容 SHA-256。"""
    return sha256(content.encode("utf-8")).hexdigest()


def _extract_content(body: str) -> str:
    """从正文 marker 中提取完整 Evidence 内容。"""
    start = body.find(CONTENT_START_MARKER)
    end = body.find(CONTENT_END_MARKER)
    if start == -1 or end == -1 or end < start:
        raise ValueError("Evidence content marker is missing")
    content_start = start + len(CONTENT_START_MARKER)
    return body[content_start:end].strip("\n")


def _parse_key_value_section(section_text: str) -> dict[str, str]:
    """解析 `- key: value` 格式的正文 section。"""
    parsed: dict[str, str] = {}
    for raw_line in section_text.splitlines():
        line = raw_line.strip()
        if not line.startswith("- ") or ":" not in line:
            continue
        key, value = line[2:].split(":", 1)
        parsed[key.strip()] = value.strip()
    return parsed


def _guess_content_format(content: str) -> str:
    """根据内容首字符做轻量格式判断。"""
    stripped = str(content).lstrip()
    if stripped.startswith(("{", "[")):
        return "json"
    if stripped.startswith("#") or "\n## " in stripped:
        return "markdown"
    return "text"


def _relative_path(project_root: Path, path: Path) -> str:
    """把绝对路径转换成项目内相对路径。"""
    try:
        return str(path.resolve().relative_to(project_root))
    except ValueError:
        return str(path)


def _now_iso() -> str:
    """返回当前本地时区 ISO-8601 时间字符串。"""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _self_test() -> None:
    """验证 Evidence Store 可以保存并读回完整内容。"""
    import tempfile

    with tempfile.TemporaryDirectory() as temp_dir:
        store = EvidenceStore(Path(temp_dir))
        created = store.save_content(
            source_type="tool_result",
            source_id="tool_selftest",
            tool_use_id="tool_selftest",
            tool_name="sample_tool",
            content="# Heading\n\n## Inner\n\nbody",
            summary="self test evidence",
            evidence_id="evid_selftest",
            content_format="markdown",
        )
        loaded = store.read_evidence("evid_selftest")
    assert created.to_ref() == "evidence:data/contexts/evidence/evid_selftest.md"
    assert loaded is not None
    assert loaded.content == "# Heading\n\n## Inner\n\nbody"


if __name__ == "__main__":
    _self_test()
    print("dutyflow evidence store self-test passed")
