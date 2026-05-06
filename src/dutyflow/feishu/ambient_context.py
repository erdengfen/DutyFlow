# 本文件负责飞书用户面主动感知结果的统一 Markdown 落盘和索引维护。

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Mapping

from dutyflow.storage.file_store import FileStore
from dutyflow.storage.markdown_store import MarkdownDocument, MarkdownStore

AMBIENT_CONTEXT_SCHEMA = "dutyflow.ambient_context.v1"
AMBIENT_CONTEXT_INDEX_SCHEMA = "dutyflow.ambient_context_index.v1"
# 关键开关：ambient_context 文件名片段最多保留 120 字符，避免外部 ID 异常过长导致路径难读。
MAX_SAFE_FILE_PART_CHARS = 120
# 关键开关：索引中的文本预览最多保留 120 字符，避免 index.md 快速膨胀。
MAX_INDEX_PREVIEW_CHARS = 120
# 关键开关：单个 ambient context packet 第一版最多包含 50 条记录，避免主动分析输入过大。
MAX_CONTEXT_PACKET_RECORDS = 50
# 关键开关：只有这些 resource_type 的文档链接才支持通过 feishu_read_doc 补读正文；sheet、wiki、file 不在此列。
_DOCX_READABLE_RESOURCE_TYPES = frozenset({"docx", "docs"})


@dataclass(frozen=True)
class AmbientDocLink:
    """表示主动感知文本中提取出的飞书文档链接线索。"""

    url: str
    resource_type: str = ""
    token: str = ""


@dataclass(frozen=True)
class AmbientFileClue:
    """表示主动感知消息中的附件线索，不包含二进制正文。"""

    message_id: str
    msg_type: str
    file_key: str = ""
    file_name: str = ""


@dataclass(frozen=True)
class AmbientContextRecord:
    """表示一条用户面主动感知结果，供各 collector 统一落盘。"""

    record_id: str
    source_type: str
    collector_name: str
    source_id: str
    sync_scope_id: str
    created_at: str
    fetched_at: str
    text: str = ""
    text_preview: str = ""
    summary: str = ""
    raw_message_ref: str = ""
    sync_state_ref: str = ""
    doc_links: tuple[AmbientDocLink, ...] = ()
    file_clues: tuple[AmbientFileClue, ...] = ()
    frontmatter_extra: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """校验记录可定位的基础字段。"""
        if not self.record_id:
            raise ValueError("AmbientContextRecord.record_id is required")
        if not self.source_type:
            raise ValueError("AmbientContextRecord.source_type is required")
        if not self.collector_name:
            raise ValueError("AmbientContextRecord.collector_name is required")


@dataclass(frozen=True)
class AmbientContextWriteResult:
    """表示 ambient_context 单条记录和索引写入结果。"""

    record_id: str
    path: Path
    global_index_path: Path
    source_index_path: Path


@dataclass(frozen=True)
class AmbientContextScanQuery:
    """表示主动感知记录扫描条件，供后续分析入队层复用。"""

    source_type: str = ""
    collector_name: str = ""
    created_after: str = ""
    created_before: str = ""
    record_ids: tuple[str, ...] = ()
    limit: int = MAX_CONTEXT_PACKET_RECORDS


@dataclass(frozen=True)
class AmbientContextPacketRecord:
    """表示 context packet 中单条主动感知记录的模型可见摘要。"""

    record_id: str
    source_type: str
    collector_name: str
    source_id: str
    sync_scope_id: str
    created_at: str
    fetched_at: str
    detail_file: str
    text_preview: str
    summary: str
    doc_links: tuple[AmbientDocLink, ...] = ()
    file_clues: tuple[AmbientFileClue, ...] = ()

    @property
    def readable_doc_tokens(self) -> tuple[str, ...]:
        """返回可通过 feishu_read_doc 读取正文的 docx token 列表。"""
        return tuple(
            link.token
            for link in self.doc_links
            if link.token and link.resource_type in _DOCX_READABLE_RESOURCE_TYPES
        )

    def to_payload(self) -> dict[str, object]:
        """转换为稳定 JSON 结构。"""
        return {
            "record_id": self.record_id,
            "source_type": self.source_type,
            "collector_name": self.collector_name,
            "source_id": self.source_id,
            "sync_scope_id": self.sync_scope_id,
            "created_at": self.created_at,
            "fetched_at": self.fetched_at,
            "detail_file": self.detail_file,
            "text_preview": self.text_preview,
            "summary": self.summary,
            "doc_links": [dict(link.__dict__) for link in self.doc_links],
            "file_clues": [dict(clue.__dict__) for clue in self.file_clues],
            "readable_doc_tokens": list(self.readable_doc_tokens),
        }


@dataclass(frozen=True)
class AmbientContextPacket:
    """表示一批待送入正式 runtime 分析的主动感知上下文包。"""

    packet_id: str
    source_type: str
    collector_names: tuple[str, ...]
    record_ids: tuple[str, ...]
    scope_ids: tuple[str, ...]
    time_window_start: str
    time_window_end: str
    record_count: int
    records: tuple[AmbientContextPacketRecord, ...]

    @property
    def readable_doc_tokens(self) -> tuple[str, ...]:
        """返回整批次中可通过 feishu_read_doc 补读正文的 docx token 列表。"""
        seen: set[str] = set()
        result: list[str] = []
        for record in self.records:
            for token in record.readable_doc_tokens:
                if token not in seen:
                    seen.add(token)
                    result.append(token)
        return tuple(result)

    def to_payload(self) -> dict[str, object]:
        """转换为 runtime 可消费的稳定 JSON 结构。"""
        return {
            "packet_id": self.packet_id,
            "source_type": self.source_type,
            "collector_names": list(self.collector_names),
            "record_ids": list(self.record_ids),
            "scope_ids": list(self.scope_ids),
            "time_window": {
                "start": self.time_window_start,
                "end": self.time_window_end,
            },
            "record_count": self.record_count,
            "records": [record.to_payload() for record in self.records],
            "readable_doc_tokens": list(self.readable_doc_tokens),
        }


class AmbientContextStore:
    """统一写入飞书用户面主动感知 Markdown 记录。"""

    def __init__(self, project_root: Path) -> None:
        """绑定项目根目录和 Markdown 存储。"""
        self.project_root = Path(project_root).resolve()
        self.markdown_store = MarkdownStore(FileStore(self.project_root))

    def write(self, record: AmbientContextRecord) -> AmbientContextWriteResult:
        """写入单条记录，并更新总索引和 source_type 子索引。"""
        record = self._record_with_relative_refs(record)
        path = self.path_for(record)
        document = MarkdownDocument(
            frontmatter=_frontmatter_from_record(record),
            body=_body_from_record(record),
        )
        written_path = self.markdown_store.write_document(path, document)
        row = _index_row(record, self.project_root, written_path)
        global_index = self._update_index(Path("data/ambient_context/index.md"), "all", row)
        source_index = self._update_index(
            Path("data/ambient_context") / _safe_file_part(record.source_type) / "index.md",
            record.source_type,
            row,
        )
        return AmbientContextWriteResult(record.record_id, written_path, global_index, source_index)

    def read_by_record_id(self, record_id: str) -> AmbientContextRecord | None:
        """按 ambient_context 的 record_id 读取详情记录。"""
        target = record_id.strip()
        if not target:
            return None
        for path in self._iter_record_paths():
            if path.stem == target:
                return self._read_record(path)
        return None

    def scan_records(self, query: AmbientContextScanQuery | None = None) -> tuple[AmbientContextRecord, ...]:
        """按来源、collector、时间和 record_id 条件扫描主动感知记录。"""
        resolved = query or AmbientContextScanQuery()
        records = [self._read_record(path) for path in self._iter_record_paths()]
        filtered = [record for record in records if _record_matches_query(record, resolved)]
        filtered.sort(key=lambda item: (_datetime_order_key(item.created_at), item.record_id))
        return tuple(filtered[: _scan_limit(resolved.limit)])

    def build_context_packet(self, query: AmbientContextScanQuery | None = None) -> AmbientContextPacket:
        """把扫描结果转换为后续 runtime 可消费的稳定 context packet。"""
        records = self.scan_records(query)
        packet_records = tuple(_packet_record(self.project_root, self, record) for record in records)
        record_ids = tuple(record.record_id for record in records)
        return AmbientContextPacket(
            packet_id=_packet_id(record_ids),
            source_type=_packet_source_type(records),
            collector_names=_unique(record.collector_name for record in records),
            record_ids=record_ids,
            scope_ids=_unique(record.sync_scope_id for record in records),
            time_window_start=records[0].created_at if records else "",
            time_window_end=records[-1].created_at if records else "",
            record_count=len(records),
            records=packet_records,
        )

    def _record_with_relative_refs(self, record: AmbientContextRecord) -> AmbientContextRecord:
        """把项目内引用路径规整为相对路径，避免 Markdown 记录绑定本机绝对目录。"""
        return replace(
            record,
            raw_message_ref=_relative_reference(self.project_root, record.raw_message_ref),
            sync_state_ref=_relative_reference(self.project_root, record.sync_state_ref),
        )

    def path_for(self, record: AmbientContextRecord) -> Path:
        """按 source_type、日期和 record_id 返回工作区内绝对路径。"""
        date_part = _date_part(record.created_at or record.fetched_at)
        relative = (
            Path("data/ambient_context")
            / _safe_file_part(record.source_type)
            / date_part
            / f"{_safe_file_part(record.record_id)}.md"
        )
        return self.markdown_store.file_store.resolve(relative)

    def _iter_record_paths(self) -> tuple[Path, ...]:
        """枚举 ambient_context 详情文件，不读取索引文件。"""
        root = self.markdown_store.file_store.resolve("data/ambient_context")
        if not root.exists():
            return ()
        return tuple(sorted(root.glob("*/*/*.md")))

    def _read_record(self, path: Path) -> AmbientContextRecord:
        """从已落盘 Markdown 重建 ambient_context 记录。"""
        document = self.markdown_store.read_document(path)
        raw_refs = _parse_key_value_section(self.markdown_store.extract_section(path, "Raw Reference"))
        return AmbientContextRecord(
            record_id=document.frontmatter.get("record_id", ""),
            source_type=document.frontmatter.get("source_type", ""),
            collector_name=document.frontmatter.get("collector_name", ""),
            source_id=document.frontmatter.get("source_id", ""),
            sync_scope_id=document.frontmatter.get("sync_scope_id", ""),
            created_at=document.frontmatter.get("created_at", ""),
            fetched_at=document.frontmatter.get("fetched_at", ""),
            text=self.markdown_store.extract_section(path, "Extracted Text"),
            text_preview=document.frontmatter.get("text_preview", ""),
            summary=self.markdown_store.extract_section(path, "Summary"),
            raw_message_ref=raw_refs.get("raw_message_ref", document.frontmatter.get("raw_message_ref", "")),
            sync_state_ref=raw_refs.get("sync_state", ""),
            doc_links=_parse_doc_links(self.markdown_store.extract_section(path, "Doc Links")),
            file_clues=_parse_file_clues(self.markdown_store.extract_section(path, "File Clues")),
            frontmatter_extra=_extra_frontmatter(document.frontmatter),
        )

    def _update_index(
        self,
        index_path: Path,
        source_type: str,
        row: dict[str, str],
    ) -> Path:
        """用 record_id 去重更新指定索引文件。"""
        rows = list(_read_index_rows(self.markdown_store, index_path))
        rows = [item for item in rows if item.get("record_id") != row["record_id"]]
        rows.append(row)
        rows.sort(key=lambda item: (item.get("created_at", ""), item.get("record_id", "")))
        document = MarkdownDocument(
            frontmatter=_index_frontmatter(source_type),
            body=_index_body(source_type, rows),
        )
        return self.markdown_store.write_document(index_path, document)


def _frontmatter_from_record(record: AmbientContextRecord) -> dict[str, str]:
    """把记录转为简单 frontmatter。"""
    frontmatter = {
        "schema": AMBIENT_CONTEXT_SCHEMA,
        "record_id": record.record_id,
        "source_type": record.source_type,
        "collector_name": record.collector_name,
        "source_id": record.source_id,
        "sync_scope_id": record.sync_scope_id,
        "created_at": record.created_at,
        "fetched_at": record.fetched_at,
        "text_preview": _preview(record),
        "doc_links": _doc_link_tokens(record.doc_links),
        "doc_link_count": str(len(record.doc_links)),
        "file_clues": _file_clue_tokens(record.file_clues),
        "file_clue_count": str(len(record.file_clues)),
        "raw_message_ref": record.raw_message_ref,
    }
    for key, value in record.frontmatter_extra.items():
        frontmatter[str(key)] = str(value)
    return {key: _frontmatter_value(value) for key, value in frontmatter.items()}


def _body_from_record(record: AmbientContextRecord) -> str:
    """渲染便于人工检查和检索的 Markdown 正文。"""
    summary = record.summary or _default_summary(record)
    return (
        f"# Ambient Context {record.record_id}\n\n"
        "## Summary\n\n"
        f"{summary}\n\n"
        "## Extracted Text\n\n"
        f"{record.text or _preview(record)}\n\n"
        "## Source Metadata\n\n"
        f"{_metadata_table(record)}\n\n"
        "## Doc Links\n\n"
        f"{_doc_link_table(record.doc_links)}\n\n"
        "## File Clues\n\n"
        f"{_file_clue_table(record.file_clues)}\n\n"
        "## Raw Reference\n\n"
        f"- raw_message_ref: {record.raw_message_ref}\n"
        f"- sync_state: {record.sync_state_ref}\n"
    )


def _metadata_table(record: AmbientContextRecord) -> str:
    """渲染基础来源元信息表。"""
    rows = (
        ("source_type", record.source_type),
        ("collector_name", record.collector_name),
        ("source_id", record.source_id),
        ("sync_scope_id", record.sync_scope_id),
        ("created_at", record.created_at),
        ("fetched_at", record.fetched_at),
    )
    lines = ["| key | value |", "|---|---|"]
    lines.extend(f"| {_cell(key)} | {_cell(value)} |" for key, value in rows)
    return "\n".join(lines)


def _doc_link_table(doc_links: tuple[AmbientDocLink, ...]) -> str:
    """渲染飞书文档链接线索表。"""
    lines = ["| url | resource_type | token |", "|---|---|---|"]
    for link in doc_links:
        lines.append(f"| {_cell(link.url)} | {_cell(link.resource_type)} | {_cell(link.token)} |")
    return "\n".join(lines)


def _file_clue_table(file_clues: tuple[AmbientFileClue, ...]) -> str:
    """渲染附件线索表。"""
    lines = ["| message_id | msg_type | file_key | file_name |", "|---|---|---|---|"]
    for clue in file_clues:
        lines.append(
            f"| {_cell(clue.message_id)} | {_cell(clue.msg_type)} | "
            f"{_cell(clue.file_key)} | {_cell(clue.file_name)} |"
        )
    return "\n".join(lines)


def _index_row(
    record: AmbientContextRecord,
    project_root: Path,
    detail_path: Path,
) -> dict[str, str]:
    """构造索引表中的一行。"""
    return {
        "record_id": record.record_id,
        "source_type": record.source_type,
        "collector_name": record.collector_name,
        "source_id": record.source_id,
        "sync_scope_id": record.sync_scope_id,
        "created_at": record.created_at,
        "fetched_at": record.fetched_at,
        "detail_file": _relative_path(project_root, detail_path),
        "text_preview": _truncate(_preview(record), MAX_INDEX_PREVIEW_CHARS),
    }


def _index_frontmatter(source_type: str) -> dict[str, str]:
    """构造索引 Markdown frontmatter。"""
    return {
        "schema": AMBIENT_CONTEXT_INDEX_SCHEMA,
        "source_type": _frontmatter_value(source_type),
        "updated_at": _now_iso(),
    }


def _index_body(source_type: str, rows: list[dict[str, str]]) -> str:
    """渲染索引 Markdown 正文。"""
    headers = (
        "record_id",
        "source_type",
        "collector_name",
        "source_id",
        "sync_scope_id",
        "created_at",
        "fetched_at",
        "detail_file",
        "text_preview",
    )
    lines = [f"# Ambient Context Index {source_type}", "", _table_header(headers)]
    for row in rows:
        lines.append("| " + " | ".join(_cell(row.get(header, "")) for header in headers) + " |")
    return "\n".join(lines) + "\n"


def _read_index_rows(
    markdown_store: MarkdownStore,
    index_path: Path,
) -> tuple[dict[str, str], ...]:
    """读取索引文档中的第一张表；索引不存在或异常时返回空。"""
    if not markdown_store.exists(index_path):
        return ()
    try:
        body = markdown_store.read_document(index_path).body
    except Exception:  # noqa: BLE001
        return ()
    return tuple(_parse_table_rows(body))


def _parse_table_rows(body: str) -> list[dict[str, str]]:
    """解析简单 Markdown 表格行。"""
    table_lines = [line.strip() for line in body.splitlines() if line.strip().startswith("|")]
    if len(table_lines) < 2:
        return []
    headers = [_clean_cell(cell) for cell in table_lines[0].strip("|").split("|")]
    rows: list[dict[str, str]] = []
    for line in table_lines[2:]:
        cells = [_clean_cell(cell) for cell in line.strip("|").split("|")]
        if len(cells) == len(headers):
            rows.append(dict(zip(headers, cells, strict=True)))
    return rows


def _parse_doc_links(section_text: str) -> tuple[AmbientDocLink, ...]:
    """从 Doc Links 表格还原文档链接线索。"""
    rows = _parse_table_rows(section_text)
    return tuple(
        AmbientDocLink(row.get("url", ""), row.get("resource_type", ""), row.get("token", ""))
        for row in rows
        if row.get("url") or row.get("token")
    )


def _parse_file_clues(section_text: str) -> tuple[AmbientFileClue, ...]:
    """从 File Clues 表格还原附件线索。"""
    rows = _parse_table_rows(section_text)
    return tuple(
        AmbientFileClue(
            row.get("message_id", ""),
            row.get("msg_type", ""),
            row.get("file_key", ""),
            row.get("file_name", ""),
        )
        for row in rows
        if row.get("message_id") or row.get("file_key")
    )


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


def _extra_frontmatter(frontmatter: Mapping[str, str]) -> dict[str, str]:
    """保留调用方额外写入的 frontmatter 字段。"""
    return {key: value for key, value in frontmatter.items() if key not in _BASE_FRONTMATTER_KEYS}


def _record_matches_query(record: AmbientContextRecord, query: AmbientContextScanQuery) -> bool:
    """判断记录是否符合扫描条件。"""
    if query.source_type and record.source_type != query.source_type:
        return False
    if query.collector_name and record.collector_name != query.collector_name:
        return False
    if query.record_ids and record.record_id not in query.record_ids:
        return False
    return _created_at_in_window(record.created_at, query.created_after, query.created_before)


def _created_at_in_window(created_at: str, created_after: str, created_before: str) -> bool:
    """判断 created_at 是否落在查询时间窗口内。"""
    current = _normalized_datetime(created_at)
    after = _normalized_datetime(created_after)
    before = _normalized_datetime(created_before)
    if after is not None and (current is None or current <= after):
        return False
    if before is not None and (current is None or current > before):
        return False
    return True


def _scan_limit(limit: int) -> int:
    """限制单次扫描返回记录数，避免 context packet 超过主动分析预算。"""
    if limit <= 0:
        return 0
    return min(limit, MAX_CONTEXT_PACKET_RECORDS)


def _datetime_order_key(value: str) -> str:
    """把时间转为稳定排序键，空时间排在最前。"""
    current = _normalized_datetime(value)
    if current is None:
        return ""
    return current.isoformat()


def _normalized_datetime(value: str) -> datetime | None:
    """把 ISO-8601 时间规整为 UTC aware datetime，便于比较。"""
    current = _parse_datetime(value)
    if current is None:
        return None
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _packet_record(
    project_root: Path,
    store: AmbientContextStore,
    record: AmbientContextRecord,
) -> AmbientContextPacketRecord:
    """把完整 ambient_context 记录裁剪为 packet 单条摘要。"""
    return AmbientContextPacketRecord(
        record_id=record.record_id,
        source_type=record.source_type,
        collector_name=record.collector_name,
        source_id=record.source_id,
        sync_scope_id=record.sync_scope_id,
        created_at=record.created_at,
        fetched_at=record.fetched_at,
        detail_file=_relative_path(project_root, store.path_for(record)),
        text_preview=_truncate(record.text_preview or record.text, MAX_INDEX_PREVIEW_CHARS),
        summary=record.summary,
        doc_links=record.doc_links,
        file_clues=record.file_clues,
    )


def _packet_id(record_ids: tuple[str, ...]) -> str:
    """按 record_ids 生成稳定 packet ID。"""
    if not record_ids:
        return "ambpkt_empty"
    digest = sha256("|".join(record_ids).encode("utf-8")).hexdigest()[:12]
    return "ambpkt_" + digest


def _packet_source_type(records: tuple[AmbientContextRecord, ...]) -> str:
    """生成 packet 的来源类型摘要。"""
    source_types = _unique(record.source_type for record in records)
    if len(source_types) == 1:
        return source_types[0]
    if source_types:
        return "mixed"
    return ""


def _unique(values) -> tuple[str, ...]:
    """保留输入顺序去重并过滤空字符串。"""
    result: list[str] = []
    for value in values:
        text = str(value).strip()
        if text and text not in result:
            result.append(text)
    return tuple(result)


def _parse_datetime(value: str) -> datetime | None:
    """解析 ISO-8601 时间；空值返回 None，非法值抛出清晰错误。"""
    text = str(value).strip()
    if not text:
        return None
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        return datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError("invalid ambient_context datetime: " + text) from exc


def _table_header(headers: tuple[str, ...]) -> str:
    """渲染 Markdown 表头和分隔行。"""
    head = "| " + " | ".join(headers) + " |"
    sep = "| " + " | ".join("---" for _item in headers) + " |"
    return head + "\n" + sep


def _preview(record: AmbientContextRecord) -> str:
    """返回记录预览文本，优先使用显式 text_preview。"""
    return record.text_preview or _truncate(record.text, MAX_INDEX_PREVIEW_CHARS)


def _default_summary(record: AmbientContextRecord) -> str:
    """构造缺省摘要。"""
    return f"{record.collector_name} captured {record.source_type} from {record.source_id}."


def _doc_link_tokens(doc_links: tuple[AmbientDocLink, ...]) -> str:
    """把文档链接 token 压成 frontmatter 友好的逗号字符串。"""
    values = [link.token or link.url for link in doc_links]
    return ",".join(value for value in values if value)


def _file_clue_tokens(file_clues: tuple[AmbientFileClue, ...]) -> str:
    """把附件线索压成 frontmatter 友好的逗号字符串。"""
    values = [clue.file_key or clue.message_id for clue in file_clues]
    return ",".join(value for value in values if value)


def _frontmatter_value(value: str) -> str:
    """把外部字符串转换为 MarkdownStore 可接受的单行 frontmatter 值。"""
    clean = str(value).replace("\r", " ").replace("\n", " ").strip()
    if clean.strip().startswith(("[", "{", "-")):
        return "'" + clean.replace("'", "’") + "'"
    return clean


def _cell(value: str) -> str:
    """转义 Markdown 表格单元格。"""
    return str(value).replace("\n", " ").replace("|", "\\|").strip()


def _clean_cell(value: str) -> str:
    """清理 Markdown 表格单元格文本。"""
    return value.replace("\\|", "|").strip()


def _truncate(text: str, limit: int) -> str:
    """按字符数裁剪文本预览。"""
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _date_part(value: str) -> str:
    """从 ISO 时间或时间戳字符串中提取日期分片。"""
    if value and len(value) >= 10 and value[4:5] == "-" and value[7:8] == "-":
        return value[:10]
    return datetime.now().astimezone().date().isoformat()


def _relative_path(root: Path, path: Path) -> str:
    """返回工作区相对路径，便于索引跨目录引用。"""
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def _relative_reference(root: Path, value: str) -> str:
    """返回项目内引用的相对路径；项目外路径保持原值用于排查。"""
    text = str(value).strip()
    if not text:
        return ""
    path = Path(text)
    if not path.is_absolute():
        return text
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return text


def _safe_file_part(value: str) -> str:
    """把外部 ID 转换为安全文件名片段。"""
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value)
    return safe[:MAX_SAFE_FILE_PART_CHARS] or "unknown"


def _now_iso() -> str:
    """返回 UTC ISO 时间字符串。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


_BASE_FRONTMATTER_KEYS = frozenset(
    {
        "schema",
        "record_id",
        "source_type",
        "collector_name",
        "source_id",
        "sync_scope_id",
        "created_at",
        "fetched_at",
        "text_preview",
        "doc_links",
        "doc_link_count",
        "file_clues",
        "file_clue_count",
        "raw_message_ref",
    }
)


def _self_test() -> None:
    """验证 ambient_context 记录和索引可写入。"""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        store = AmbientContextStore(Path(tmp))
        record = AmbientContextRecord(
            record_id="dm_om_1",
            source_type="direct_message",
            collector_name="direct_message_collector",
            source_id="oc_1",
            sync_scope_id="oc_1",
            created_at="2026-05-06T12:00:00+08:00",
            fetched_at="2026-05-06T12:01:00+08:00",
            text="hello",
            doc_links=(AmbientDocLink("https://example.feishu.cn/docx/token", "docx", "token"),),
        )
        result = store.write(record)
        assert result.path.exists()
        assert result.source_index_path.exists()
        packet = store.build_context_packet(AmbientContextScanQuery(source_type="direct_message"))
        assert packet.record_ids == ("dm_om_1",)
        assert packet.to_payload()["records"][0]["detail_file"].endswith("dm_om_1.md")


if __name__ == "__main__":
    _self_test()
    print("dutyflow feishu ambient context self-test passed")
