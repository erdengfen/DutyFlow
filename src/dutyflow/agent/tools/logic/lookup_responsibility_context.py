# 本文件实现 lookup_responsibility_context 工具的只读查询逻辑。

from __future__ import annotations

import json
from pathlib import Path
import sys

SRC_ROOT = Path(__file__).resolve().parents[4]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dutyflow.agent.tools.contracts.lookup_responsibility_context_contract import (
    LOOKUP_RESPONSIBILITY_CONTEXT_TOOL_CONTRACT,
)
from dutyflow.agent.tools.types import ToolCall, ToolResultEnvelope, error_envelope
from dutyflow.identity.contact_resolver import ContactResolver
from dutyflow.identity.source_context import SourceContextResolver


class LookupResponsibilityContextTool:
    """查询联系人与来源共同形成的责任上下文。"""

    name = "lookup_responsibility_context"
    contract = LOOKUP_RESPONSIBILITY_CONTEXT_TOOL_CONTRACT
    is_concurrency_safe = True
    requires_approval = False
    timeout_seconds = 30.0
    max_retries = 0
    retry_policy = "none"
    idempotency = "read_only"
    degradation_mode = "none"
    fallback_tool_names = ()

    def handle(self, tool_call: ToolCall, tool_use_context) -> ToolResultEnvelope:
        """结合联系人详情和来源索引返回责任裁剪片段。"""
        tool_input = dict(tool_call.tool_input)
        if not _text(tool_input, "contact_id"):
            return error_envelope(tool_call, "invalid_responsibility_lookup_input", "contact_id is required")
        payload = _resolve_responsibility_payload(Path(tool_use_context.cwd), tool_input)
        return ToolResultEnvelope(
            tool_call.tool_use_id,
            tool_call.tool_name,
            True,
            json.dumps(payload, ensure_ascii=False),
        )


def _resolve_responsibility_payload(root: Path, tool_input: dict[str, object]) -> dict[str, object]:
    """根据联系人、来源和事项类型组装责任上下文。"""
    contact_resolver = ContactResolver(root)
    source_resolver = SourceContextResolver(root)
    contact = contact_resolver.get_contact_record(_text(tool_input, "contact_id"))
    source = source_resolver.get_source_record(_text(tool_input, "source_id"))
    if contact is None:
        return _empty_payload()
    scopes = _matched_scopes(contact, _text(tool_input, "matter_type"))
    attention = _requires_attention(contact, source, scopes)
    files = [contact["source_file"]]
    if source is not None:
        files.append(str(Path("data/identity/sources/index.md")))
    return {
        "responsibility_found": bool(scopes),
        "responsibility_scope": scopes,
        "relationship_to_user": contact["detail"].frontmatter.get("relationship_to_user", ""),
        "requires_user_attention": attention,
        "context_snippet": _build_responsibility_snippet(contact, source, scopes, attention),
        "source_files": files,
    }


def _matched_scopes(contact: dict[str, object], matter_type: str) -> list[str]:
    """按事项类型过滤联系人责任范围。"""
    detail = contact["detail"]
    rows = _parse_table(detail.sections.get("Responsibility Context", ""))
    if not matter_type:
        return _scopes_from_rows_or_frontmatter(rows, detail.frontmatter.get("responsibility_scope", ""))
    matched = [row.get("scope", "") for row in rows if matter_type.casefold() in row.get("scope", "").casefold() or matter_type.casefold() in row.get("description", "").casefold()]
    if matched:
        return [scope for scope in matched if scope]
    return _csv_tokens(detail.frontmatter.get("responsibility_scope", ""))


def _requires_attention(
    contact: dict[str, object],
    source: dict[str, str] | None,
    scopes: list[str],
) -> bool:
    """根据关系、来源和责任范围判断是否值得提醒用户。"""
    relationship = contact["detail"].frontmatter.get("relationship_to_user", "")
    default_weight = ""
    if source is not None:
        default_weight = source.get("default_weight", "")
    if relationship in {"manager", "direct_report"}:
        return True
    if default_weight in {"high", "critical"}:
        return True
    return bool(scopes and source is not None and source.get("owner_contact_id", "") == contact["detail"].frontmatter.get("id", ""))


def _build_responsibility_snippet(
    contact: dict[str, object],
    source: dict[str, str] | None,
    scopes: list[str],
    requires_user_attention: bool,
) -> str:
    """把责任判断结果裁剪成简短片段。"""
    detail = contact["detail"]
    parts = [
        detail.frontmatter.get("display_name", ""),
        f"relationship_to_user={detail.frontmatter.get('relationship_to_user', '')}",
        f"responsibility_scope={','.join(scopes)}" if scopes else "",
        f"requires_user_attention={str(requires_user_attention).lower()}",
        detail.sections.get("Relationship To User", ""),
    ]
    if source is not None:
        parts.append(f"source={source.get('display_name', '')}")
        parts.append(f"default_weight={source.get('default_weight', '')}")
        parts.append(source.get("notes", ""))
    return " | ".join(part for part in parts if part.strip())


def _parse_table(body: str) -> tuple[dict[str, str], ...]:
    """解析 Responsibility Context section 中的首张表。"""
    lines = [line.strip() for line in body.splitlines()]
    start = _find_table_start(lines)
    if start == -1:
        return ()
    headers = _split_table_row(lines[start])
    rows: list[dict[str, str]] = []
    index = start + 2
    while index < len(lines) and lines[index].startswith("|"):
        values = _split_table_row(lines[index])
        if len(headers) == len(values):
            rows.append(dict(zip(headers, values, strict=True)))
        index += 1
    return tuple(rows)


def _find_table_start(lines: list[str]) -> int:
    """找到 Markdown 表头起始行。"""
    for index in range(len(lines) - 1):
        if lines[index].startswith("|") and lines[index + 1].startswith("|---"):
            return index
    return -1


def _split_table_row(line: str) -> list[str]:
    """把一行 Markdown 表拆成列值。"""
    return [cell.strip() for cell in line.strip("|").split("|")]


def _scopes_from_rows_or_frontmatter(rows: tuple[dict[str, str], ...], frontmatter_value: str) -> list[str]:
    """优先从责任表读取 scope，否则回退到 frontmatter。"""
    scopes = [row.get("scope", "") for row in rows if row.get("scope", "")]
    return scopes or _csv_tokens(frontmatter_value)


def _csv_tokens(value: str) -> list[str]:
    """把英文逗号分隔字段转换成列表。"""
    return [item.strip() for item in value.split(",") if item.strip()]


def _empty_payload() -> dict[str, object]:
    """返回未命中责任记录时的统一结果。"""
    return {
        "responsibility_found": False,
        "responsibility_scope": [],
        "relationship_to_user": "",
        "requires_user_attention": False,
        "context_snippet": "",
        "source_files": [],
    }


def _text(tool_input: dict[str, object], key: str) -> str:
    """安全提取字符串输入。"""
    value = tool_input.get(key, "")
    return value.strip() if isinstance(value, str) else ""


def _self_test() -> None:
    """验证空工作区下责任上下文返回未命中。"""
    import tempfile

    with tempfile.TemporaryDirectory() as temp_dir:
        payload = _resolve_responsibility_payload(Path(temp_dir), {"contact_id": "contact_001"})
        assert payload["responsibility_found"] is False


if __name__ == "__main__":
    _self_test()
    print("dutyflow lookup_responsibility_context logic self-test passed")
