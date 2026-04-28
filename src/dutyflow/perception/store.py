# 本文件负责把飞书关键事件转换为感知记录 Markdown，并向后续 loop 暴露标准读取接口。

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from dutyflow.feishu.events import FeishuEventEnvelope
from dutyflow.storage.file_store import FileStore
from dutyflow.storage.markdown_store import MarkdownDocument, MarkdownStore


@dataclass(frozen=True)
class PerceptionEntity:
    """表示感知记录中一条稳定实体线索。"""

    kind: str
    value: str
    source: str


@dataclass(frozen=True)
class PerceptionParseTarget:
    """表示后续内容解析工具可能消费的一条目标线索。"""

    target_id: str
    target_type: str
    file_key: str
    file_name: str
    url: str
    required_tool: str


@dataclass(frozen=True)
class PerceivedEventRecord:
    """表示已经落盘并可被后续 loop 消费的感知记录。"""

    path: Path
    record_id: str
    source_event_id: str
    message_id: str
    received_at: str
    event_type: str
    trigger_kind: str
    chat_type: str
    chat_id: str
    sender_open_id: str
    message_type: str
    mentions_bot: bool
    has_attachment: bool
    attachment_kinds: tuple[str, ...]
    raw_text: str
    content_preview: str
    mention_text: str
    entities: tuple[PerceptionEntity, ...]
    parse_targets: tuple[PerceptionParseTarget, ...]
    contact_lookup_hint: str
    source_lookup_hint: str
    responsibility_lookup_hint: str
    followup_needed: str
    raw_event_file: str

    def to_loop_input(self) -> dict[str, Any]:
        """返回后续 Agent Loop 可直接消费的标准输入。"""
        return {
            "perception_id": self.record_id,
            "perception_file": str(self.path),
            "source_event_id": self.source_event_id,
            "raw_event_file": self.raw_event_file,
            "message_id": self.message_id,
            "received_at": self.received_at,
            "event_type": self.event_type,
            "trigger_kind": self.trigger_kind,
            "chat_type": self.chat_type,
            "chat_id": self.chat_id,
            "sender_open_id": self.sender_open_id,
            "message_type": self.message_type,
            "mentions_bot": self.mentions_bot,
            "has_attachment": self.has_attachment,
            "attachment_kinds": list(self.attachment_kinds),
            "raw_text": self.raw_text,
            "content_preview": self.content_preview,
            "mention_text": self.mention_text,
            "entities": [_entity_to_dict(item) for item in self.entities],
            "parse_targets": [_parse_target_to_dict(item) for item in self.parse_targets],
            "contact_lookup_hint": self.contact_lookup_hint,
            "source_lookup_hint": self.source_lookup_hint,
            "responsibility_lookup_hint": self.responsibility_lookup_hint,
            "followup_needed": self.followup_needed,
        }


class PerceptionRecordService:
    """负责感知记录的生成、落盘和后续 loop 读取。"""

    def __init__(
        self,
        project_root: Path,
        *,
        markdown_store: MarkdownStore | None = None,
    ) -> None:
        """绑定工作区和 Markdown 存储依赖。"""
        self.project_root = Path(project_root).resolve()
        self.markdown_store = markdown_store or MarkdownStore(FileStore(self.project_root))
        self.perception_dir = self.project_root / "data" / "perception"
        self.markdown_store.file_store.ensure_dir(self.perception_dir)

    def create_record(
        self,
        envelope: FeishuEventEnvelope,
        raw_event_record_path: Path,
    ) -> PerceivedEventRecord:
        """根据单条飞书事件生成或覆盖对应的感知记录。"""
        record = _build_perceived_record(self.project_root, envelope, raw_event_record_path)
        document = MarkdownDocument(
            frontmatter=_build_frontmatter(record),
            body=_build_body(record),
        )
        self.markdown_store.write_document(record.path, document)
        return record

    def read_by_record_id(self, record_id: str) -> PerceivedEventRecord | None:
        """按稳定记录 ID 读取感知记录。"""
        for path in self._iter_record_paths():
            if path.stem == record_id:
                return self._read_record(path)
        return None

    def read_by_message_id(self, message_id: str) -> PerceivedEventRecord | None:
        """按消息 ID 读取感知记录。"""
        record_id = "per_" + _sanitize_suffix(message_id)
        return self.read_by_record_id(record_id)

    def build_loop_input(
        self,
        *,
        record_id: str = "",
        message_id: str = "",
    ) -> dict[str, Any] | None:
        """为后续 loop 提供统一的感知输入结构。"""
        record = self._resolve_record(record_id=record_id, message_id=message_id)
        if record is None:
            return None
        return record.to_loop_input()

    def _resolve_record(
        self,
        *,
        record_id: str,
        message_id: str,
    ) -> PerceivedEventRecord | None:
        """按 record_id 或 message_id 解析唯一感知记录。"""
        if record_id:
            return self.read_by_record_id(record_id)
        if message_id:
            return self.read_by_message_id(message_id)
        return None

    def _iter_record_paths(self) -> tuple[Path, ...]:
        """遍历当前工作区下全部感知记录路径。"""
        return tuple(sorted(self.perception_dir.glob("*/*.md")))

    def _read_record(self, path: Path) -> PerceivedEventRecord:
        """从已落盘 Markdown 重建感知记录对象。"""
        document = self.markdown_store.read_document(path)
        extracted_text = _parse_key_value_section(
            self.markdown_store.extract_section(path, "Extracted Text")
        )
        lookup_hints = _parse_key_value_section(
            self.markdown_store.extract_section(path, "Lookup Hints")
        )
        return PerceivedEventRecord(
            path=path,
            record_id=document.frontmatter.get("id", ""),
            source_event_id=document.frontmatter.get("source_event_id", ""),
            message_id=document.frontmatter.get("message_id", ""),
            received_at=document.frontmatter.get("received_at", ""),
            event_type=document.frontmatter.get("event_type", ""),
            trigger_kind=document.frontmatter.get("trigger_kind", ""),
            chat_type=document.frontmatter.get("chat_type", ""),
            chat_id=document.frontmatter.get("chat_id", ""),
            sender_open_id=document.frontmatter.get("sender_open_id", ""),
            message_type=document.frontmatter.get("message_type", ""),
            mentions_bot=_as_bool(document.frontmatter.get("mentions_bot", "")),
            has_attachment=_as_bool(document.frontmatter.get("has_attachment", "")),
            attachment_kinds=_split_csv(document.frontmatter.get("attachment_kinds", "")),
            raw_text=extracted_text.get("raw_text", ""),
            content_preview=extracted_text.get("content_preview", ""),
            mention_text=extracted_text.get("mention_text", ""),
            entities=_parse_entities(self.markdown_store.extract_section(path, "Entities")),
            parse_targets=_parse_targets(self.markdown_store.extract_section(path, "Parse Targets")),
            contact_lookup_hint=lookup_hints.get("contact_lookup_hint", ""),
            source_lookup_hint=lookup_hints.get("source_lookup_hint", ""),
            responsibility_lookup_hint=lookup_hints.get("responsibility_lookup_hint", ""),
            followup_needed=lookup_hints.get("followup_needed", ""),
            raw_event_file=document.frontmatter.get("raw_event_file", ""),
        )


def _build_perceived_record(
    project_root: Path,
    envelope: FeishuEventEnvelope,
    raw_event_record_path: Path,
) -> PerceivedEventRecord:
    """把飞书事件转换成稳定的感知记录对象。"""
    content_payload = _parse_message_content(envelope.raw_event)
    parse_targets = _build_parse_targets(envelope, content_payload)
    attachment_kinds = _collect_attachment_kinds(envelope, parse_targets)
    record_id = "per_" + _sanitize_suffix(envelope.message_id or envelope.event_id)
    record_path = _build_record_path(project_root, envelope.received_at, record_id)
    entities = _build_entities(envelope)
    return PerceivedEventRecord(
        path=record_path,
        record_id=record_id,
        source_event_id=raw_event_record_path.stem,
        message_id=envelope.message_id,
        received_at=envelope.received_at,
        event_type=envelope.event_type,
        trigger_kind=_build_trigger_kind(envelope, attachment_kinds),
        chat_type=envelope.chat_type,
        chat_id=envelope.chat_id,
        sender_open_id=envelope.sender_open_id,
        message_type=envelope.message_type or "text",
        mentions_bot=envelope.mentions_bot,
        has_attachment=bool(attachment_kinds),
        attachment_kinds=attachment_kinds,
        raw_text=envelope.message_text,
        content_preview=envelope.content_preview,
        mention_text=",".join(envelope.mentioned_open_ids),
        entities=entities,
        parse_targets=parse_targets,
        contact_lookup_hint=f"feishu_open_id={envelope.sender_open_id}",
        source_lookup_hint=_build_source_lookup_hint(envelope),
        responsibility_lookup_hint=_build_responsibility_lookup_hint(envelope),
        followup_needed="yes",
        raw_event_file=_relative_path(project_root, raw_event_record_path),
    )


def _build_frontmatter(record: PerceivedEventRecord) -> dict[str, str]:
    """构造感知记录 frontmatter。"""
    return {
        "schema": "dutyflow.perceived_event.v1",
        "id": record.record_id,
        "source_event_id": record.source_event_id,
        "message_id": record.message_id,
        "received_at": record.received_at,
        "event_type": record.event_type,
        "trigger_kind": record.trigger_kind,
        "chat_type": record.chat_type,
        "chat_id": record.chat_id,
        "sender_open_id": record.sender_open_id,
        "message_type": record.message_type,
        "mentions_bot": _bool_text(record.mentions_bot),
        "has_attachment": _bool_text(record.has_attachment),
        "attachment_kinds": ",".join(record.attachment_kinds),
        "raw_event_file": record.raw_event_file,
        "status": "perceived",
        "updated_at": record.received_at,
    }


def _build_body(record: PerceivedEventRecord) -> str:
    """渲染感知记录正文。"""
    return (
        f"# Perceived Event {record.record_id}\n\n"
        "## Summary\n\n"
        f"{_build_summary(record)}\n\n"
        "## Extracted Text\n\n"
        f"{_render_key_value_item('raw_text', record.raw_text)}\n"
        f"{_render_key_value_item('content_preview', record.content_preview)}\n"
        f"{_render_key_value_item('mention_text', record.mention_text)}\n\n"
        "## Entities\n\n"
        f"{_render_entities_table(record.entities)}\n\n"
        "## Parse Targets\n\n"
        f"{_render_targets_table(record.parse_targets)}\n\n"
        "## Lookup Hints\n\n"
        f"- contact_lookup_hint: {record.contact_lookup_hint}\n"
        f"- source_lookup_hint: {record.source_lookup_hint}\n"
        f"- responsibility_lookup_hint: {record.responsibility_lookup_hint}\n"
        f"- followup_needed: {record.followup_needed}\n\n"
        "## Raw Reference\n\n"
        f"- event_record: {record.raw_event_file}\n"
    )


def _build_summary(record: PerceivedEventRecord) -> str:
    """生成感知记录的人类可读摘要。"""
    channel = "用户私聊 Bot" if record.chat_type == "p2p" else "群聊 @Bot"
    if record.has_attachment:
        detail = "发送了一条带附件线索的消息"
    elif record.raw_text:
        detail = "发送了一条文本消息"
    else:
        detail = "触发了一条可感知消息"
    return f"{channel} {detail}。"


def _build_entities(envelope: FeishuEventEnvelope) -> tuple[PerceptionEntity, ...]:
    """从事件包裹对象中抽取稳定实体。"""
    entities = [
        PerceptionEntity(kind="sender", value=envelope.sender_open_id, source="sender_open_id"),
        PerceptionEntity(kind="chat", value=envelope.chat_id, source="chat_id"),
    ]
    for open_id in envelope.mentioned_open_ids:
        entities.append(PerceptionEntity(kind="mention", value=open_id, source="mentions"))
    return tuple(item for item in entities if item.value)


def _build_parse_targets(
    envelope: FeishuEventEnvelope,
    content_payload: Mapping[str, Any],
) -> tuple[PerceptionParseTarget, ...]:
    """从消息内容中提取后续可解析的资源目标。"""
    targets: list[PerceptionParseTarget] = []
    file_key = _text(content_payload.get("file_key"))
    file_name = _text(content_payload.get("file_name"))
    image_key = _text(content_payload.get("image_key"))
    image_name = _text(content_payload.get("image_name"))
    if file_key or envelope.message_type == "file":
        targets.append(_file_target(envelope.message_id, file_key, file_name))
    if image_key or envelope.message_type == "image":
        targets.append(_image_target(envelope.message_id, image_key, image_name))
    targets.extend(_link_targets(envelope.message_id, envelope.message_text, content_payload))
    return tuple(targets)


def _file_target(message_id: str, file_key: str, file_name: str) -> PerceptionParseTarget:
    """构造文件类型解析目标。"""
    return PerceptionParseTarget(
        target_id=f"{message_id}:file",
        target_type="file",
        file_key=file_key,
        file_name=file_name,
        url="",
        required_tool="fetch_feishu_message_resource",
    )


def _image_target(message_id: str, image_key: str, image_name: str) -> PerceptionParseTarget:
    """构造图片类型解析目标。"""
    return PerceptionParseTarget(
        target_id=f"{message_id}:image",
        target_type="image",
        file_key=image_key,
        file_name=image_name,
        url="",
        required_tool="fetch_feishu_message_resource",
    )


def _link_targets(
    message_id: str,
    message_text: str,
    content_payload: Mapping[str, Any],
) -> list[PerceptionParseTarget]:
    """从文本或消息结构中提取网页/文档链接。"""
    urls = _collect_urls(message_text, content_payload)
    targets: list[PerceptionParseTarget] = []
    for index, url in enumerate(urls, start=1):
        targets.append(
            PerceptionParseTarget(
                target_id=f"{message_id}:link:{index}",
                target_type="link",
                file_key="",
                file_name="",
                url=url,
                required_tool="parse_web_link",
            )
        )
    return targets


def _collect_urls(message_text: str, content_payload: Mapping[str, Any]) -> tuple[str, ...]:
    """汇总文本和结构化字段中的稳定 URL。"""
    urls: list[str] = []
    for key in ("url", "href"):
        value = _text(content_payload.get(key))
        if value:
            urls.append(value)
    urls.extend(re.findall(r"https?://[^\s]+", message_text))
    return tuple(dict.fromkeys(urls))


def _collect_attachment_kinds(
    envelope: FeishuEventEnvelope,
    parse_targets: tuple[PerceptionParseTarget, ...],
) -> tuple[str, ...]:
    """根据消息类型和解析目标汇总附件种类。"""
    kinds = [target.target_type for target in parse_targets]
    if envelope.message_type == "post" and "link" not in kinds:
        kinds.append("link")
    return tuple(dict.fromkeys(kind for kind in kinds if kind))


def _build_trigger_kind(
    envelope: FeishuEventEnvelope,
    attachment_kinds: tuple[str, ...],
) -> str:
    """生成感知层稳定 trigger_kind。"""
    prefix = "p2p" if envelope.chat_type == "p2p" else "group_at_bot"
    if "file" in attachment_kinds:
        return f"{prefix}_file"
    if "image" in attachment_kinds:
        return f"{prefix}_image"
    if "link" in attachment_kinds:
        return f"{prefix}_link"
    return f"{prefix}_text"


def _build_source_lookup_hint(envelope: FeishuEventEnvelope) -> str:
    """构造后续来源查询工具的稳定提示。"""
    return f"source_id={envelope.chat_id},source_type=chat,chat_type={envelope.chat_type}"


def _build_responsibility_lookup_hint(envelope: FeishuEventEnvelope) -> str:
    """构造后续责任查询工具的最小提示。"""
    return f"sender_open_id={envelope.sender_open_id},chat_type={envelope.chat_type}"


def _build_record_path(project_root: Path, received_at: str, record_id: str) -> Path:
    """按日期目录分片构造感知记录落盘路径。"""
    date_part = received_at[:10]
    return project_root / "data" / "perception" / date_part / f"{record_id}.md"


def _parse_message_content(raw_event: Mapping[str, Any]) -> dict[str, Any]:
    """从飞书原始事件中解析 message.content。"""
    event = _mapping(raw_event.get("event"))
    message = _mapping(event.get("message"))
    raw_content = message.get("content")
    if isinstance(raw_content, Mapping):
        return dict(raw_content)
    content_text = _text(raw_content)
    if not content_text:
        return {}
    try:
        parsed = json.loads(content_text)
    except json.JSONDecodeError:
        return {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def _render_entities_table(entities: tuple[PerceptionEntity, ...]) -> str:
    """渲染实体表格。"""
    header = "| kind | value | source |\n|---|---|---|"
    rows = [f"| {item.kind} | {item.value} | {item.source} |" for item in entities]
    return "\n".join([header, *rows]) if rows else header


def _render_targets_table(targets: tuple[PerceptionParseTarget, ...]) -> str:
    """渲染解析目标表格。"""
    header = (
        "| target_id | target_type | file_key | file_name | url | required_tool |\n"
        "|---|---|---|---|---|---|"
    )
    rows = [
        "| {target_id} | {target_type} | {file_key} | {file_name} | {url} | {required_tool} |".format(
            target_id=item.target_id,
            target_type=item.target_type,
            file_key=item.file_key,
            file_name=item.file_name,
            url=item.url,
            required_tool=item.required_tool,
        )
        for item in targets
    ]
    return "\n".join([header, *rows]) if rows else header


def _render_key_value_item(key: str, value: str) -> str:
    """把稳定键值渲染为可回读的 Markdown 片段。"""
    if "\n" not in value:
        return f"- {key}: {value}"
    return f"- {key}:\n```text\n{value}\n```"


def _parse_entities(section_text: str) -> tuple[PerceptionEntity, ...]:
    """从实体表格 section 读取实体数组。"""
    rows = _parse_table(section_text)
    return tuple(
        PerceptionEntity(
            kind=row.get("kind", ""),
            value=row.get("value", ""),
            source=row.get("source", ""),
        )
        for row in rows
        if row.get("value", "")
    )


def _parse_targets(section_text: str) -> tuple[PerceptionParseTarget, ...]:
    """从解析目标表格 section 读取解析目标数组。"""
    rows = _parse_table(section_text)
    return tuple(
        PerceptionParseTarget(
            target_id=row.get("target_id", ""),
            target_type=row.get("target_type", ""),
            file_key=row.get("file_key", ""),
            file_name=row.get("file_name", ""),
            url=row.get("url", ""),
            required_tool=row.get("required_tool", ""),
        )
        for row in rows
        if row.get("target_id", "")
    )


def _parse_key_value_section(section_text: str) -> dict[str, str]:
    """解析 `- key: value` 形式的稳定 section。"""
    payload: dict[str, str] = {}
    lines = section_text.splitlines()
    index = 0
    while index < len(lines):
        text = lines[index].strip()
        if not text.startswith("- ") or ":" not in text:
            index += 1
            continue
        key, value = text[2:].split(":", 1)
        key = key.strip()
        value = value.lstrip()
        if value:
            payload[key] = value
            index += 1
            continue
        index += 1
        payload[key], index = _read_multiline_value(lines, index)
    return payload


def _read_multiline_value(lines: list[str], start_index: int) -> tuple[str, int]:
    """读取 `- key:` 后面的多行值，支持 fenced block。"""
    if start_index >= len(lines):
        return "", start_index
    if lines[start_index].strip().startswith("```"):
        return _read_fenced_multiline_value(lines, start_index + 1)
    return _read_plain_multiline_value(lines, start_index)


def _read_fenced_multiline_value(lines: list[str], start_index: int) -> tuple[str, int]:
    """读取 fenced code block 中的多行文本。"""
    collected: list[str] = []
    index = start_index
    while index < len(lines):
        if lines[index].strip().startswith("```"):
            return "\n".join(collected).strip("\n"), index + 1
        collected.append(lines[index])
        index += 1
    return "\n".join(collected).strip("\n"), index


def _read_plain_multiline_value(lines: list[str], start_index: int) -> tuple[str, int]:
    """读取直到下一条 `- key:` 为止的普通多行值。"""
    collected: list[str] = []
    index = start_index
    while index < len(lines):
        text = lines[index].strip()
        if text.startswith("- ") and ":" in text:
            break
        collected.append(lines[index])
        index += 1
    return "\n".join(collected).strip("\n").strip(), index


def _parse_table(section_text: str) -> tuple[dict[str, str], ...]:
    """解析单张 Markdown 表格。"""
    lines = [line.strip() for line in section_text.splitlines() if line.strip().startswith("|")]
    if len(lines) < 2:
        return ()
    headers = [cell.strip() for cell in lines[0].strip("|").split("|")]
    rows: list[dict[str, str]] = []
    for line in lines[2:]:
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) != len(headers):
            continue
        rows.append(dict(zip(headers, cells, strict=True)))
    return tuple(rows)


def _mapping(value: object) -> dict[str, Any]:
    """把不确定对象安全转换为字典。"""
    return dict(value) if isinstance(value, Mapping) else {}


def _entity_to_dict(entity: PerceptionEntity) -> dict[str, str]:
    """把实体对象转换为稳定字典。"""
    return {"kind": entity.kind, "value": entity.value, "source": entity.source}


def _parse_target_to_dict(target: PerceptionParseTarget) -> dict[str, str]:
    """把解析目标对象转换为稳定字典。"""
    return {
        "target_id": target.target_id,
        "target_type": target.target_type,
        "file_key": target.file_key,
        "file_name": target.file_name,
        "url": target.url,
        "required_tool": target.required_tool,
    }


def _relative_path(project_root: Path, path: Path) -> str:
    """返回相对项目根目录的稳定路径字符串。"""
    return path.resolve().relative_to(project_root).as_posix()


def _sanitize_suffix(value: str) -> str:
    """把外部 ID 转为适合文件名的稳定后缀。"""
    cleaned = [char if char.isalnum() else "_" for char in value]
    result = "".join(cleaned).strip("_")
    return result or "unknown"


def _split_csv(value: str) -> tuple[str, ...]:
    """把逗号字符串转换为稳定元组。"""
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _bool_text(value: bool) -> str:
    """把布尔值转换为 frontmatter 可用文本。"""
    return "true" if value else "false"


def _as_bool(value: str) -> bool:
    """把 frontmatter 中的布尔文本恢复为布尔值。"""
    return value.strip().lower() == "true"


def _text(value: object) -> str:
    """把简单值安全转换为字符串。"""
    if value is None:
        return ""
    return str(value).strip()


def _self_test() -> None:
    """验证感知记录可写入并可反向构造成 loop 输入。"""
    from tempfile import TemporaryDirectory

    from dutyflow.feishu.events import FeishuEventAdapter

    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        service = PerceptionRecordService(root)
        adapter = FeishuEventAdapter()
        envelope = adapter.build_event_envelope(adapter.create_local_fixture_event("hello"))
        raw_event_path = root / "data" / "events" / "evt_fixture.md"
        service.markdown_store.file_store.ensure_dir(raw_event_path.parent)
        service.markdown_store.file_store.write_text(raw_event_path, "fixture")
        record = service.create_record(envelope, raw_event_path)
        loop_input = service.build_loop_input(message_id=envelope.message_id)
        assert record.trigger_kind == "p2p_text"
        assert loop_input is not None
        assert loop_input["sender_open_id"] == "ou_fixture_sender"


if __name__ == "__main__":
    _self_test()
    print("dutyflow perception store self-test passed")
