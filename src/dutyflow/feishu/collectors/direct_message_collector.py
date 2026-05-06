# 本文件负责以 owner 用户身份拉取显式范围内的飞书 p2p 私信并落盘为 ambient_context。

from __future__ import annotations

import json
import re
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
from dutyflow.feishu.sync_state import FeishuSyncStateStore
from dutyflow.feishu.user_client import FeishuUserClient
from dutyflow.feishu.user_request import FeishuUserResponse

COLLECTOR_NAME = "direct_message_collector"
SOURCE_TYPE = "direct_message"
_FEISHU_MESSAGES_URL = "https://open.feishu.cn/open-apis/im/v1/messages"
_CONTAINER_ID_TYPE = "chat"
_SORT_ASC = "ByCreateTimeAsc"
# 关键开关：飞书会话历史消息接口第一版每页最多请求 50 条，避免私信采集单页过大。
DEFAULT_DIRECT_MESSAGE_PAGE_SIZE = 50
# 关键开关：消息文本预览最多保留 120 字符，供索引和人工列表快速查看。
DIRECT_MESSAGE_PREVIEW_CHARS = 120
_URL_PATTERN = re.compile(r"https?://[^\s<>()\"']+")
_FILE_MESSAGE_TYPES = {"file", "image", "media", "audio"}
_DOC_PATH_MARKERS = {
    "docx": "docx",
    "docs": "doc",
    "wiki": "wiki",
    "sheets": "sheet",
    "base": "bitable",
    "mindnotes": "mindnote",
    "file": "file",
}


@dataclass(frozen=True)
class DirectMessageCollectResult:
    """表示 direct_message_collector 单轮同步结果。"""

    ok: bool
    status: str
    chat_id: str
    items_written: int
    record_paths: tuple[str, ...]
    cursor: str
    next_cursor: str
    has_more: bool
    next_page_token: str
    sync_state_path: str
    stopped_reason: str
    detail: str


@dataclass(frozen=True)
class _ParsedDirectMessage:
    """表示已转换为 ambient_context 的单条私信。"""

    record: AmbientContextRecord
    create_time_ms: str


class DirectMessageCollector:
    """采集用户明确授权范围内的 p2p 私信历史消息。"""

    def __init__(
        self,
        project_root: Path,
        user_client: FeishuUserClient,
        *,
        ambient_store: AmbientContextStore | None = None,
        sync_state_store: FeishuSyncStateStore | None = None,
        budget: CollectorBudget | None = None,
    ) -> None:
        """绑定用户面 client、落盘层、sync_state 和单轮预算。"""
        self.project_root = Path(project_root).resolve()
        self.user_client = user_client
        self.ambient_store = ambient_store or AmbientContextStore(self.project_root)
        self.sync_state_store = sync_state_store or FeishuSyncStateStore(self.project_root)
        self.budget = budget or CollectorBudget(collector_name=COLLECTOR_NAME)

    def collect(
        self,
        chat_id: str,
        *,
        start_time: int | str,
        end_time: int | str,
        page_size: int = DEFAULT_DIRECT_MESSAGE_PAGE_SIZE,
        page_token: str = "",
        sort_type: str = _SORT_ASC,
        save_raw: bool = False,
    ) -> DirectMessageCollectResult:
        """按显式时间窗口拉取一个 p2p chat_id 的历史消息。"""
        _require_chat_id(chat_id)
        guard = CollectorBudgetGuard(self.budget)
        context = _CollectContext(
            chat_id=chat_id,
            start_time=_timestamp_text(start_time),
            end_time=_timestamp_text(end_time),
            page_size=_bounded_page_size(page_size),
            sort_type=sort_type or _SORT_ASC,
            sync_state_ref=_relative_path(
                self.project_root,
                self.sync_state_store.path_for(COLLECTOR_NAME, chat_id),
            ),
        )
        return self._collect_pages(context, guard, page_token, save_raw)

    def _collect_pages(
        self,
        context: "_CollectContext",
        guard: CollectorBudgetGuard,
        page_token: str,
        save_raw: bool,
    ) -> DirectMessageCollectResult:
        """按预算逐页请求并写入 ambient_context。"""
        record_paths: list[str] = []
        max_create_ms = ""
        next_page_token = page_token
        has_more = False
        while guard.record_page():
            response = self._request_page(context, next_page_token, save_raw)
            if not response.ok:
                return self._failure_result(context, response, guard)
            written, max_create_ms = self._write_response_items(
                response,
                context,
                guard,
                max_create_ms,
            )
            record_paths.extend(written)
            has_more = response.has_more
            next_page_token = response.page_token
            if _should_stop_loop(response, guard):
                break
        return self._success_result(context, guard, record_paths, max_create_ms, has_more, next_page_token)

    def _request_page(
        self,
        context: "_CollectContext",
        page_token: str,
        save_raw: bool,
    ) -> FeishuUserResponse:
        """请求一页会话历史消息。"""
        return self.user_client.get(
            _FEISHU_MESSAGES_URL,
            params=_request_params(context, page_token),
            timeout_seconds=self.budget.request_timeout_seconds,
            trace_id=_trace_id(context.chat_id, context.end_time),
            collector_name=COLLECTOR_NAME,
            save_raw=save_raw,
        )

    def _write_response_items(
        self,
        response: FeishuUserResponse,
        context: "_CollectContext",
        guard: CollectorBudgetGuard,
        max_create_ms: str,
    ) -> tuple[list[str], str]:
        """解析一页消息并写入 ambient_context。"""
        written: list[str] = []
        for item in _response_items(response):
            if not guard.can_accept_item():
                break
            parsed = _parse_message(item, context, guard, response.raw_path)
            if parsed is None:
                continue
            guard.record_item()
            result = self.ambient_store.write(parsed.record)
            written.append(_relative_path(self.project_root, result.path))
            max_create_ms = _max_timestamp(max_create_ms, parsed.create_time_ms)
        return written, max_create_ms

    def _success_result(
        self,
        context: "_CollectContext",
        guard: CollectorBudgetGuard,
        record_paths: list[str],
        max_create_ms: str,
        has_more: bool,
        next_page_token: str,
    ) -> DirectMessageCollectResult:
        """写入成功 sync_state 并构造成功结果。"""
        current = self.sync_state_store.read(COLLECTOR_NAME, context.chat_id, SOURCE_TYPE)
        cursor = max_create_ms or current.cursor
        next_cursor = _next_cursor_from_ms(max_create_ms) if max_create_ms else current.next_cursor
        state = self.sync_state_store.mark_success(
            COLLECTOR_NAME,
            context.chat_id,
            cursor=cursor,
            next_cursor=next_cursor,
            surface_type=SOURCE_TYPE,
        )
        return _result(True, "ok", context, guard, record_paths, state.cursor, state.next_cursor, has_more, next_page_token, "")

    def _failure_result(
        self,
        context: "_CollectContext",
        response: FeishuUserResponse,
        guard: CollectorBudgetGuard,
    ) -> DirectMessageCollectResult:
        """写入失败 sync_state 并构造失败结果。"""
        state = self.sync_state_store.mark_failure(
            COLLECTOR_NAME,
            context.chat_id,
            response.status,
            response.detail,
            surface_type=SOURCE_TYPE,
        )
        return _result(False, response.status, context, guard, [], state.cursor, state.next_cursor, False, "", response.detail)


@dataclass(frozen=True)
class _CollectContext:
    """表示 direct_message_collector 单轮请求上下文。"""

    chat_id: str
    start_time: str
    end_time: str
    page_size: int
    sort_type: str
    sync_state_ref: str


def _parse_message(
    item: Mapping[str, Any],
    context: _CollectContext,
    guard: CollectorBudgetGuard,
    raw_path: str,
) -> _ParsedDirectMessage | None:
    """把飞书消息 item 转为 ambient_context 记录。"""
    message_id = _as_text(item.get("message_id"))
    if not message_id:
        return None
    content = _content_mapping(item)
    msg_type = _as_text(item.get("msg_type"))
    text = guard.trim_content(_message_text(content, msg_type))
    create_time = _as_text(item.get("create_time"))
    record = AmbientContextRecord(
        record_id="dm_" + message_id,
        source_type=SOURCE_TYPE,
        collector_name=COLLECTOR_NAME,
        source_id=_as_text(item.get("chat_id")) or context.chat_id,
        sync_scope_id=context.chat_id,
        created_at=_timestamp_ms_to_iso(create_time),
        fetched_at=_now_iso(),
        text=text,
        text_preview=_truncate(text, DIRECT_MESSAGE_PREVIEW_CHARS),
        summary=_summary(message_id, item, msg_type, text),
        raw_message_ref=raw_path,
        sync_state_ref=context.sync_state_ref,
        doc_links=_extract_doc_links(content, text),
        file_clues=_extract_file_clues(message_id, msg_type, content),
        frontmatter_extra=_direct_message_frontmatter(item, msg_type),
    )
    return _ParsedDirectMessage(record, create_time)


def _direct_message_frontmatter(item: Mapping[str, Any], msg_type: str) -> dict[str, str]:
    """提取 direct_message 记录专属 frontmatter。"""
    sender = _mapping(item.get("sender"))
    return {
        "message_id": _as_text(item.get("message_id")),
        "chat_id": _as_text(item.get("chat_id")),
        "root_id": _as_text(item.get("root_id")),
        "parent_id": _as_text(item.get("parent_id")),
        "sender_id": _as_text(sender.get("id")),
        "sender_id_type": _as_text(sender.get("id_type")),
        "msg_type": msg_type,
        "create_time": _as_text(item.get("create_time")),
        "update_time": _as_text(item.get("update_time")),
    }


def _request_params(context: _CollectContext, page_token: str) -> dict[str, Any]:
    """构造获取会话历史消息接口 query 参数。"""
    params: dict[str, Any] = {
        "container_id": context.chat_id,
        "container_id_type": _CONTAINER_ID_TYPE,
        "start_time": context.start_time,
        "end_time": context.end_time,
        "sort_type": context.sort_type,
        "page_size": context.page_size,
    }
    if page_token:
        params["page_token"] = page_token
    return params


def _response_items(response: FeishuUserResponse) -> tuple[Mapping[str, Any], ...]:
    """从统一响应中提取消息 item 列表。"""
    items = response.data.get("items")
    if not isinstance(items, list):
        return ()
    return tuple(dict(item) for item in items if isinstance(item, Mapping))


def _content_mapping(item: Mapping[str, Any]) -> dict[str, Any]:
    """解析消息 body.content，失败时返回空字典。"""
    body = _mapping(item.get("body"))
    content = body.get("content")
    if isinstance(content, Mapping):
        return dict(content)
    if not isinstance(content, str) or not content:
        return {}
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return {"text": content}
    return dict(parsed) if isinstance(parsed, Mapping) else {"text": content}


def _message_text(content: Mapping[str, Any], msg_type: str) -> str:
    """从 text/post/interactive 等消息内容中提取可检索文本。"""
    text = _as_text(content.get("text"))
    if text:
        return text
    title = _as_text(content.get("title"))
    post_text = _extract_post_text(content)
    values = [title, post_text]
    if msg_type in _FILE_MESSAGE_TYPES:
        values.extend(_file_text_values(content))
    return " ".join(value for value in values if value)


def _extract_post_text(content: Mapping[str, Any]) -> str:
    """展开飞书 post 消息 content 二维数组中的 text/a 元素。"""
    paragraphs = content.get("content")
    if not isinstance(paragraphs, list):
        return ""
    parts: list[str] = []
    for paragraph in paragraphs:
        _collect_post_paragraph_text(paragraph, parts)
    return " ".join(parts)


def _collect_post_paragraph_text(paragraph: Any, parts: list[str]) -> None:
    """收集一个 post 段落中的文本元素。"""
    if not isinstance(paragraph, list):
        return
    for element in paragraph:
        if not isinstance(element, Mapping):
            continue
        tag = _as_text(element.get("tag"))
        if tag in {"text", "a"} and _as_text(element.get("text")):
            parts.append(_as_text(element.get("text")))


def _extract_doc_links(content: Mapping[str, Any], text: str) -> tuple[AmbientDocLink, ...]:
    """从消息文本和富文本字段中提取飞书云文档链接。"""
    raw = " ".join([text, *_collect_strings(content)])
    links: list[AmbientDocLink] = []
    seen: set[str] = set()
    for match in _URL_PATTERN.findall(raw):
        url = _clean_url(match)
        if url in seen or not _is_feishu_doc_url(url):
            continue
        seen.add(url)
        links.append(AmbientDocLink(url=url, resource_type=_resource_type(url), token=_resource_token(url)))
    return tuple(links)


def _extract_file_clues(
    message_id: str,
    msg_type: str,
    content: Mapping[str, Any],
) -> tuple[AmbientFileClue, ...]:
    """从附件类消息中提取文件线索，不下载二进制内容。"""
    file_key = _pick_first(content, "file_key", "image_key", "media_key", "key")
    file_name = _pick_first(content, "file_name", "image_name", "name", "title")
    if msg_type not in _FILE_MESSAGE_TYPES and not file_key:
        return ()
    return (AmbientFileClue(message_id, msg_type, file_key, file_name),)


def _collect_strings(value: Any) -> list[str]:
    """递归收集 content 中的字符串字段，用于链接提取。"""
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        strings: list[str] = []
        for item in value.values():
            strings.extend(_collect_strings(item))
        return strings
    if isinstance(value, list):
        strings = []
        for item in value:
            strings.extend(_collect_strings(item))
        return strings
    return []


def _file_text_values(content: Mapping[str, Any]) -> list[str]:
    """提取文件类消息里可检索的名称字段。"""
    return [
        value
        for value in (
            _as_text(content.get("file_name")),
            _as_text(content.get("image_name")),
            _as_text(content.get("name")),
            _as_text(content.get("title")),
        )
        if value
    ]


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
    cursor: str,
    next_cursor: str,
    has_more: bool,
    next_page_token: str,
    detail: str,
) -> DirectMessageCollectResult:
    """构造 collector 对外结果对象。"""
    usage = guard.snapshot()
    return DirectMessageCollectResult(
        ok=ok,
        status=status,
        chat_id=context.chat_id,
        items_written=len(record_paths),
        record_paths=tuple(record_paths),
        cursor=cursor,
        next_cursor=next_cursor,
        has_more=has_more,
        next_page_token=next_page_token,
        sync_state_path=context.sync_state_ref,
        stopped_reason=usage.stopped_reason,
        detail=detail,
    )


def _summary(
    message_id: str,
    item: Mapping[str, Any],
    msg_type: str,
    text: str,
) -> str:
    """构造单条私信记录的人可读摘要。"""
    sender = _mapping(item.get("sender"))
    sender_id = _as_text(sender.get("id"))
    preview = _truncate(text, DIRECT_MESSAGE_PREVIEW_CHARS)
    return f"direct message {message_id} from {sender_id} type {msg_type}: {preview}"


def _is_feishu_doc_url(url: str) -> bool:
    """判断 URL 是否像飞书或 Lark 云文档链接。"""
    host = urlsplit(url).netloc.lower()
    if "feishu.cn" not in host and "larksuite.com" not in host:
        return False
    return any(f"/{marker}/" in urlsplit(url).path for marker in _DOC_PATH_MARKERS)


def _resource_type(url: str) -> str:
    """按 URL path 推断飞书资源类型。"""
    segments = _url_segments(url)
    for segment in segments:
        if segment in _DOC_PATH_MARKERS:
            return _DOC_PATH_MARKERS[segment]
    return "unknown"


def _resource_token(url: str) -> str:
    """从飞书 URL path 中提取资源 token。"""
    segments = _url_segments(url)
    for index, segment in enumerate(segments):
        if segment in _DOC_PATH_MARKERS and index + 1 < len(segments):
            return segments[index + 1]
    return segments[-1] if segments else ""


def _url_segments(url: str) -> list[str]:
    """返回 URL path 的非空片段。"""
    return [unquote(segment) for segment in urlsplit(url).path.split("/") if segment]


def _clean_url(url: str) -> str:
    """清理常见尾随标点，避免 token 带入句号或括号。"""
    return url.rstrip(".,，。)]}>")


def _mapping(value: Any) -> dict[str, Any]:
    """把不确定对象安全转换为 dict。"""
    return dict(value) if isinstance(value, Mapping) else {}


def _pick_first(content: Mapping[str, Any], *keys: str) -> str:
    """从 content 中返回第一个非空字符串字段。"""
    for key in keys:
        value = _as_text(content.get(key))
        if value:
            return value
    return ""


def _as_text(value: Any) -> str:
    """把值稳定转换为去空白字符串。"""
    if value is None:
        return ""
    return str(value).strip()


def _bounded_page_size(page_size: int) -> int:
    """把 page_size 限制在飞书接口第一版允许的安全范围内。"""
    try:
        value = int(page_size)
    except (TypeError, ValueError):
        value = DEFAULT_DIRECT_MESSAGE_PAGE_SIZE
    return max(1, min(value, DEFAULT_DIRECT_MESSAGE_PAGE_SIZE))


def _timestamp_text(value: int | str) -> str:
    """把秒级时间戳参数转换为字符串。"""
    text = str(value).strip()
    if not text:
        raise ValueError("start_time/end_time is required")
    return text


def _timestamp_ms_to_iso(value: str) -> str:
    """把飞书毫秒时间戳转换为 ISO-8601；失败时回退到当前时间。"""
    text = _as_text(value)
    if not text.isdigit():
        return _now_iso()
    seconds = int(text[:10])
    return datetime.fromtimestamp(seconds, timezone.utc).astimezone().isoformat(timespec="seconds")


def _next_cursor_from_ms(value: str) -> str:
    """把毫秒 create_time 推导为下一轮秒级 start_time。"""
    text = _as_text(value)
    if not text.isdigit():
        return ""
    return str(int(text) // 1000)


def _max_timestamp(left: str, right: str) -> str:
    """返回较大的毫秒时间戳字符串。"""
    if not right:
        return left
    if not left:
        return right
    return right if int(right) > int(left) else left


def _truncate(text: str, limit: int) -> str:
    """裁剪预览文本。"""
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _relative_path(root: Path, path: Path | str) -> str:
    """返回工作区相对路径。"""
    resolved = Path(path).resolve()
    try:
        return str(resolved.relative_to(root.resolve()))
    except ValueError:
        return str(resolved)


def _trace_id(chat_id: str, end_time: str) -> str:
    """构造请求 trace_id，便于审计日志定位。"""
    return "dmc_" + _safe_part(chat_id) + "_" + _safe_part(end_time)


def _safe_part(value: str) -> str:
    """把外部 ID 转换为安全 trace/file 片段。"""
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value)[:80]


def _require_chat_id(chat_id: str) -> None:
    """校验 collector 必须绑定明确 p2p chat_id。"""
    if not chat_id:
        raise ValueError("chat_id is required")


def _now_iso() -> str:
    """返回当前本地时区 ISO 时间字符串。"""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _self_test() -> None:
    """验证消息解析可生成 direct_message ambient_context 记录。"""
    item = {
        "message_id": "om_1",
        "create_time": "1778040000000",
        "chat_id": "oc_1",
        "sender": {"id": "ou_1", "id_type": "open_id"},
        "body": {"content": json.dumps({"text": "见 https://example.feishu.cn/docx/token"})},
        "msg_type": "text",
    }
    context = _CollectContext("oc_1", "1778039900", "1778040100", 50, _SORT_ASC, "")
    parsed = _parse_message(item, context, CollectorBudgetGuard(CollectorBudget(COLLECTOR_NAME)), "")
    assert parsed is not None
    assert parsed.record.record_id == "dm_om_1"
    assert parsed.record.doc_links[0].token == "token"


if __name__ == "__main__":
    _self_test()
    print("dutyflow feishu direct message collector self-test passed")
