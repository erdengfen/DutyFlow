# 本文件负责从来源索引中解析来源上下文和责任提示。

from __future__ import annotations

import json
from pathlib import Path
import sys

SRC_ROOT = Path(__file__).resolve().parents[2]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dutyflow.storage.structured_markdown import FrontmatterParser


class SourceContextResolver:
    """按稳定字段解析来源上下文，并返回裁剪后的来源片段。"""

    def __init__(self, root: Path) -> None:
        """绑定工作区根目录和来源索引路径。"""
        self.root = Path(root).resolve()
        self.parser = FrontmatterParser(self.root)
        self.index_path = self.root / "data/identity/sources/index.md"

    def resolve_source(self, tool_input: dict[str, object]) -> dict[str, object]:
        """按 source_id、feishu_id 或显示名匹配来源。"""
        rows = self._load_index_rows()
        if not rows:
            return _not_found_payload()
        exact = self._match_exact_rows(rows, tool_input)
        if exact is not None:
            return self._build_match_payload(exact)
        scoped = self._match_scoped_rows(rows, tool_input)
        if scoped is not None:
            return self._build_match_payload(scoped)
        weak = self._match_weak_rows(rows, tool_input)
        if weak is not None:
            return self._build_match_payload(weak)
        return _not_found_payload()

    def get_source_record(self, source_id: str) -> dict[str, str] | None:
        """按 source_id 返回来源索引行，供责任判断复用。"""
        if not source_id:
            return None
        rows = self._load_index_rows()
        matched = [row for row in rows if row.get("source_id", "") == source_id]
        if len(matched) != 1:
            return None
        return dict(matched[0])

    def resolve_source_json(self, tool_input: dict[str, object]) -> str:
        """返回 JSON 字符串结果。"""
        return json.dumps(self.resolve_source(tool_input), ensure_ascii=False)

    def _load_index_rows(self) -> tuple[dict[str, str], ...]:
        """读取来源索引表。"""
        if not self.index_path.exists():
            return ()
        return self.parser.read_index_rows(self.index_path)

    def _match_exact_rows(
        self,
        rows: tuple[dict[str, str], ...],
        tool_input: dict[str, object],
    ) -> dict[str, object] | None:
        """按 source_id 和飞书侧 ID 做高置信匹配。"""
        fields = (
            ("source_id", "source_id", "high"),
            ("feishu_id", "feishu_id", "high"),
        )
        for input_key, row_key, confidence in fields:
            value = _text(tool_input, input_key)
            if not value:
                continue
            matched = [row for row in rows if row.get(row_key, "") == value]
            if matched:
                return _match_result(matched, input_key, confidence)
        return None

    def _match_scoped_rows(
        self,
        rows: tuple[dict[str, str], ...],
        tool_input: dict[str, object],
    ) -> dict[str, object] | None:
        """按来源类型和显示名组合做中置信匹配。"""
        source_type = _text(tool_input, "source_type")
        display_name = _text(tool_input, "display_name")
        if not source_type or not display_name:
            return None
        matched = [row for row in rows if _matches_text(row, "source_type", source_type) and _matches_text(row, "display_name", display_name)]
        if not matched:
            return None
        return _match_result(matched, "source_type+display_name", "medium")

    def _match_weak_rows(
        self,
        rows: tuple[dict[str, str], ...],
        tool_input: dict[str, object],
    ) -> dict[str, object] | None:
        """按显示名单独匹配，并强制视为歧义入口。"""
        display_name = _text(tool_input, "display_name")
        if not display_name:
            return None
        matched = [row for row in rows if _matches_text(row, "display_name", display_name)]
        if not matched:
            return None
        return _match_result(matched, "display_name", "low", force_ambiguous=True)

    def _build_match_payload(self, match: dict[str, object]) -> dict[str, object]:
        """把来源命中结果转换成工具输出结构。"""
        if match["match_status"] != "unique":
            return {
                "match_status": match["match_status"],
                "source_id": "",
                "source_type": "",
                "owner_contact_id": "",
                "default_weight": "",
                "context_snippet": "",
                "source_file": "",
            }
        row = match["rows"][0]
        return {
            "match_status": "unique",
            "source_id": row.get("source_id", ""),
            "source_type": row.get("source_type", ""),
            "owner_contact_id": row.get("owner_contact_id", ""),
            "default_weight": row.get("default_weight", ""),
            "context_snippet": _build_source_snippet(row),
            "source_file": _relative_path(self.root, self.index_path),
        }


def _match_result(
    rows: list[dict[str, str]],
    matched_by: str,
    confidence: str,
    force_ambiguous: bool = False,
) -> dict[str, object]:
    """生成统一匹配结果。"""
    return {
        "match_status": "unique" if len(rows) == 1 and not force_ambiguous else "ambiguous",
        "matched_by": matched_by,
        "confidence": confidence,
        "rows": tuple(rows),
    }


def _build_source_snippet(row: dict[str, str]) -> str:
    """把来源索引行裁剪成短上下文。"""
    parts = [
        row.get("display_name", ""),
        _line("source_type", row.get("source_type", "")),
        _line("owner_contact_id", row.get("owner_contact_id", "")),
        _line("default_weight", row.get("default_weight", "")),
        row.get("notes", ""),
    ]
    return " | ".join(part for part in parts if part.strip())


def _matches_text(row: dict[str, str], key: str, expected: str) -> bool:
    """判断某个索引字段是否精确命中。"""
    return row.get(key, "").strip().casefold() == expected.strip().casefold()


def _relative_path(root: Path, path: Path) -> str:
    """返回工作区内稳定相对路径。"""
    return str(path.resolve().relative_to(root.resolve()))


def _line(key: str, value: str) -> str:
    """按 key=value 输出简短字段。"""
    return f"{key}={value}" if value else ""


def _text(tool_input: dict[str, object], key: str) -> str:
    """把输入字段安全转换成字符串。"""
    value = tool_input.get(key, "")
    return value.strip() if isinstance(value, str) else ""


def _not_found_payload() -> dict[str, str]:
    """返回统一未命中结果。"""
    return {
        "match_status": "not_found",
        "source_id": "",
        "source_type": "",
        "owner_contact_id": "",
        "default_weight": "",
        "context_snippet": "",
        "source_file": "",
    }


def _self_test() -> None:
    """验证按空工作区解析来源时返回 not_found。"""
    import tempfile

    with tempfile.TemporaryDirectory() as temp_dir:
        payload = SourceContextResolver(Path(temp_dir)).resolve_source({"source_id": "source_chat_001"})
        assert payload["match_status"] == "not_found"


if __name__ == "__main__":
    _self_test()
    print("dutyflow source context resolver self-test passed")
