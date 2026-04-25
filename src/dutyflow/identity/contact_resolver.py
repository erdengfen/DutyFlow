# 本文件负责从联系人索引和单人详情文件中解析联系人身份。

from __future__ import annotations

import json
from pathlib import Path
import sys

SRC_ROOT = Path(__file__).resolve().parents[2]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dutyflow.storage.structured_markdown import FrontmatterParser, StructuredRecord


class ContactResolver:
    """按稳定字段解析联系人身份，并返回裁剪后的身份片段。"""

    def __init__(self, root: Path) -> None:
        """绑定工作区根目录和 Markdown 解析器。"""
        self.root = Path(root).resolve()
        self.parser = FrontmatterParser(self.root)
        self.index_path = self.root / "data/identity/contacts/index.md"

    def resolve_contact(self, tool_input: dict[str, object]) -> dict[str, object]:
        """按优先级匹配联系人，并返回唯一、歧义或未命中结果。"""
        rows = self._load_index_rows()
        if not rows:
            return _not_found_payload()
        exact = self._match_exact_rows(rows, tool_input)
        if exact is not None:
            return self._build_match_payload(exact)
        scoped = self._match_department_rows(rows, tool_input)
        if scoped is not None:
            return self._build_match_payload(scoped)
        weak = self._match_weak_rows(rows, tool_input)
        if weak is not None:
            return self._build_match_payload(weak)
        return _not_found_payload()

    def get_contact_record(self, contact_id: str) -> dict[str, object] | None:
        """按 contact_id 读取联系人详情，供责任判断复用。"""
        if not contact_id:
            return None
        rows = self._load_index_rows()
        candidates = [row for row in rows if row.get("contact_id", "") == contact_id]
        if len(candidates) != 1:
            return None
        detail = self._load_detail_record(candidates[0])
        if detail is None:
            return None
        return {
            "row": candidates[0],
            "detail": detail,
            "source_file": _relative_path(self.root, detail.path),
        }

    def resolve_contact_json(self, tool_input: dict[str, object]) -> str:
        """返回 JSON 字符串，供工具层直接输出。"""
        return json.dumps(self.resolve_contact(tool_input), ensure_ascii=False)

    def _load_index_rows(self) -> tuple[dict[str, str], ...]:
        """读取联系人索引表。"""
        if not self.index_path.exists():
            return ()
        return self.parser.read_index_rows(self.index_path)

    def _match_exact_rows(
        self,
        rows: tuple[dict[str, str], ...],
        tool_input: dict[str, object],
    ) -> dict[str, object] | None:
        """按 contact_id 和飞书稳定 ID 做高置信匹配。"""
        fields = (
            ("contact_id", "contact_id", "high"),
            ("feishu_user_id", "feishu_user_id", "high"),
            ("feishu_open_id", "feishu_open_id", "high"),
        )
        for input_key, row_key, confidence in fields:
            value = _text(tool_input, input_key)
            if not value:
                continue
            matched = [row for row in rows if row.get(row_key, "") == value]
            if matched:
                return _match_result(matched, input_key, confidence)
        return None

    def _match_department_rows(
        self,
        rows: tuple[dict[str, str], ...],
        tool_input: dict[str, object],
    ) -> dict[str, object] | None:
        """按姓名或别名结合部门做中置信匹配。"""
        department = _text(tool_input, "department")
        if not department:
            return None
        name = _text(tool_input, "name")
        alias = _text(tool_input, "alias")
        if name:
            matched = [row for row in rows if _matches_display_name(row, name) and _matches_department(row, department)]
            if matched:
                return _match_result(matched, "name+department", "medium")
        if alias:
            matched = [row for row in rows if _matches_alias(row, alias) and _matches_department(row, department)]
            if matched:
                return _match_result(matched, "alias+department", "medium")
        return None

    def _match_weak_rows(
        self,
        rows: tuple[dict[str, str], ...],
        tool_input: dict[str, object],
    ) -> dict[str, object] | None:
        """按姓名或别名单独匹配，并强制按歧义处理。"""
        name = _text(tool_input, "name")
        alias = _text(tool_input, "alias")
        if name:
            matched = [row for row in rows if _matches_display_name(row, name) or _matches_alias(row, name)]
            if matched:
                return _match_result(matched, "name", "low", force_ambiguous=True)
        if alias:
            matched = [row for row in rows if _matches_alias(row, alias)]
            if matched:
                return _match_result(matched, "alias", "low", force_ambiguous=True)
        return None

    def _build_match_payload(self, match: dict[str, object]) -> dict[str, object]:
        """把索引命中结果转换成工具输出结构。"""
        if match["match_status"] != "unique":
            return {
                "match_status": match["match_status"],
                "contact_id": "",
                "confidence": match["confidence"],
                "matched_by": match["matched_by"],
                "source_file": "",
                "context_snippet": "",
                "ambiguous_candidates": [_candidate_summary(self.root, row) for row in match["rows"]],
            }
        row = match["rows"][0]
        detail = self._load_detail_record(row)
        if detail is None:
            return _not_found_payload()
        return {
            "match_status": "unique",
            "contact_id": row.get("contact_id", ""),
            "confidence": match["confidence"],
            "matched_by": match["matched_by"],
            "source_file": _relative_path(self.root, detail.path),
            "context_snippet": _build_contact_snippet(detail),
            "ambiguous_candidates": [],
        }

    def _load_detail_record(self, row: dict[str, str]) -> StructuredRecord | None:
        """根据索引中的 detail_file 读取单人详情文档。"""
        detail_file = row.get("detail_file", "").strip()
        if not detail_file:
            return None
        path = (self.index_path.parent / detail_file).resolve()
        if not path.exists() or not path.is_relative_to(self.root):
            return None
        record = self.parser.parse(path)
        if record.frontmatter.get("schema") != "dutyflow.contact_detail.v1":
            return None
        return record


def _match_result(
    rows: list[dict[str, str]],
    matched_by: str,
    confidence: str,
    force_ambiguous: bool = False,
) -> dict[str, object]:
    """把原始命中候选转换成统一匹配结果。"""
    unique = len(rows) == 1 and not force_ambiguous
    return {
        "match_status": "unique" if unique else "ambiguous",
        "matched_by": matched_by,
        "confidence": confidence,
        "rows": tuple(rows),
    }


def _build_contact_snippet(record: StructuredRecord) -> str:
    """从联系人详情中裁剪身份判断需要的短片段。"""
    parts = [
        record.frontmatter.get("display_name", ""),
        _line("relationship_to_user", record.frontmatter.get("relationship_to_user", "")),
        _line("department", record.frontmatter.get("department", "")),
        _line("role_title", record.frontmatter.get("role_title", "")),
        record.sections.get("Identity Summary", ""),
        record.sections.get("Relationship To User", ""),
        record.sections.get("Decision Snippets", ""),
    ]
    return " | ".join(part for part in parts if part.strip())


def _candidate_summary(root: Path, row: dict[str, str]) -> dict[str, str]:
    """把歧义候选裁剪成轻量列表项。"""
    detail_file = row.get("detail_file", "").strip()
    source_file = ""
    if detail_file:
        source_file = _relative_path(root, (root / "data/identity/contacts" / detail_file).resolve())
    return {
        "contact_id": row.get("contact_id", ""),
        "display_name": row.get("display_name", ""),
        "department": row.get("department", ""),
        "org_level": row.get("org_level", ""),
        "source_file": source_file,
    }


def _matches_display_name(row: dict[str, str], expected: str) -> bool:
    """判断 display_name 是否精确命中。"""
    return row.get("display_name", "").strip().casefold() == expected.strip().casefold()


def _matches_alias(row: dict[str, str], expected: str) -> bool:
    """判断 aliases 是否包含指定别名。"""
    expected_token = expected.strip().casefold()
    aliases = [item.strip().casefold() for item in row.get("aliases", "").split(",") if item.strip()]
    return expected_token in aliases


def _matches_department(row: dict[str, str], department: str) -> bool:
    """判断部门是否精确命中。"""
    return row.get("department", "").strip().casefold() == department.strip().casefold()


def _relative_path(root: Path, path: Path) -> str:
    """返回工作区内的稳定相对路径。"""
    return str(path.resolve().relative_to(root.resolve()))


def _line(key: str, value: str) -> str:
    """按 key=value 形式输出简短字段。"""
    return f"{key}={value}" if value else ""


def _text(tool_input: dict[str, object], key: str) -> str:
    """把输入字段安全转换成字符串。"""
    value = tool_input.get(key, "")
    return value.strip() if isinstance(value, str) else ""


def _not_found_payload() -> dict[str, object]:
    """返回统一未命中结果。"""
    return {
        "match_status": "not_found",
        "contact_id": "",
        "confidence": "low",
        "matched_by": "",
        "source_file": "",
        "context_snippet": "",
        "ambiguous_candidates": [],
    }


def _self_test() -> None:
    """验证按空工作区解析联系人时返回 not_found。"""
    import tempfile

    with tempfile.TemporaryDirectory() as temp_dir:
        payload = ContactResolver(Path(temp_dir)).resolve_contact({"contact_id": "contact_001"})
        assert payload["match_status"] == "not_found"


if __name__ == "__main__":
    _self_test()
    print("dutyflow contact resolver self-test passed")
