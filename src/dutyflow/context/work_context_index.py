# 本文件负责枚举 DutyFlow 本地已落盘工作上下文，供短句查询先发现 refs。

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Mapping

from dutyflow.approval.approval_flow import ApprovalStore
from dutyflow.context.evidence_store import EvidenceStore
from dutyflow.feishu.ambient_context import AmbientContextScanQuery, AmbientContextStore
from dutyflow.storage.file_store import FileStore
from dutyflow.storage.markdown_store import MarkdownStore
from dutyflow.tasks.task_state import TaskStore

# 关键开关：单次本地工作上下文枚举最多返回 50 条，避免模型短句查询一次塞入过多上下文。
MAX_WORK_CONTEXT_ITEMS = 50
# 关键开关：未指定 limit 时默认返回 20 条，优先覆盖当天最重要的近期信息。
DEFAULT_WORK_CONTEXT_LIMIT = 20
# 关键开关：枚举结果中的 summary 最多保留 240 字，详情仍通过 read_context_ref 或 detail_file 读取。
WORK_CONTEXT_SUMMARY_MAX_CHARS = 240


@dataclass(frozen=True)
class WorkContextQuery:
    """表示本地工作上下文枚举条件，不允许访问项目外路径。"""

    date: str = ""
    source_types: tuple[str, ...] = ()
    query: str = ""
    task_statuses: tuple[str, ...] = ()
    approval_statuses: tuple[str, ...] = ()
    limit: int = DEFAULT_WORK_CONTEXT_LIMIT


@dataclass(frozen=True)
class WorkContextItem:
    """表示一条可交给模型选择或继续 read_context_ref 的轻量上下文条目。"""

    kind: str
    ref_type: str
    ref_id: str
    title: str
    summary: str
    source_type: str = ""
    status: str = ""
    created_at: str = ""
    updated_at: str = ""
    detail_file: str = ""

    def to_payload(self) -> dict[str, str]:
        """转换为工具层稳定 JSON 字段。"""
        return {
            "kind": self.kind,
            "ref_type": self.ref_type,
            "ref_id": self.ref_id,
            "title": self.title,
            "summary": self.summary,
            "source_type": self.source_type,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "detail_file": self.detail_file,
        }


@dataclass(frozen=True)
class WorkContextListResult:
    """表示一次本地工作上下文枚举结果。"""

    items: tuple[WorkContextItem, ...]
    total_count: int
    query: WorkContextQuery

    def to_payload(self) -> dict[str, object]:
        """转换为模型工具结果可消费的 JSON 对象。"""
        return {
            "total_count": self.total_count,
            "returned_count": len(self.items),
            "filters": _query_payload(self.query),
            "items": [item.to_payload() for item in self.items],
        }


class WorkContextIndexService:
    """只读扫描项目内固定业务目录，返回可追溯的工作上下文条目。"""

    def __init__(self, project_root: Path) -> None:
        """绑定项目根目录和各类业务 store。"""
        self.project_root = Path(project_root).resolve()
        self.ambient_store = AmbientContextStore(self.project_root)
        self.task_store = TaskStore(self.project_root)
        self.approval_store = ApprovalStore(self.project_root)
        self.evidence_store = EvidenceStore(self.project_root)
        self.markdown_store = MarkdownStore(FileStore(self.project_root))

    def list_context(self, query: WorkContextQuery | None = None) -> WorkContextListResult:
        """枚举本地工作上下文，并按查询条件过滤、按时间倒序返回。"""
        resolved = _normalize_query(query or WorkContextQuery())
        items = (
            self._ambient_items()
            + self._task_items()
            + self._approval_items()
            + self._evidence_items()
            + self._report_items()
        )
        filtered = tuple(item for item in items if _matches_query(item, resolved))
        sorted_items = tuple(sorted(filtered, key=_sort_key, reverse=True))
        return WorkContextListResult(
            sorted_items[: resolved.limit],
            len(filtered),
            resolved,
        )

    def _ambient_items(self) -> tuple[WorkContextItem, ...]:
        """把 ambient_context 记录转换为轻量条目。"""
        records = self.ambient_store.scan_records(AmbientContextScanQuery(limit=MAX_WORK_CONTEXT_ITEMS))
        return tuple(
            WorkContextItem(
                kind="ambient_context",
                ref_type="ambient_context",
                ref_id=record.record_id,
                title=record.text_preview or record.summary or record.record_id,
                summary=_trim(record.summary or record.text or record.text_preview),
                source_type=record.source_type,
                created_at=record.created_at,
                updated_at=record.fetched_at,
                detail_file=_relative_path(self.project_root, self.ambient_store.path_for(record)),
            )
            for record in records
        )

    def _task_items(self) -> tuple[WorkContextItem, ...]:
        """把任务状态记录转换为轻量条目。"""
        return tuple(
            WorkContextItem(
                kind="task",
                ref_type="task",
                ref_id=record.task_id,
                title=record.title,
                summary=_trim(record.summary or record.last_result_summary or record.next_action),
                status=record.status,
                created_at=record.created_at,
                updated_at=record.updated_at,
                detail_file=_relative_path(self.project_root, record.path),
            )
            for record in self.task_store.list_tasks()
        )

    def _approval_items(self) -> tuple[WorkContextItem, ...]:
        """把审批记录转换为轻量条目。"""
        approvals = self.approval_store.list_pending_approvals() + self.approval_store.list_completed_approvals()
        return tuple(
            WorkContextItem(
                kind="approval",
                ref_type="approval",
                ref_id=record.approval_id,
                title=record.request,
                summary=_trim(record.reason or record.risk or record.request),
                status=record.status,
                created_at=record.requested_at,
                updated_at=record.resolved_at or record.requested_at,
                detail_file=_relative_path(self.project_root, record.path),
            )
            for record in approvals
        )

    def _evidence_items(self) -> tuple[WorkContextItem, ...]:
        """把 Evidence 证据记录转换为轻量条目。"""
        return tuple(
            WorkContextItem(
                kind="evidence",
                ref_type="evidence",
                ref_id=record.evidence_id,
                title=record.summary or record.evidence_id,
                summary=_trim(record.summary or record.content),
                source_type=record.source_type,
                created_at=record.created_at,
                updated_at=record.created_at,
                detail_file=record.relative_path,
            )
            for record in self.evidence_store.list_evidence()
        )

    def _report_items(self) -> tuple[WorkContextItem, ...]:
        """把 reports 目录中的人工可见报告转换为轻量条目。"""
        report_root = self.project_root / "data" / "reports"
        if not report_root.exists():
            return ()
        items: list[WorkContextItem] = []
        for path in sorted(report_root.rglob("*.md")):
            item = self._try_report_item(path)
            if item is not None:
                items.append(item)
        return tuple(items)

    def _try_report_item(self, path: Path) -> WorkContextItem | None:
        """读取报告文件；坏文件只跳过，避免单个报告阻断短句查询。"""
        try:
            return self._report_item(path)
        except Exception:  # noqa: BLE001
            return None

    def _report_item(self, path: Path) -> WorkContextItem:
        """读取单个报告文件的标题、时间和摘要。"""
        document = self.markdown_store.read_document(path)
        ref_id = document.frontmatter.get("report_id") or document.frontmatter.get("id") or path.stem
        created_at = document.frontmatter.get("created_at", "")
        updated_at = document.frontmatter.get("updated_at", created_at)
        return WorkContextItem(
            kind="report",
            ref_type="report",
            ref_id=ref_id,
            title=_markdown_title(document.body) or ref_id,
            summary=_trim(self.markdown_store.extract_section(path, "Summary") or document.body),
            source_type="report",
            status=document.frontmatter.get("status", ""),
            created_at=created_at,
            updated_at=updated_at,
            detail_file=_relative_path(self.project_root, path),
        )


def query_from_tool_input(tool_input: Mapping[str, object]) -> WorkContextQuery:
    """把工具 JSON 入参转换为 WorkContextQuery。"""
    return WorkContextQuery(
        date=_read_text(tool_input, "date"),
        source_types=_split_csv(_read_text(tool_input, "source_types")),
        query=_read_text(tool_input, "query"),
        task_statuses=_split_csv(_read_text(tool_input, "task_status")),
        approval_statuses=_split_csv(_read_text(tool_input, "approval_status")),
        limit=_read_limit(tool_input.get("limit", DEFAULT_WORK_CONTEXT_LIMIT)),
    )


def _normalize_query(query: WorkContextQuery) -> WorkContextQuery:
    """规整日期别名和 limit 上限。"""
    return WorkContextQuery(
        date=_normalize_date(query.date),
        source_types=tuple(item.casefold() for item in query.source_types),
        query=query.query.casefold(),
        task_statuses=tuple(item.casefold() for item in query.task_statuses),
        approval_statuses=tuple(item.casefold() for item in query.approval_statuses),
        limit=_bounded_limit(query.limit),
    )


def _matches_query(item: WorkContextItem, query: WorkContextQuery) -> bool:
    """判断条目是否符合所有过滤条件。"""
    return (
        _matches_date(item, query.date)
        and _matches_source(item, query.source_types)
        and _matches_text(item, query.query)
        and _matches_task_status(item, query.task_statuses)
        and _matches_approval_status(item, query.approval_statuses)
    )


def _matches_date(item: WorkContextItem, date_text: str) -> bool:
    """按日期字符串过滤条目。"""
    if not date_text:
        return True
    return item.created_at.startswith(date_text) or item.updated_at.startswith(date_text)


def _matches_source(item: WorkContextItem, source_types: tuple[str, ...]) -> bool:
    """按 kind 或 source_type 过滤条目。"""
    if not source_types:
        return True
    values = {item.kind.casefold(), item.source_type.casefold()}
    return any(value in values for value in source_types)


def _matches_text(item: WorkContextItem, query: str) -> bool:
    """按标题和摘要做轻量包含匹配。"""
    if not query:
        return True
    haystack = " ".join((item.title, item.summary, item.ref_id, item.source_type)).casefold()
    return query in haystack


def _matches_task_status(item: WorkContextItem, statuses: tuple[str, ...]) -> bool:
    """按任务状态过滤；传入任务状态时只返回 task 条目。"""
    if not statuses:
        return True
    return item.kind == "task" and item.status.casefold() in statuses


def _matches_approval_status(item: WorkContextItem, statuses: tuple[str, ...]) -> bool:
    """按审批状态过滤；传入审批状态时只返回 approval 条目。"""
    if not statuses:
        return True
    return item.kind == "approval" and item.status.casefold() in statuses


def _sort_key(item: WorkContextItem) -> tuple[str, str, str]:
    """生成倒序排序键，优先使用更新时间。"""
    return (_datetime_key(item.updated_at or item.created_at), item.kind, item.ref_id)


def _datetime_key(value: str) -> str:
    """把 ISO 时间规整为可排序字符串。"""
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat()
    except ValueError:
        return value


def _read_text(tool_input: Mapping[str, object], key: str) -> str:
    """读取工具输入中的单行字符串。"""
    value = tool_input.get(key, "")
    if value is None:
        return ""
    return str(value).strip()


def _split_csv(value: str) -> tuple[str, ...]:
    """解析英文逗号分隔字段，并保持稳定去重顺序。"""
    items: list[str] = []
    for raw_item in value.split(","):
        normalized = raw_item.strip()
        if normalized and normalized not in items:
            items.append(normalized)
    return tuple(items)


def _read_limit(value: object) -> int:
    """读取 limit，非法值回退默认值。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return DEFAULT_WORK_CONTEXT_LIMIT


def _bounded_limit(value: int) -> int:
    """限制 limit 范围，避免模型请求过多上下文。"""
    if value <= 0:
        return 0
    return min(value, MAX_WORK_CONTEXT_ITEMS)


def _normalize_date(value: str) -> str:
    """支持 today/今天 别名，其他值保持原样。"""
    text = value.strip()
    if text in {"today", "今天"}:
        return datetime.now().astimezone().date().isoformat()
    return text


def _trim(value: str) -> str:
    """裁剪模型可见摘要。"""
    normalized = str(value).strip()
    if len(normalized) <= WORK_CONTEXT_SUMMARY_MAX_CHARS:
        return normalized
    return normalized[: WORK_CONTEXT_SUMMARY_MAX_CHARS - 3] + "..."


def _markdown_title(body: str) -> str:
    """从 Markdown 正文提取一级标题。"""
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped.removeprefix("# ").strip()
    return ""


def _query_payload(query: WorkContextQuery) -> dict[str, object]:
    """生成结果中的查询条件回显。"""
    return {
        "date": query.date,
        "source_types": list(query.source_types),
        "query": query.query,
        "task_status": list(query.task_statuses),
        "approval_status": list(query.approval_statuses),
        "limit": query.limit,
    }


def _relative_path(project_root: Path, path: Path) -> str:
    """把项目内路径转换为相对路径。"""
    try:
        return str(Path(path).resolve().relative_to(project_root))
    except ValueError:
        return str(path)


def _self_test() -> None:
    """验证空目录枚举返回稳定空结果。"""
    import tempfile

    with tempfile.TemporaryDirectory() as temp_dir:
        result = WorkContextIndexService(Path(temp_dir)).list_context()
    assert result.total_count == 0
    assert result.items == ()


if __name__ == "__main__":
    _self_test()
    print("dutyflow work context index self-test passed")
