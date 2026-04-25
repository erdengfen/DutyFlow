# 本文件负责联系人补充知识记录的查询、读取和受控写入。

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from dutyflow.storage.structured_markdown import (
    FrontmatterParser,
    RecordLocator,
    SchemaRegistry,
    SnippetBuilder,
    StructuredRecord,
    StructuredRecordUpdater,
    _now_iso,
)


class ContactKnowledgeService:
    """围绕 contact_knowledge 数据族提供稳定业务操作。"""

    def __init__(self, root: Path) -> None:
        """绑定工作区并初始化结构化 Markdown 组件。"""
        self.root = Path(root).resolve()
        self.schemas = SchemaRegistry()
        self.parser = FrontmatterParser(self.root)
        self.locator = RecordLocator(self.root, self.schemas, self.parser)
        self.builder = SnippetBuilder()
        self.updater = StructuredRecordUpdater(self.root, self.schemas, self.parser, self.locator)

    def search_headers(self, tool_input: dict[str, object]) -> dict[str, object]:
        """按 contact、topic、关键词和 query 搜索知识 header。"""
        contact_ids = self._resolve_contact_ids(tool_input)
        if _name_query_unresolved(tool_input, contact_ids):
            return {"match_status": "not_found", "note_ids": [], "matched_by": "name", "headers": []}
        if len(contact_ids) > 1:
            return {"match_status": "ambiguous", "note_ids": [], "matched_by": "name", "headers": []}
        records = self._filter_records(self.locator.list_records("contact_knowledge"), tool_input, contact_ids)
        headers = [self._build_header(record) for record in self._sort_records(records)]
        return {
            "match_status": _collection_status(len(headers)),
            "note_ids": [item["note_id"] for item in headers],
            "matched_by": _matched_by(tool_input),
            "headers": headers,
        }

    def get_detail(self, note_id: str) -> dict[str, str]:
        """按 note_id 读取单条联系人知识 detail。"""
        record = self.locator.find_by_id("contact_knowledge", note_id)
        if record is None:
            raise KeyError(f"contact knowledge not found: {note_id}")
        detail = self.builder.build_detail(
            record,
            id_key="id",
            section_names=("Summary", "Structured Facts", "Decision Value", "Change Log"),
            root=self.root,
        )
        return {
            "note_id": detail["id"],
            "summary": detail["summary"],
            "structured_facts": detail["structured_facts"],
            "decision_value": detail["decision_value"],
            "change_log_preview": detail["change_log"],
            "source_file": detail["source_file"],
        }

    def add_record(self, tool_input: dict[str, object]) -> dict[str, str]:
        """创建新的联系人知识记录。"""
        note_id = f"ckn_{uuid4().hex[:8]}"
        contact_id = _required_text(tool_input, "contact_id")
        topic = _required_text(tool_input, "topic")
        summary = _required_text(tool_input, "summary")
        timestamp = _now_iso()
        record = self.updater.create_record(
            "contact_knowledge",
            record_id=note_id,
            frontmatter={
                "schema": "dutyflow.contact_knowledge_note.v1",
                "id": note_id,
                "contact_id": contact_id,
                "topic": topic,
                "keywords": _optional_text(tool_input, "keywords"),
                "confidence": "medium",
                "status": "active",
                "source_refs": _optional_text(tool_input, "source_refs"),
                "created_at": timestamp,
                "updated_at": timestamp,
            },
            sections={
                "Summary": summary,
                "Structured Facts": _optional_text(tool_input, "structured_facts_markdown"),
                "Decision Value": _optional_text(tool_input, "decision_value"),
            },
        )
        return {"note_id": note_id, "status": "created", "file_path": _source_path(self.root, record)}

    def update_record(self, tool_input: dict[str, object]) -> dict[str, str]:
        """更新已有联系人知识记录。"""
        note_id = _required_text(tool_input, "note_id")
        frontmatter_updates = {"updated_at": _now_iso()}
        section_updates: dict[str, str] = {}
        _assign_if_present(frontmatter_updates, tool_input, "status")
        _assign_if_present(frontmatter_updates, tool_input, "confidence")
        _assign_section_if_present(section_updates, tool_input, "summary", "Summary")
        _assign_section_if_present(
            section_updates,
            tool_input,
            "structured_facts_markdown",
            "Structured Facts",
        )
        _assign_section_if_present(section_updates, tool_input, "decision_value", "Decision Value")
        record = self.updater.update_record(
            "contact_knowledge",
            record_id=note_id,
            frontmatter_updates=frontmatter_updates,
            section_updates=section_updates,
            change_note=_optional_text(tool_input, "change_note") or "updated",
        )
        return {"note_id": note_id, "status": "updated", "file_path": _source_path(self.root, record)}

    def search_headers_json(self, tool_input: dict[str, object]) -> str:
        """返回 JSON 字符串，供工具层直接使用。"""
        return json.dumps(self.search_headers(tool_input), ensure_ascii=False)

    def get_detail_json(self, note_id: str) -> str:
        """返回 JSON 字符串 detail。"""
        return json.dumps(self.get_detail(note_id), ensure_ascii=False)

    def add_record_json(self, tool_input: dict[str, object]) -> str:
        """返回 JSON 字符串创建结果。"""
        return json.dumps(self.add_record(tool_input), ensure_ascii=False)

    def update_record_json(self, tool_input: dict[str, object]) -> str:
        """返回 JSON 字符串更新结果。"""
        return json.dumps(self.update_record(tool_input), ensure_ascii=False)

    def _resolve_contact_ids(self, tool_input: dict[str, object]) -> tuple[str, ...]:
        """优先使用 contact_id，否则按联系人索引解析 name。"""
        contact_id = _optional_text(tool_input, "contact_id")
        if contact_id:
            return (contact_id,)
        name = _optional_text(tool_input, "name")
        if not name:
            return ()
        return _resolve_contact_ids_by_name(self.locator, name)

    def _filter_records(
        self,
        records: tuple[StructuredRecord, ...],
        tool_input: dict[str, object],
        contact_ids: tuple[str, ...],
    ) -> list[StructuredRecord]:
        """按当前查询条件筛选联系人知识记录。"""
        filtered = list(records)
        filtered = _filter_by_contact_ids(filtered, contact_ids)
        filtered = _filter_by_exact_field(filtered, "topic", _optional_text(tool_input, "topic"))
        filtered = _filter_by_exact_field(filtered, "status", _optional_text(tool_input, "status"))
        filtered = _filter_by_keywords(filtered, _optional_text(tool_input, "keywords"))
        return _filter_by_query(filtered, _optional_text(tool_input, "query"))

    def _build_header(self, record: StructuredRecord) -> dict[str, str]:
        """把 record 组装成 search headers 返回结构。"""
        header = self.builder.build_header(
            record,
            id_key="id",
            fields=("contact_id", "topic", "keywords", "confidence", "status", "updated_at"),
            summary_section="Summary",
            root=self.root,
        )
        return {
            "note_id": header["id"],
            "contact_id": header["contact_id"],
            "topic": header["topic"],
            "keywords": header["keywords"],
            "confidence": header["confidence"],
            "status": header["status"],
            "updated_at": header["updated_at"],
            "summary": header["summary"],
            "source_file": header["source_file"],
        }

    def _sort_records(self, records: list[StructuredRecord]) -> list[StructuredRecord]:
        """按更新时间倒序，再按 ID 排序。"""
        return sorted(
            records,
            key=lambda item: (
                item.frontmatter.get("updated_at", ""),
                item.frontmatter.get("id", ""),
            ),
            reverse=True,
        )


def _resolve_contact_ids_by_name(locator: RecordLocator, name: str) -> tuple[str, ...]:
    """按联系人索引中的 display_name 和 aliases 解析 contact_id。"""
    rows = _load_contact_index_rows(locator)
    matched = {row.get("contact_id", "") for row in rows if _name_matches_row(name, row)}
    matched.discard("")
    return tuple(sorted(matched))


def _load_contact_index_rows(locator: RecordLocator) -> tuple[dict[str, str], ...]:
    """读取联系人索引表。"""
    path = locator.root / "data/identity/contacts/index.md"
    if not path.exists():
        return ()
    return locator.parser.read_index_rows(path)


def _name_matches_row(name: str, row: dict[str, str]) -> bool:
    """判断名称是否命中 display_name 或 aliases。"""
    candidate = name.strip().casefold()
    if not candidate:
        return False
    aliases = [item.strip().casefold() for item in row.get("aliases", "").split(",") if item.strip()]
    return candidate == row.get("display_name", "").strip().casefold() or candidate in aliases


def _filter_by_contact_ids(records: list[StructuredRecord], contact_ids: tuple[str, ...]) -> list[StructuredRecord]:
    """按 contact_id 精确筛选。"""
    if not contact_ids:
        return records
    return [record for record in records if record.frontmatter.get("contact_id", "") in contact_ids]


def _filter_by_exact_field(records: list[StructuredRecord], field: str, expected: str) -> list[StructuredRecord]:
    """按单个 frontmatter 字段做大小写无关精确匹配。"""
    if not expected:
        return records
    target = expected.casefold()
    return [record for record in records if record.frontmatter.get(field, "").casefold() == target]


def _filter_by_keywords(records: list[StructuredRecord], keywords: str) -> list[StructuredRecord]:
    """要求所有关键词都出现在前台轻量文本中。"""
    tokens = _csv_tokens(keywords)
    if not tokens:
        return records
    return [record for record in records if _contains_all_tokens(_header_haystack(record), tokens)]


def _filter_by_query(records: list[StructuredRecord], query: str) -> list[StructuredRecord]:
    """按自由 query 在 summary、topic 和 decision 中做包含匹配。"""
    token = query.strip().casefold()
    if not token:
        return records
    return [record for record in records if token in _header_haystack(record)]


def _contains_all_tokens(haystack: str, tokens: tuple[str, ...]) -> bool:
    """判断 haystack 是否包含全部关键词。"""
    return all(token in haystack for token in tokens)


def _header_haystack(record: StructuredRecord) -> str:
    """组合用于 header 搜索的轻量文本。"""
    parts = [
        record.frontmatter.get("contact_id", ""),
        record.frontmatter.get("topic", ""),
        record.frontmatter.get("keywords", ""),
        record.frontmatter.get("status", ""),
        record.sections.get("Summary", ""),
        record.sections.get("Decision Value", ""),
    ]
    return "\n".join(parts).casefold()


def _matched_by(tool_input: dict[str, object]) -> str:
    """返回当前搜索命中的输入字段摘要。"""
    fields = [name for name in ("contact_id", "name", "topic", "keywords", "query", "status") if _optional_text(tool_input, name)]
    return ",".join(fields)


def _name_query_unresolved(tool_input: dict[str, object], contact_ids: tuple[str, ...]) -> bool:
    """只传 name 且无法解析 contact_id 时，应明确返回 not_found。"""
    return bool(_optional_text(tool_input, "name") and not _optional_text(tool_input, "contact_id") and not contact_ids)


def _collection_status(count: int) -> str:
    """把记录数量转换成统一 match_status。"""
    if count <= 0:
        return "not_found"
    if count == 1:
        return "unique"
    return "multiple"


def _csv_tokens(value: str) -> tuple[str, ...]:
    """把英文逗号分隔字符串转换为查询 token。"""
    return tuple(item.strip().casefold() for item in value.split(",") if item.strip())


def _required_text(tool_input: dict[str, object], key: str) -> str:
    """读取必填字符串字段。"""
    value = _optional_text(tool_input, key)
    if not value:
        raise ValueError(f"{key} is required")
    return value


def _optional_text(tool_input: dict[str, object], key: str) -> str:
    """读取可选字符串字段。"""
    raw = tool_input.get(key, "")
    return str(raw).strip()


def _assign_if_present(target: dict[str, str], tool_input: dict[str, object], key: str) -> None:
    """只有在显式传入字段时才更新 frontmatter。"""
    if key in tool_input:
        target[key] = _optional_text(tool_input, key)


def _assign_section_if_present(
    target: dict[str, str],
    tool_input: dict[str, object],
    key: str,
    section_name: str,
) -> None:
    """只有在显式传入字段时才更新 section。"""
    if key in tool_input:
        target[section_name] = _optional_text(tool_input, key)


def _source_path(root: Path, record: StructuredRecord) -> str:
    """返回相对工作区的稳定文件路径。"""
    return str(record.path.relative_to(root))


def _self_test() -> None:
    """验证联系人知识服务可创建并读取单条记录。"""
    import tempfile

    with tempfile.TemporaryDirectory() as temp_dir:
        service = ContactKnowledgeService(Path(temp_dir))
        created = service.add_record(
            {
                "contact_id": "contact_001",
                "topic": "working_preference",
                "summary": "prefers async review",
                "keywords": "async, review",
            }
        )
        detail = service.get_detail(created["note_id"])
        assert detail["summary"] == "prefers async review"


if __name__ == "__main__":
    _self_test()
    print("dutyflow contact knowledge service self-test passed")
