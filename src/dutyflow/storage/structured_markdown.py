# 本文件负责结构化 Markdown 记录的轻量解析、定位和受控更新。

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from dutyflow.storage.file_store import FileStore
from dutyflow.storage.markdown_store import MarkdownDocument, MarkdownStore


@dataclass(frozen=True)
class StructuredSchema:
    """描述一类结构化 Markdown 记录的稳定约束。"""

    family: str
    record_schema: str
    id_field: str
    detail_glob: str
    detail_path_template: str
    title_template: str
    allowed_sections: tuple[str, ...]
    summary_section: str
    change_log_section: str = ""
    index_path: str = ""


@dataclass(frozen=True)
class StructuredRecord:
    """保存单条结构化 Markdown 记录及其已解析 section。"""

    path: Path
    frontmatter: dict[str, str]
    body: str
    sections: dict[str, str]
    section_order: tuple[str, ...]

    @property
    def record_id(self) -> str:
        """返回记录稳定 ID。"""
        return self.frontmatter.get("id", "")


class SchemaRegistry:
    """维护结构化 Markdown 数据族的 schema 定义。"""

    def __init__(self) -> None:
        """初始化当前已支持的数据族定义。"""
        self._schemas = {
            "contact_knowledge": StructuredSchema(
                family="contact_knowledge",
                record_schema="dutyflow.contact_knowledge_note.v1",
                id_field="id",
                detail_glob="data/knowledge/contacts/contact_*/ckn_*.md",
                detail_path_template="data/knowledge/contacts/{contact_id}/{record_id}.md",
                title_template="Contact Knowledge {record_id}",
                allowed_sections=(
                    "Summary",
                    "Structured Facts",
                    "Decision Value",
                    "Change Log",
                ),
                summary_section="Summary",
                change_log_section="Change Log",
            ),
            "long_term_memory": StructuredSchema(
                family="long_term_memory",
                record_schema="dutyflow.long_term_memory.v1",
                id_field="id",
                detail_glob="data/memory/entries/memory_*.md",
                detail_path_template="data/memory/entries/{record_id}.md",
                title_template="Memory {record_id}",
                allowed_sections=(
                    "Summary",
                    "Memory Body",
                    "Structured Facts",
                    "Retrieval Hints",
                    "Validation",
                    "Change Log",
                ),
                summary_section="Summary",
                change_log_section="Change Log",
                index_path="data/memory/index.md",
            ),
        }

    def get(self, family: str) -> StructuredSchema:
        """按数据族名称获取 schema。"""
        if family not in self._schemas:
            raise KeyError(f"unknown structured markdown family: {family}")
        return self._schemas[family]


class FrontmatterParser:
    """读取结构化 Markdown 文档并拆分 frontmatter 与 section。"""

    def __init__(self, root: Path) -> None:
        """绑定工作区根目录。"""
        self.root = Path(root).resolve()
        self.store = MarkdownStore(FileStore(self.root))

    def parse(self, path: Path | str) -> StructuredRecord:
        """读取单条 Markdown 记录并拆分正文 section。"""
        document = self.store.read_document(path)
        resolved = self.store.file_store.resolve(path)
        sections, order = _split_sections(document.body)
        return StructuredRecord(
            path=resolved,
            frontmatter=dict(document.frontmatter),
            body=document.body,
            sections=sections,
            section_order=order,
        )

    def read_index_rows(self, path: Path | str) -> tuple[dict[str, str], ...]:
        """读取索引文档中的第一张 Markdown 表。"""
        document = self.store.read_document(path)
        return _parse_first_table(document.body)


class RecordLocator:
    """负责按数据族扫描记录、读取索引并按 ID 定位。"""

    def __init__(
        self,
        root: Path,
        schema_registry: SchemaRegistry,
        parser: FrontmatterParser,
    ) -> None:
        """绑定工作区、schema 定义和解析器。"""
        self.root = Path(root).resolve()
        self.schema_registry = schema_registry
        self.parser = parser

    def list_records(self, family: str) -> tuple[StructuredRecord, ...]:
        """返回某个数据族下的全部候选记录。"""
        schema = self.schema_registry.get(family)
        paths = self._candidate_paths(schema)
        records = [self.parser.parse(path) for path in paths]
        valid = [record for record in records if record.frontmatter.get("schema") == schema.record_schema]
        return tuple(valid)

    def find_by_id(self, family: str, record_id: str) -> StructuredRecord | None:
        """按稳定 ID 返回唯一记录。"""
        schema = self.schema_registry.get(family)
        for record in self.list_records(family):
            if record.frontmatter.get(schema.id_field) == record_id:
                return record
        return None

    def read_index_rows(self, family: str) -> tuple[dict[str, str], ...]:
        """读取数据族索引表；索引不存在时返回空元组。"""
        schema = self.schema_registry.get(family)
        if not schema.index_path:
            return ()
        path = self.root / schema.index_path
        if not path.exists():
            return ()
        return self.parser.read_index_rows(path)

    def _candidate_paths(self, schema: StructuredSchema) -> tuple[Path, ...]:
        """优先用索引，否则按目录 glob 扫描候选文件。"""
        if schema.index_path:
            indexed = self._paths_from_index(schema)
            if indexed:
                return indexed
        return tuple(sorted(self.root.glob(schema.detail_glob)))

    def _paths_from_index(self, schema: StructuredSchema) -> tuple[Path, ...]:
        """从索引表读取 detail_file 并解析成文件路径。"""
        rows = self.read_index_rows(schema.family)
        if not rows:
            return ()
        index_path = self.root / schema.index_path
        base_dir = index_path.parent
        paths: list[Path] = []
        for row in rows:
            detail_file = row.get("detail_file", "").strip()
            if not detail_file:
                continue
            candidate = (base_dir / detail_file).resolve()
            if candidate.exists() and candidate.is_relative_to(self.root):
                paths.append(candidate)
        return tuple(paths)


class SectionExtractor:
    """负责从记录中提取允许暴露的 section。"""

    def extract(self, record: StructuredRecord, section_names: tuple[str, ...]) -> dict[str, str]:
        """按 section 名称抽取正文片段。"""
        return {name: record.sections.get(name, "") for name in section_names}


class SnippetBuilder:
    """把结构化记录拼成模型可消费的稳定轻量结果。"""

    def build_header(
        self,
        record: StructuredRecord,
        *,
        id_key: str,
        fields: tuple[str, ...],
        summary_section: str,
        root: Path,
    ) -> dict[str, str]:
        """构造单条 header 结果。"""
        payload = {field: record.frontmatter.get(field, "") for field in fields}
        payload[id_key] = record.frontmatter.get(id_key, "")
        payload["summary"] = record.sections.get(summary_section, "")
        payload["source_file"] = _relative_path(root, record.path)
        return payload

    def build_detail(
        self,
        record: StructuredRecord,
        *,
        id_key: str,
        section_names: tuple[str, ...],
        root: Path,
    ) -> dict[str, str]:
        """构造 detail 结果。"""
        payload = {id_key: record.frontmatter.get(id_key, "")}
        for section_name in section_names:
            payload[_section_key(section_name)] = record.sections.get(section_name, "")
        payload["source_file"] = _relative_path(root, record.path)
        return payload


class StructuredRecordUpdater:
    """负责受控新增和更新结构化 Markdown 记录。"""

    def __init__(
        self,
        root: Path,
        schema_registry: SchemaRegistry,
        parser: FrontmatterParser,
        locator: RecordLocator,
    ) -> None:
        """绑定工作区、schema 和解析辅助对象。"""
        self.root = Path(root).resolve()
        self.schema_registry = schema_registry
        self.parser = parser
        self.locator = locator
        self.store = MarkdownStore(FileStore(self.root))

    def create_record(
        self,
        family: str,
        *,
        record_id: str,
        frontmatter: Mapping[str, str],
        sections: Mapping[str, str],
    ) -> StructuredRecord:
        """创建新记录并返回解析后的结果。"""
        schema = self.schema_registry.get(family)
        path = self._build_detail_path(schema, record_id, frontmatter)
        if path.exists():
            raise ValueError(f"record already exists: {record_id}")
        body = self._render_body(schema, record_id, sections)
        document = MarkdownDocument(dict(frontmatter), body)
        self.store.write_document(path, document)
        return self.parser.parse(path)

    def update_record(
        self,
        family: str,
        *,
        record_id: str,
        frontmatter_updates: Mapping[str, str],
        section_updates: Mapping[str, str],
        change_note: str = "",
        change_action: str = "updated",
    ) -> StructuredRecord:
        """更新已有记录并返回最新解析结果。"""
        schema = self.schema_registry.get(family)
        record = self.locator.find_by_id(family, record_id)
        if record is None:
            raise KeyError(f"record not found: {record_id}")
        frontmatter = dict(record.frontmatter)
        frontmatter.update(frontmatter_updates)
        sections = dict(record.sections)
        sections.update(section_updates)
        sections = self._with_change_log(schema, sections, change_action, change_note)
        body = _render_sections(self._merged_order(schema, record), sections, self._title(schema, record_id))
        document = MarkdownDocument(frontmatter, body)
        self.store.write_document(record.path, document)
        return self.parser.parse(record.path)

    def _build_detail_path(
        self,
        schema: StructuredSchema,
        record_id: str,
        frontmatter: Mapping[str, str],
    ) -> Path:
        """根据 schema 模板计算 detail 文件路径。"""
        variables = dict(frontmatter)
        variables["record_id"] = record_id
        relative = schema.detail_path_template.format(**variables)
        return self.root / relative

    def _render_body(
        self,
        schema: StructuredSchema,
        record_id: str,
        sections: Mapping[str, str],
    ) -> str:
        """按 schema 允许的 section 顺序渲染正文。"""
        normalized = {name: sections.get(name, "") for name in schema.allowed_sections}
        normalized = self._with_change_log(schema, normalized, "created", "初次记录")
        return _render_sections(schema.allowed_sections, normalized, self._title(schema, record_id))

    def _with_change_log(
        self,
        schema: StructuredSchema,
        sections: dict[str, str],
        action: str,
        note: str,
    ) -> dict[str, str]:
        """在存在 Change Log section 时追加一条记录。"""
        if not schema.change_log_section:
            return sections
        timestamp = _now_iso()
        existing = sections.get(schema.change_log_section, "")
        sections[schema.change_log_section] = _append_change_log(existing, timestamp, action, note or action)
        return sections

    def _merged_order(self, schema: StructuredSchema, record: StructuredRecord) -> tuple[str, ...]:
        """更新时优先保留既有 section 顺序，再补全 schema 约定 section。"""
        order = list(record.section_order)
        for section_name in schema.allowed_sections:
            if section_name not in order:
                order.append(section_name)
        return tuple(order)

    def _title(self, schema: StructuredSchema, record_id: str) -> str:
        """返回记录标题。"""
        return schema.title_template.format(record_id=record_id)


def _split_sections(body: str) -> tuple[dict[str, str], tuple[str, ...]]:
    """按二级标题拆分 Markdown 正文。"""
    sections: dict[str, list[str]] = {}
    order: list[str] = []
    current = ""
    for line in body.splitlines():
        if line.startswith("## "):
            current = line[3:].strip()
            sections.setdefault(current, [])
            order.append(current)
            continue
        if current:
            sections[current].append(line)
    rendered = {name: "\n".join(lines).strip() for name, lines in sections.items()}
    return rendered, tuple(order)


def _parse_first_table(body: str) -> tuple[dict[str, str], ...]:
    """读取正文中的第一张 Markdown 表。"""
    lines = [line.strip() for line in body.splitlines()]
    start = _find_table_start(lines)
    if start == -1:
        return ()
    headers = _split_table_row(lines[start])
    rows: list[dict[str, str]] = []
    index = start + 2
    while index < len(lines) and lines[index].startswith("|"):
        values = _split_table_row(lines[index])
        if len(values) == len(headers):
            rows.append(dict(zip(headers, values, strict=True)))
        index += 1
    return tuple(rows)


def _find_table_start(lines: list[str]) -> int:
    """返回第一张 Markdown 表头所在行号。"""
    for index in range(len(lines) - 1):
        if lines[index].startswith("|") and lines[index + 1].startswith("|---"):
            return index
    return -1


def _split_table_row(line: str) -> list[str]:
    """把单行 Markdown 表拆成列值。"""
    return [cell.strip() for cell in line.strip("|").split("|")]


def _render_sections(
    section_order: tuple[str, ...],
    sections: Mapping[str, str],
    title: str,
) -> str:
    """按固定顺序渲染 Markdown 正文。"""
    parts = [f"# {title}"]
    for section_name in section_order:
        parts.append(f"\n## {section_name}\n")
        content = sections.get(section_name, "").strip()
        parts.append(content if content else "")
    return "\n".join(parts).rstrip() + "\n"


def _append_change_log(existing: str, at: str, action: str, note: str) -> str:
    """向 Change Log 表中追加一行。"""
    row = f"| {at} | {action} | {note} |"
    if existing.strip():
        return existing.rstrip() + "\n" + row
    header = "| at | action | note |\n|---|---|---|"
    return header + "\n" + row


def _section_key(section_name: str) -> str:
    """把 section 名称转换成稳定字段名。"""
    return section_name.lower().replace(" ", "_")


def _relative_path(root: Path, path: Path) -> str:
    """返回相对工作区的稳定文件路径。"""
    return str(path.resolve().relative_to(root.resolve()))


def _now_iso() -> str:
    """返回当前 UTC 时间的 ISO-8601 字符串。"""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _self_test() -> None:
    """验证结构化 Markdown 更新器能创建并回读联系人知识记录。"""
    import tempfile

    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        registry = SchemaRegistry()
        parser = FrontmatterParser(root)
        locator = RecordLocator(root, registry, parser)
        updater = StructuredRecordUpdater(root, registry, parser, locator)
        created = updater.create_record(
            "contact_knowledge",
            record_id="ckn_demo",
            frontmatter={
                "schema": "dutyflow.contact_knowledge_note.v1",
                "id": "ckn_demo",
                "contact_id": "contact_001",
                "topic": "working_preference",
                "keywords": "async",
                "confidence": "medium",
                "status": "active",
                "source_refs": "manual_input",
                "created_at": _now_iso(),
                "updated_at": _now_iso(),
            },
            sections={"Summary": "demo"},
        )
        assert created.record_id == "ckn_demo"
        assert locator.find_by_id("contact_knowledge", "ckn_demo") is not None


if __name__ == "__main__":
    _self_test()
    print("dutyflow structured markdown self-test passed")
