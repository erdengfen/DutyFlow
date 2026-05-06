# 本文件负责以 owner 用户身份枚举显式授权范围内的飞书云盘文件夹清单并落盘为 ambient_context。

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import unquote, urlsplit

from dutyflow.feishu.ambient_context import (
    AmbientContextRecord,
    AmbientContextStore,
    AmbientDocLink,
    AmbientFileClue,
)
from dutyflow.feishu.collector_budget import CollectorBudget, CollectorBudgetGuard
from dutyflow.feishu.scope_registry import (
    DEFAULT_SCOPE_ACCOUNT_ID,
    DOC_SCOPE,
    DRIVE_FOLDER_SCOPE,
    FILE_SCOPE,
    USER_DOCUMENT_COLLECTOR,
    WIKI_SCOPE,
    FeishuScopeRecord,
    FeishuScopeRegistry,
    scope_account_id_from_config,
)
from dutyflow.feishu.sync_state import FeishuSyncStateStore
from dutyflow.feishu.user_client import FeishuUserClient
from dutyflow.feishu.user_request import FeishuUserResponse

COLLECTOR_NAME = USER_DOCUMENT_COLLECTOR
SOURCE_TYPE = "user_document"
_ROOT_FOLDER_URL = "https://open.feishu.cn/open-apis/drive/explorer/v2/root_folder/meta"
_FILES_URL = "https://open.feishu.cn/open-apis/drive/v1/files"
_ORDER_BY_EDITED_TIME = "EditedTime"
_DIRECTION_DESC = "DESC"
# 关键开关：飞书云盘文件夹清单接口每页最多请求 50 条，避免单页元数据过大。
DEFAULT_USER_DOCUMENT_PAGE_SIZE = 50
# 关键开关：云文档元数据预览最多保留 160 字符，兼顾标题、类型和链接可读性。
USER_DOCUMENT_PREVIEW_CHARS = 160
_DOC_SCOPE_TYPES = {"docx", "doc", "sheet", "mindnote"}
_SUPPORTED_CHILD_SCOPE_TYPES = {"folder", "docx", "doc", "sheet", "mindnote", "wiki", "file"}


@dataclass(frozen=True)
class UserDocumentRootDiscoverResult:
    """表示用户云盘 root folder 发现结果。"""

    ok: bool
    status: str
    root_folder_token: str
    scope_record: FeishuScopeRecord | None
    detail: str


@dataclass(frozen=True)
class UserDocumentCollectResult:
    """表示 user_document_collector 单个 scope 的同步结果。"""

    ok: bool
    status: str
    scope_id: str
    scope_type: str
    items_written: int
    candidate_scopes_written: int
    record_paths: tuple[str, ...]
    cursor: str
    next_cursor: str
    has_more: bool
    next_page_token: str
    sync_state_path: str
    stopped_reason: str
    detail: str


@dataclass(frozen=True)
class _ParsedDocumentItem:
    """表示已转换为 ambient_context 的云盘清单 item。"""

    record: AmbientContextRecord
    modified_cursor: str
    candidate_scope: FeishuScopeRecord | None


class UserDocumentCollector:
    """采集用户明确授权范围内的云盘文件夹清单，不读取大文档正文。"""

    def __init__(
        self,
        project_root: Path,
        user_client: FeishuUserClient,
        *,
        registry: FeishuScopeRegistry | None = None,
        ambient_store: AmbientContextStore | None = None,
        sync_state_store: FeishuSyncStateStore | None = None,
        budget: CollectorBudget | None = None,
    ) -> None:
        """绑定用户面 client、scope registry、落盘层、sync_state 和预算。"""
        self.project_root = Path(project_root).resolve()
        self.user_client = user_client
        self.registry = registry or FeishuScopeRegistry(self.project_root)
        self.ambient_store = ambient_store or AmbientContextStore(self.project_root)
        self.sync_state_store = sync_state_store or FeishuSyncStateStore(self.project_root)
        self.budget = budget or CollectorBudget(collector_name=COLLECTOR_NAME)

    def discover_root(
        self,
        config: object,
        *,
        save_raw: bool = False,
    ) -> UserDocumentRootDiscoverResult:
        """获取 owner 我的空间 root folder，并只写入 candidate drive_folder scope。"""
        response = self.user_client.get(
            _ROOT_FOLDER_URL,
            timeout_seconds=self.budget.request_timeout_seconds,
            trace_id="udc_root",
            collector_name=COLLECTOR_NAME,
            save_raw=save_raw,
        )
        if not response.ok:
            return UserDocumentRootDiscoverResult(False, response.status, "", None, response.detail)
        record = _root_scope_record(response.data, scope_account_id_from_config(config), config)
        if record is None:
            return UserDocumentRootDiscoverResult(False, "root_folder_missing", "", None, "root folder token missing")
        written = self.registry.upsert_candidate(record)
        return UserDocumentRootDiscoverResult(True, "ok", written.scope_id, written, "")

    def collect(
        self,
        folder_token: str,
        *,
        page_size: int = DEFAULT_USER_DOCUMENT_PAGE_SIZE,
        page_token: str = "",
        account_id: str = DEFAULT_SCOPE_ACCOUNT_ID,
        save_raw: bool = False,
    ) -> UserDocumentCollectResult:
        """拉取一个已授权文件夹当前层级的文件清单。"""
        _require_folder_token(folder_token)
        guard = CollectorBudgetGuard(self.budget)
        context = _CollectContext(
            scope_id=folder_token,
            scope_type=DRIVE_FOLDER_SCOPE,
            account_id=account_id,
            page_size=_bounded_page_size(page_size),
            sync_state_ref=_relative_path(
                self.project_root,
                self.sync_state_store.path_for(COLLECTOR_NAME, folder_token),
            ),
        )
        return self._collect_folder_pages(context, guard, page_token, save_raw)

    def collect_enabled_scopes(
        self,
        config: object,
        *,
        save_raw: bool = False,
    ) -> tuple[UserDocumentCollectResult, ...]:
        """从 scope registry 读取 enabled drive_folder 并逐一枚举当前层级清单。"""
        account_id = scope_account_id_from_config(config)
        scopes = self.registry.list_enabled(COLLECTOR_NAME, account_id=account_id)
        results: list[UserDocumentCollectResult] = []
        for scope in scopes:
            result = self._collect_enabled_scope(scope, save_raw)
            if result is None:
                continue
            _mark_scope_after_collect(self.registry, scope, result)
            results.append(result)
        return tuple(results)

    def _collect_enabled_scope(
        self,
        scope: FeishuScopeRecord,
        save_raw: bool,
    ) -> UserDocumentCollectResult | None:
        """按 scope 类型分发采集；文档直连 scope 只沉淀元数据。"""
        if scope.scope_type == DRIVE_FOLDER_SCOPE:
            page_token = self.sync_state_store.read(COLLECTOR_NAME, scope.scope_id, SOURCE_TYPE).next_cursor
            return self.collect(
                scope.scope_id,
                page_token=page_token,
                account_id=scope.account_id,
                save_raw=save_raw,
            )
        if scope.scope_type in {DOC_SCOPE, WIKI_SCOPE, FILE_SCOPE}:
            return self.collect_resource_scope(scope)
        return None

    def collect_resource_scope(self, scope: FeishuScopeRecord) -> UserDocumentCollectResult:
        """把已批准的 doc/wiki/file scope 作为元数据记录落盘，不读取正文。"""
        context = _CollectContext(
            scope_id=scope.scope_id,
            scope_type=scope.scope_type,
            account_id=scope.account_id,
            page_size=1,
            sync_state_ref=_relative_path(
                self.project_root,
                self.sync_state_store.path_for(COLLECTOR_NAME, scope.scope_id),
            ),
        )
        record = _record_from_scope(scope, context, _now_iso())
        written = self.ambient_store.write(record)
        record_path = _relative_path(self.project_root, written.path)
        state = self.sync_state_store.mark_success(
            COLLECTOR_NAME,
            scope.scope_id,
            cursor=record.fetched_at,
            surface_type=SOURCE_TYPE,
        )
        return _result(True, "ok", context, CollectorBudgetGuard(self.budget), [record_path], 0, state.cursor, state.next_cursor, False, "", "")

    def _collect_folder_pages(
        self,
        context: "_CollectContext",
        guard: CollectorBudgetGuard,
        page_token: str,
        save_raw: bool,
    ) -> UserDocumentCollectResult:
        """按预算逐页请求文件夹清单并写入 ambient_context。"""
        record_paths: list[str] = []
        candidate_count = 0
        cursor = ""
        next_page_token = page_token
        has_more = False
        while guard.record_page():
            response = self._request_folder_page(context, next_page_token, save_raw)
            if not response.ok:
                return self._failure_result(context, response, guard)
            written, candidates, cursor = self._write_response_files(response, context, guard, cursor)
            record_paths.extend(written)
            candidate_count += candidates
            has_more = response.has_more
            next_page_token = response.page_token
            if _should_stop_loop(response, guard):
                break
        return self._success_result(context, guard, record_paths, candidate_count, cursor, has_more, next_page_token)

    def _request_folder_page(
        self,
        context: "_CollectContext",
        page_token: str,
        save_raw: bool,
    ) -> FeishuUserResponse:
        """请求一页云盘文件夹清单。"""
        return self.user_client.get(
            _FILES_URL,
            params=_request_params(context, page_token),
            timeout_seconds=self.budget.request_timeout_seconds,
            trace_id=_trace_id(context.scope_id),
            collector_name=COLLECTOR_NAME,
            save_raw=save_raw,
        )

    def _write_response_files(
        self,
        response: FeishuUserResponse,
        context: "_CollectContext",
        guard: CollectorBudgetGuard,
        cursor: str,
    ) -> tuple[list[str], int, str]:
        """解析一页文件清单，写入记录，并把新资源沉淀为 candidate scope。"""
        written: list[str] = []
        candidates = 0
        for item in _response_files(response):
            if not guard.can_accept_item():
                break
            parsed = _parse_file_item(item, context, guard, response.raw_path)
            if parsed is None:
                continue
            guard.record_item()
            result = self.ambient_store.write(parsed.record)
            written.append(_relative_path(self.project_root, result.path))
            cursor = _max_cursor(cursor, parsed.modified_cursor)
            if parsed.candidate_scope is not None:
                self.registry.upsert_candidate(parsed.candidate_scope)
                candidates += 1
        return written, candidates, cursor

    def _success_result(
        self,
        context: "_CollectContext",
        guard: CollectorBudgetGuard,
        record_paths: list[str],
        candidate_count: int,
        cursor: str,
        has_more: bool,
        next_page_token: str,
    ) -> UserDocumentCollectResult:
        """写入成功 sync_state 并构造成功结果。"""
        current = self.sync_state_store.read(COLLECTOR_NAME, context.scope_id, SOURCE_TYPE)
        next_cursor = next_page_token if has_more and next_page_token else ""
        state = self.sync_state_store.mark_success(
            COLLECTOR_NAME,
            context.scope_id,
            cursor=cursor or current.cursor,
            next_cursor=next_cursor,
            surface_type=SOURCE_TYPE,
        )
        return _result(True, "ok", context, guard, record_paths, candidate_count, state.cursor, state.next_cursor, has_more, next_page_token, "")

    def _failure_result(
        self,
        context: "_CollectContext",
        response: FeishuUserResponse,
        guard: CollectorBudgetGuard,
    ) -> UserDocumentCollectResult:
        """写入失败 sync_state 并构造失败结果。"""
        state = self.sync_state_store.mark_failure(
            COLLECTOR_NAME,
            context.scope_id,
            response.status,
            response.detail,
            surface_type=SOURCE_TYPE,
        )
        return _result(False, response.status, context, guard, [], 0, state.cursor, state.next_cursor, False, "", response.detail)


@dataclass(frozen=True)
class _CollectContext:
    """表示 user_document_collector 单个文件夹同步上下文。"""

    scope_id: str
    scope_type: str
    account_id: str
    page_size: int
    sync_state_ref: str


def _parse_file_item(
    item: Mapping[str, Any],
    context: _CollectContext,
    guard: CollectorBudgetGuard,
    raw_path: str,
) -> _ParsedDocumentItem | None:
    """把飞书文件清单 item 转为 ambient_context 记录和候选 scope。"""
    token = _as_text(item.get("token"))
    if not token:
        return None
    file_type = _as_text(item.get("type"))
    text = guard.trim_content(_document_text(item))
    modified_time = _as_text(item.get("modified_time"))
    record = AmbientContextRecord(
        record_id="ud_" + _safe_part(file_type or "resource") + "_" + _safe_part(token),
        source_type=SOURCE_TYPE,
        collector_name=COLLECTOR_NAME,
        source_id=token,
        sync_scope_id=context.scope_id,
        created_at=_time_to_iso(modified_time or _as_text(item.get("created_time"))),
        fetched_at=_now_iso(),
        text=text,
        text_preview=_truncate(text, USER_DOCUMENT_PREVIEW_CHARS),
        summary=_summary(item),
        raw_message_ref=raw_path,
        sync_state_ref=context.sync_state_ref,
        doc_links=_doc_links_for_item(item),
        file_clues=_file_clues_for_item(item),
        frontmatter_extra=_document_frontmatter(item, context.scope_id),
    )
    return _ParsedDocumentItem(
        record,
        modified_time or _as_text(item.get("created_time")),
        _child_scope_record(item, context.scope_id, context.account_id),
    )


def _record_from_scope(
    scope: FeishuScopeRecord,
    context: _CollectContext,
    fetched_at: str,
) -> AmbientContextRecord:
    """把已批准的 doc/wiki/file scope 转为一条元数据 ambient_context 记录。"""
    text = " ".join(value for value in (scope.scope_type, scope.scope_id, scope.source_url) if value)
    item = {
        "token": scope.scope_id,
        "name": scope.scope_id,
        "type": scope.scope_type,
        "url": scope.source_url,
        "owner_id": scope.owner_open_id or scope.owner_user_id,
    }
    return AmbientContextRecord(
        record_id="ud_" + _safe_part(scope.scope_type) + "_" + _safe_part(scope.scope_id),
        source_type=SOURCE_TYPE,
        collector_name=COLLECTOR_NAME,
        source_id=scope.scope_id,
        sync_scope_id=context.scope_id,
        created_at=fetched_at,
        fetched_at=fetched_at,
        text=text,
        text_preview=_truncate(text, USER_DOCUMENT_PREVIEW_CHARS),
        summary=f"user document scope {scope.scope_type} {scope.scope_id}",
        sync_state_ref=context.sync_state_ref,
        doc_links=_doc_links_for_item(item),
        file_clues=_file_clues_for_item(item),
        frontmatter_extra=_document_frontmatter(item, scope.source_chat_id),
    )


def _root_scope_record(
    data: Mapping[str, Any],
    account_id: str,
    config: object,
) -> FeishuScopeRecord | None:
    """把 root_folder/meta 响应转为 drive_folder candidate scope。"""
    token = _as_text(data.get("token"))
    if not token:
        return None
    return FeishuScopeRecord(
        account_id=account_id,
        scope_type=DRIVE_FOLDER_SCOPE,
        scope_id=token,
        status="candidate",
        collector_names=(COLLECTOR_NAME,),
        discovered_from="oauth_drive_root",
        tenant_key=str(getattr(config, "feishu_tenant_key", "")),
        owner_open_id=str(getattr(config, "feishu_owner_open_id", "")),
        owner_user_id=_as_text(data.get("user_id")) or str(getattr(config, "feishu_owner_user_id", "")),
        source_id=token,
    )


def _child_scope_record(
    item: Mapping[str, Any],
    parent_folder_token: str,
    account_id: str,
) -> FeishuScopeRecord | None:
    """把文件夹清单 item 转为子资源 candidate scope；不自动启用。"""
    file_type = _as_text(item.get("type"))
    if file_type not in _SUPPORTED_CHILD_SCOPE_TYPES:
        return None
    token = _as_text(item.get("token"))
    if not token:
        return None
    return FeishuScopeRecord(
        account_id=account_id or DEFAULT_SCOPE_ACCOUNT_ID,
        scope_type=_scope_type_for_file_type(file_type),
        scope_id=token,
        status="candidate",
        collector_names=(COLLECTOR_NAME,),
        discovered_from="drive_folder_list",
        source_id=token,
        source_url=_as_text(item.get("url")),
        source_chat_id=parent_folder_token,
    )


def _scope_type_for_file_type(file_type: str) -> str:
    """把飞书文件类型映射到第一版 scope_type。"""
    if file_type == "folder":
        return DRIVE_FOLDER_SCOPE
    if file_type == "wiki":
        return WIKI_SCOPE
    if file_type == "file":
        return FILE_SCOPE
    return DOC_SCOPE


def _document_frontmatter(item: Mapping[str, Any], folder_token: str) -> dict[str, str]:
    """提取 user_document 记录专属 frontmatter。"""
    return {
        "file_token": _as_text(item.get("token")),
        "file_name": _as_text(item.get("name")),
        "file_type": _as_text(item.get("type")),
        "file_url": _as_text(item.get("url")),
        "created_time": _as_text(item.get("created_time")),
        "modified_time": _as_text(item.get("modified_time")),
        "owner_id": _as_text(item.get("owner_id")),
        "parent_folder_token": folder_token,
    }


def _document_text(item: Mapping[str, Any]) -> str:
    """构造文件清单 item 的可检索短文本。"""
    values = (
        _as_text(item.get("name")),
        "type=" + _as_text(item.get("type")) if _as_text(item.get("type")) else "",
        "owner=" + _as_text(item.get("owner_id")) if _as_text(item.get("owner_id")) else "",
        _as_text(item.get("url")),
    )
    return " ".join(value for value in values if value)


def _doc_links_for_item(item: Mapping[str, Any]) -> tuple[AmbientDocLink, ...]:
    """为云文档类 item 构造文档链接线索。"""
    url = _as_text(item.get("url"))
    token = _as_text(item.get("token"))
    file_type = _as_text(item.get("type"))
    if not url or file_type in {"folder", "file", "shortcut"}:
        return ()
    return (AmbientDocLink(url=url, resource_type=_resource_type(url) or file_type, token=_resource_token(url) or token),)


def _file_clues_for_item(item: Mapping[str, Any]) -> tuple[AmbientFileClue, ...]:
    """为普通文件 item 构造附件线索，不下载二进制内容。"""
    if _as_text(item.get("type")) != "file":
        return ()
    token = _as_text(item.get("token"))
    return (AmbientFileClue(token, "file", token, _as_text(item.get("name"))),)


def _summary(item: Mapping[str, Any]) -> str:
    """构造云盘清单记录的人可读摘要。"""
    name = _as_text(item.get("name"))
    file_type = _as_text(item.get("type"))
    modified = _as_text(item.get("modified_time"))
    return f"user document {name} type {file_type} modified {modified}".strip()


def _request_params(context: _CollectContext, page_token: str) -> dict[str, Any]:
    """构造文件夹清单接口 query 参数。"""
    params: dict[str, Any] = {
        "folder_token": context.scope_id,
        "page_size": context.page_size,
        "order_by": _ORDER_BY_EDITED_TIME,
        "direction": _DIRECTION_DESC,
    }
    if page_token:
        params["page_token"] = page_token
    return params


def _response_files(response: FeishuUserResponse) -> tuple[Mapping[str, Any], ...]:
    """从统一响应中提取文件清单 items。"""
    files = response.data.get("files")
    if not isinstance(files, list):
        return ()
    return tuple(dict(item) for item in files if isinstance(item, Mapping))


def _mark_scope_after_collect(
    registry: FeishuScopeRegistry,
    scope: FeishuScopeRecord,
    result: UserDocumentCollectResult,
) -> None:
    """根据同步结果更新 scope registry 可观察状态。"""
    if result.ok:
        registry.mark_success(scope.account_id, scope.scope_type, scope.scope_id)
    elif result.status == "permission_denied":
        registry.mark_permission_denied(scope.account_id, scope.scope_type, scope.scope_id, result.detail or result.status)


def _should_stop_loop(response: FeishuUserResponse, guard: CollectorBudgetGuard) -> bool:
    """判断当前轮次是否应停止继续请求下一页。"""
    if not guard.can_accept_item():
        return True
    if not response.has_more or not response.page_token:
        return True
    return not guard.can_request_next_page()


def _result(
    ok: bool,
    status: str,
    context: _CollectContext,
    guard: CollectorBudgetGuard,
    record_paths: list[str],
    candidate_count: int,
    cursor: str,
    next_cursor: str,
    has_more: bool,
    next_page_token: str,
    detail: str,
) -> UserDocumentCollectResult:
    """构造 collector 对外结果对象。"""
    usage = guard.snapshot()
    return UserDocumentCollectResult(
        ok=ok,
        status=status,
        scope_id=context.scope_id,
        scope_type=context.scope_type,
        items_written=len(record_paths),
        candidate_scopes_written=candidate_count,
        record_paths=tuple(record_paths),
        cursor=cursor,
        next_cursor=next_cursor,
        has_more=has_more,
        next_page_token=next_page_token,
        sync_state_path=context.sync_state_ref,
        stopped_reason=usage.stopped_reason,
        detail=detail,
    )


def _bounded_page_size(page_size: int) -> int:
    """把 page_size 限制在飞书接口允许的安全范围内。"""
    try:
        value = int(page_size)
    except (TypeError, ValueError):
        value = DEFAULT_USER_DOCUMENT_PAGE_SIZE
    return max(1, min(value, DEFAULT_USER_DOCUMENT_PAGE_SIZE))


def _time_to_iso(value: str) -> str:
    """把飞书时间字段转换为 ISO-8601；无法识别时回退当前时间。"""
    text = _as_text(value)
    if text.isdigit() and len(text) >= 13:
        return _seconds_to_iso(int(text[:10]))
    if text.isdigit() and len(text) >= 10:
        return _seconds_to_iso(int(text[:10]))
    if len(text) >= 10 and text[4:5] == "-" and text[7:8] == "-":
        return text
    return _now_iso()


def _seconds_to_iso(seconds: int) -> str:
    """把秒级时间戳转为本地时区 ISO 时间。"""
    return datetime.fromtimestamp(seconds, timezone.utc).astimezone().isoformat(timespec="seconds")


def _max_cursor(left: str, right: str) -> str:
    """返回较新的 modified_time/created_time 字符串。"""
    if not right:
        return left
    if not left:
        return right
    if left.isdigit() and right.isdigit():
        return right if int(right) > int(left) else left
    return right if right > left else left


def _resource_type(url: str) -> str:
    """从飞书 URL path 推断资源类型。"""
    segments = _url_segments(url)
    for segment in segments:
        if segment in {"docx", "docs", "wiki", "sheets", "base", "mindnotes", "file"}:
            return segment
    return ""


def _resource_token(url: str) -> str:
    """从飞书 URL path 中提取资源 token。"""
    segments = _url_segments(url)
    markers = {"docx", "docs", "wiki", "sheets", "base", "mindnotes", "file"}
    for index, segment in enumerate(segments):
        if segment in markers and index + 1 < len(segments):
            return segments[index + 1]
    return segments[-1] if segments else ""


def _url_segments(url: str) -> list[str]:
    """返回 URL path 的非空片段。"""
    return [unquote(segment) for segment in urlsplit(url).path.split("/") if segment]


def _relative_path(root: Path, path: Path | str) -> str:
    """返回工作区相对路径。"""
    resolved = Path(path).resolve()
    try:
        return str(resolved.relative_to(root.resolve()))
    except ValueError:
        return str(resolved)


def _trace_id(folder_token: str) -> str:
    """构造请求 trace_id，便于审计日志定位。"""
    return "udc_" + _safe_part(folder_token)


def _safe_part(value: str) -> str:
    """把外部 ID 转换为安全 trace/file 片段。"""
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value)[:80]


def _truncate(text: str, limit: int) -> str:
    """裁剪预览文本。"""
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _as_text(value: Any) -> str:
    """把值稳定转换为去空白字符串。"""
    if value is None:
        return ""
    return str(value).strip()


def _require_folder_token(folder_token: str) -> None:
    """校验 collector 必须绑定明确 folder token。"""
    if not folder_token:
        raise ValueError("folder_token is required")


def _now_iso() -> str:
    """返回当前本地时区 ISO 时间字符串。"""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _self_test() -> None:
    """验证文件清单 item 可生成 user_document ambient_context 记录。"""
    item = {
        "token": "doxcn_1",
        "name": "项目计划",
        "type": "docx",
        "url": "https://example.feishu.cn/docx/doxcn_1",
        "modified_time": "1778040000",
        "owner_id": "ou_1",
    }
    context = _CollectContext("fld_root", DRIVE_FOLDER_SCOPE, DEFAULT_SCOPE_ACCOUNT_ID, 50, "")
    parsed = _parse_file_item(item, context, CollectorBudgetGuard(CollectorBudget(COLLECTOR_NAME)), "")
    assert parsed is not None
    assert parsed.record.record_id == "ud_docx_doxcn_1"
    assert parsed.record.source_type == SOURCE_TYPE
    assert parsed.record.doc_links[0].token == "doxcn_1"


if __name__ == "__main__":
    _self_test()
    print("dutyflow feishu user document collector self-test passed")
