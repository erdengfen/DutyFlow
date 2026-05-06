# 本文件负责飞书用户面 collector 的最小续跑状态落盘。

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from dutyflow.storage.file_store import FileStore
from dutyflow.storage.markdown_store import MarkdownDocument, MarkdownStore

_SYNC_STATE_SCHEMA = "dutyflow.feishu_collector_sync_state.v1"
# 关键开关：collector/scope 文件名片段最多保留 80 个字符，避免异常长 ID 生成难以查看的路径。
_MAX_SAFE_NAME_CHARS = 80


@dataclass(frozen=True)
class FeishuCollectorSyncState:
    """表示单个 collector 在一个同步范围内的最小续跑状态。"""

    collector_name: str
    surface_type: str
    scope_id: str
    cursor: str = ""
    last_success_at: str = ""
    last_failure_at: str = ""
    last_error_kind: str = ""
    last_error_detail: str = ""
    next_cursor: str = ""
    updated_at: str = ""

    def __post_init__(self) -> None:
        """校验状态归属字段，避免写入无法定位的同步记录。"""
        if not self.collector_name:
            raise ValueError("FeishuCollectorSyncState.collector_name is required")
        if not self.scope_id:
            raise ValueError("FeishuCollectorSyncState.scope_id is required")


class FeishuSyncStateStore:
    """用 Markdown 保存飞书用户面 collector 的最小续跑状态。"""

    def __init__(self, project_root: Path) -> None:
        """绑定项目根目录和工作区内 Markdown 存储。"""
        self.project_root = Path(project_root).resolve()
        self.markdown_store = MarkdownStore(FileStore(self.project_root))

    def read(
        self,
        collector_name: str,
        scope_id: str,
        surface_type: str = "",
    ) -> FeishuCollectorSyncState:
        """读取指定 collector/scope 的状态，不存在时返回空 cursor 初始状态。"""
        path = self.path_for(collector_name, scope_id)
        if not self.markdown_store.exists(path):
            return _initial_state(collector_name, scope_id, surface_type)
        document = self.markdown_store.read_document(path)
        return _state_from_frontmatter(
            document.frontmatter,
            collector_name,
            scope_id,
            surface_type,
        )

    def mark_success(
        self,
        collector_name: str,
        scope_id: str,
        cursor: str,
        next_cursor: str = "",
        surface_type: str = "",
    ) -> FeishuCollectorSyncState:
        """记录同步成功位置，并保留最近一次失败信息供排查。"""
        now = _now_iso()
        current = self.read(collector_name, scope_id, surface_type)
        state = replace(
            current,
            surface_type=_select_surface_type(surface_type, current.surface_type),
            cursor=cursor,
            next_cursor=next_cursor,
            last_success_at=now,
            updated_at=now,
        )
        self._write_state(state)
        return state

    def mark_failure(
        self,
        collector_name: str,
        scope_id: str,
        error_kind: str,
        error_detail: str,
        surface_type: str = "",
    ) -> FeishuCollectorSyncState:
        """记录同步失败原因，不推进 cursor。"""
        now = _now_iso()
        current = self.read(collector_name, scope_id, surface_type)
        state = replace(
            current,
            surface_type=_select_surface_type(surface_type, current.surface_type),
            last_failure_at=now,
            last_error_kind=error_kind,
            last_error_detail=_single_line(error_detail),
            updated_at=now,
        )
        self._write_state(state)
        return state

    def next_cursor(self, collector_name: str, scope_id: str) -> str:
        """返回下一次同步起点；next_cursor 为空时回退到 cursor。"""
        state = self.read(collector_name, scope_id)
        return state.next_cursor or state.cursor

    def path_for(self, collector_name: str, scope_id: str) -> Path:
        """返回指定状态记录的工作区内绝对路径。"""
        collector_part = _safe_file_part(collector_name)
        scope_part = _safe_file_part(scope_id)
        relative = Path("data/feishu/sync_state") / collector_part / f"{scope_part}.md"
        return self.markdown_store.file_store.resolve(relative)

    def _write_state(self, state: FeishuCollectorSyncState) -> Path:
        """把状态写入 Markdown 文件。"""
        document = MarkdownDocument(
            frontmatter=_frontmatter_from_state(state),
            body=_body_from_state(state),
        )
        return self.markdown_store.write_document(
            self.path_for(state.collector_name, state.scope_id),
            document,
        )


def _initial_state(
    collector_name: str,
    scope_id: str,
    surface_type: str,
) -> FeishuCollectorSyncState:
    """构造不存在记录时的初始状态。"""
    return FeishuCollectorSyncState(
        collector_name=collector_name,
        surface_type=surface_type,
        scope_id=scope_id,
    )


def _state_from_frontmatter(
    frontmatter: dict[str, str],
    collector_name: str,
    scope_id: str,
    surface_type: str,
) -> FeishuCollectorSyncState:
    """从 Markdown frontmatter 恢复状态，缺失字段按调用参数兜底。"""
    return FeishuCollectorSyncState(
        collector_name=frontmatter.get("collector_name") or collector_name,
        surface_type=frontmatter.get("surface_type") or surface_type,
        scope_id=frontmatter.get("scope_id") or scope_id,
        cursor=frontmatter.get("cursor", ""),
        last_success_at=frontmatter.get("last_success_at", ""),
        last_failure_at=frontmatter.get("last_failure_at", ""),
        last_error_kind=frontmatter.get("last_error_kind", ""),
        last_error_detail=frontmatter.get("last_error_detail", ""),
        next_cursor=frontmatter.get("next_cursor", ""),
        updated_at=frontmatter.get("updated_at", ""),
    )


def _frontmatter_from_state(state: FeishuCollectorSyncState) -> dict[str, str]:
    """把状态转为简单 frontmatter 字段。"""
    record_id = (
        "sync_"
        + _safe_file_part(state.collector_name)
        + "_"
        + _safe_file_part(state.scope_id)
    )
    return {
        "schema": _SYNC_STATE_SCHEMA,
        "id": record_id,
        "collector_name": _frontmatter_value(state.collector_name),
        "surface_type": _frontmatter_value(state.surface_type),
        "scope_id": _frontmatter_value(state.scope_id),
        "cursor": _frontmatter_value(state.cursor),
        "last_success_at": _frontmatter_value(state.last_success_at),
        "last_failure_at": _frontmatter_value(state.last_failure_at),
        "last_error_kind": _frontmatter_value(state.last_error_kind),
        "last_error_detail": _frontmatter_value(state.last_error_detail),
        "next_cursor": _frontmatter_value(state.next_cursor),
        "updated_at": _frontmatter_value(state.updated_at),
    }


def _body_from_state(state: FeishuCollectorSyncState) -> str:
    """渲染便于人工查看的状态摘要。"""
    return (
        "# Feishu Collector Sync State\n\n"
        "## Summary\n\n"
        f"- collector_name: {state.collector_name}\n"
        f"- surface_type: {state.surface_type}\n"
        f"- scope_id: {state.scope_id}\n"
        f"- cursor: {state.cursor}\n"
        f"- next_cursor: {state.next_cursor}\n"
        f"- last_success_at: {state.last_success_at}\n"
        f"- last_failure_at: {state.last_failure_at}\n"
        f"- last_error_kind: {state.last_error_kind}\n"
        f"- last_error_detail: {state.last_error_detail}\n"
    )


def _select_surface_type(new_value: str, current_value: str) -> str:
    """优先使用调用方显式传入的 surface_type，否则保留已有值。"""
    return new_value or current_value


def _frontmatter_value(value: str) -> str:
    """把状态值转成 MarkdownStore 可接受的单行 frontmatter。"""
    clean = _single_line(value)
    if clean.strip().startswith(("[", "{", "-")):
        return "'" + clean.replace("'", "’") + "'"
    return clean


def _single_line(value: str) -> str:
    """把外部字符串压成单行，避免破坏简单 frontmatter。"""
    return str(value).replace("\r", " ").replace("\n", " ").strip()


def _safe_file_part(value: str) -> str:
    """把 collector_name/scope_id 转成安全文件名片段。"""
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value)
    return safe[:_MAX_SAFE_NAME_CHARS] or "unknown"


def _now_iso() -> str:
    """返回 UTC ISO 时间字符串。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _self_test() -> None:
    """验证成功和失败状态能写入并读回。"""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        store = FeishuSyncStateStore(Path(tmp))
        store.mark_success("self_test", "scope_a", "cur_1", "cur_2", "user_docs")
        state = store.mark_failure("self_test", "scope_a", "timeout", "request slow")
        assert state.cursor == "cur_1"
        assert state.next_cursor == "cur_2"
        assert state.last_error_kind == "timeout"


if __name__ == "__main__":
    _self_test()
    print("dutyflow feishu sync state self-test passed")
