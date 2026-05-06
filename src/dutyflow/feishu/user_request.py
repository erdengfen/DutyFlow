# 本文件负责封装飞书用户面 API 请求的统一鉴权、错误归一、日志和 raw 响应落盘。

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit
from uuid import uuid4

from dutyflow.feishu.user_token_provider import FeishuUserTokenProvider
from dutyflow.logging.audit_log import AuditLogger, build_audit_preview
from dutyflow.storage.file_store import FileStore
from dutyflow.storage.markdown_store import MarkdownDocument, MarkdownStore

# 关键开关：用户面 API 默认请求超时秒数，避免主动感知轮次长时间卡住。
DEFAULT_USER_REQUEST_TIMEOUT_SECONDS = 15.0
# 关键开关：用户面 API 瞬时失败最多重试 2 次，权限错误和 token 错误不走普通重试。
DEFAULT_USER_REQUEST_MAX_RETRIES = 2
# 关键开关：分页辅助默认最多拉 3 页，避免第一版主动感知误触发大规模全量拉取。
DEFAULT_USER_REQUEST_MAX_PAGES = 3
# 关键开关：raw 响应落盘最多保留 50000 字符，防止调试文件意外膨胀。
MAX_RAW_RESPONSE_CHARS = 50000
_TOKEN_INVALID_FEISHU_CODES = {99991663, 99991664, 99991668}
_PERMISSION_DENIED_FEISHU_CODES = {99991672, 99991673, 99991679}
_TRANSIENT_HTTP_STATUS = {408, 500, 502, 503, 504}
_SENSITIVE_KEY_MARKERS = ("token", "secret", "authorization", "app_secret")
_AUTHORIZATION_TEXT_PATTERN = re.compile(r"Bearer\s+[A-Za-z0-9._\-]+")


@dataclass(frozen=True)
class FeishuUserRequest:
    """描述一次飞书用户面 API 请求，不保存 Authorization 原文。"""

    method: str
    url: str
    params: Mapping[str, Any] | None = None
    json_body: Mapping[str, Any] | None = None
    timeout_seconds: float = DEFAULT_USER_REQUEST_TIMEOUT_SECONDS
    trace_id: str = ""
    collector_name: str = ""


@dataclass(frozen=True)
class FeishuUserResponse:
    """描述一次飞书用户面 API 响应的归一化结果。"""

    ok: bool
    status: str
    http_status: int
    feishu_code: int
    detail: str
    data: Mapping[str, Any]
    page_token: str
    has_more: bool
    raw_path: str


class FeishuUserRequestClient:
    """为用户面 collector 提供最小统一请求能力。

    该类只负责 owner 用户身份下的飞书 API 请求，不负责 bot/app 发送能力。
    """

    def __init__(
        self,
        token_provider: FeishuUserTokenProvider,
        project_root: Path,
        *,
        audit_logger: AuditLogger | None = None,
        raw_response_enabled: bool = False,
        max_retries: int = DEFAULT_USER_REQUEST_MAX_RETRIES,
    ) -> None:
        """绑定 token provider、工作区和日志组件。"""
        self.token_provider = token_provider
        self.project_root = project_root
        self.raw_response_enabled = raw_response_enabled
        self.max_retries = max(0, max_retries)
        self.markdown_store = MarkdownStore(FileStore(project_root))
        self.audit_logger = audit_logger or AuditLogger(
            self.markdown_store,
            Path("data/logs"),
        )

    def request(
        self,
        request: FeishuUserRequest,
        *,
        save_raw: bool = False,
    ) -> FeishuUserResponse:
        """执行一次请求，包含普通瞬时错误重试，但不主动强制刷新 token。"""
        prepared = _prepare_request(request)
        try:
            token = self.token_provider.get_token()
        except RuntimeError as exc:
            response = _token_error_response(str(exc))
            self._record_request_log(prepared, response)
            return response
        response = self._request_with_retries(prepared, token, save_raw)
        self._record_request_log(prepared, response)
        return response

    def request_with_token_retry(
        self,
        request: FeishuUserRequest,
        *,
        save_raw: bool = False,
    ) -> FeishuUserResponse:
        """遇到 401 或飞书 token 失效码时强制刷新一次后重试。"""
        first = self.request(request, save_raw=save_raw)
        if not _is_token_invalid_response(first):
            return first
        try:
            self.token_provider.force_refresh()
        except RuntimeError as exc:
            return _reauth_response(str(exc))
        second = self.request(request, save_raw=save_raw)
        return second

    def paged_request(
        self,
        request: FeishuUserRequest,
        *,
        max_pages: int = DEFAULT_USER_REQUEST_MAX_PAGES,
        page_token_field: str = "page_token",
        save_raw: bool = False,
    ) -> tuple[FeishuUserResponse, ...]:
        """按 page_token/has_more 拉取多页，并受最大页数限制。"""
        responses: list[FeishuUserResponse] = []
        next_token = ""
        limit = max(1, max_pages)
        for _page_index in range(limit):
            current = _with_page_token(request, page_token_field, next_token)
            response = self.request_with_token_retry(current, save_raw=save_raw)
            responses.append(response)
            if not response.ok or not response.has_more or not response.page_token:
                break
            next_token = response.page_token
        return tuple(responses)

    def _request_with_retries(
        self,
        request: FeishuUserRequest,
        token: str,
        save_raw: bool,
    ) -> FeishuUserResponse:
        """执行 HTTP 请求，并对超时、5xx、429 做有限重试。"""
        response = _transient_response("api_error", "request not executed")
        for attempt in range(self.max_retries + 1):
            response = self._send_once(request, token, save_raw)
            if not _should_retry_response(response):
                return response
            if attempt >= self.max_retries:
                return response
        return response

    def _send_once(
        self,
        request: FeishuUserRequest,
        token: str,
        save_raw: bool,
    ) -> FeishuUserResponse:
        """发送单次 HTTP 请求并归一化响应。"""
        import httpx

        headers = _build_headers(token)
        try:
            http_response = httpx.request(
                request.method,
                request.url,
                headers=headers,
                params=dict(request.params or {}),
                json=dict(request.json_body or {}) if request.json_body else None,
                timeout=request.timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            return _transient_response("timeout", str(exc))
        except httpx.RequestError as exc:
            return _transient_response("transient_error", str(exc))
        return self._build_response(request, http_response, save_raw)

    def _build_response(
        self,
        request: FeishuUserRequest,
        http_response: Any,
        save_raw: bool,
    ) -> FeishuUserResponse:
        """把 HTTP 响应解析成 FeishuUserResponse，并按需写 raw 文件。"""
        body = _read_json_body(http_response)
        raw_path = ""
        if save_raw or self.raw_response_enabled:
            raw_path = self._write_raw_response(request, http_response, body)
        if not isinstance(body, dict):
            return _invalid_response(http_response.status_code, raw_path)
        response = _response_from_body(http_response.status_code, body, raw_path)
        return response

    def _write_raw_response(
        self,
        request: FeishuUserRequest,
        http_response: Any,
        body: Any,
    ) -> str:
        """把脱敏后的 raw 响应写入 data/feishu/raw。"""
        path = _raw_response_path(request.trace_id)
        document = MarkdownDocument(
            frontmatter=_raw_frontmatter(request, http_response, body),
            body=_raw_body(request, body, http_response.text),
        )
        written = self.markdown_store.write_document(path, document)
        return str(written)

    def _record_request_log(
        self,
        request: FeishuUserRequest,
        response: FeishuUserResponse,
    ) -> None:
        """写入用户面请求审计日志，payload 不包含 Authorization。"""
        outcome = "success" if response.ok else "failed"
        payload = {
            "collector_name": request.collector_name,
            "method": request.method,
            "endpoint": _endpoint_without_query(request.url),
            "http_status": response.http_status,
            "feishu_code": response.feishu_code,
            "status": response.status,
            "raw_path": response.raw_path,
        }
        self.audit_logger.record_event(
            category="system",
            event_type="feishu_user_request",
            outcome=outcome,
            note=response.status,
            trace_id=request.trace_id,
            payload=payload,
        )


def _prepare_request(request: FeishuUserRequest) -> FeishuUserRequest:
    """补齐 trace_id、method、collector_name 和 timeout 默认值。"""
    trace_id = request.trace_id or "fur_" + uuid4().hex[:12]
    collector_name = request.collector_name or "unknown_collector"
    timeout = request.timeout_seconds or DEFAULT_USER_REQUEST_TIMEOUT_SECONDS
    return replace(
        request,
        method=request.method.upper(),
        timeout_seconds=timeout,
        trace_id=_safe_file_part(trace_id),
        collector_name=collector_name,
    )


def _build_headers(token: str) -> dict[str, str]:
    """构造飞书用户面 API 请求头。"""
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def _read_json_body(http_response: Any) -> Any:
    """读取 JSON 响应体，无法解析时返回 None。"""
    try:
        return http_response.json()
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def _response_from_body(
    http_status: int,
    body: Mapping[str, Any],
    raw_path: str,
) -> FeishuUserResponse:
    """按 HTTP 状态和飞书 code 归一化响应。"""
    if http_status != 200:
        return _http_error_response(http_status, body, raw_path)
    if "code" not in body:
        return _invalid_response(http_status, raw_path)
    code = _int_value(body.get("code"), -1)
    if code != 0:
        return _feishu_error_response(code, body, raw_path)
    data = _dict_value(body.get("data"))
    return FeishuUserResponse(
        ok=True,
        status="ok",
        http_status=http_status,
        feishu_code=0,
        detail="",
        data=data,
        page_token=_extract_page_token(data),
        has_more=bool(data.get("has_more")),
        raw_path=raw_path,
    )


def _http_error_response(
    http_status: int,
    body: Mapping[str, Any],
    raw_path: str,
) -> FeishuUserResponse:
    """把 HTTP 非 200 响应映射成稳定状态。"""
    if http_status == 401:
        status = "reauth_required"
    elif http_status == 403:
        status = "permission_denied"
    elif http_status == 404:
        status = "not_found"
    elif http_status == 429:
        status = "rate_limited"
    elif http_status in _TRANSIENT_HTTP_STATUS:
        status = "transient_error"
    else:
        status = "api_error"
    return _error_response(
        status,
        http_status,
        _int_value(body.get("code"), -1),
        body,
        raw_path,
    )


def _feishu_error_response(
    code: int,
    body: Mapping[str, Any],
    raw_path: str,
) -> FeishuUserResponse:
    """把飞书非零 code 映射成稳定状态。"""
    if code in _TOKEN_INVALID_FEISHU_CODES:
        status = "reauth_required"
    elif code in _PERMISSION_DENIED_FEISHU_CODES:
        status = "permission_denied"
    else:
        status = "api_error"
    return _error_response(status, 200, code, body, raw_path)


def _error_response(
    status: str,
    http_status: int,
    feishu_code: int,
    body: Mapping[str, Any],
    raw_path: str,
) -> FeishuUserResponse:
    """构造统一错误响应对象。"""
    return FeishuUserResponse(
        ok=False,
        status=status,
        http_status=http_status,
        feishu_code=feishu_code,
        detail=_detail_from_body(body),
        data={},
        page_token="",
        has_more=False,
        raw_path=raw_path,
    )


def _token_error_response(detail: str) -> FeishuUserResponse:
    """构造 token 缺失或需重新授权的响应。"""
    status = "token_missing" if "尚未完成" in detail else "reauth_required"
    return FeishuUserResponse(False, status, 0, -1, detail, {}, "", False, "")


def _reauth_response(detail: str) -> FeishuUserResponse:
    """构造强制刷新失败后的重新授权响应。"""
    return FeishuUserResponse(False, "reauth_required", 0, -1, detail, {}, "", False, "")


def _invalid_response(http_status: int, raw_path: str) -> FeishuUserResponse:
    """构造无效 JSON 或无效飞书结构响应。"""
    return FeishuUserResponse(
        False,
        "invalid_response",
        http_status,
        -1,
        "invalid json response",
        {},
        "",
        False,
        raw_path,
    )


def _transient_response(status: str, detail: str) -> FeishuUserResponse:
    """构造超时或瞬时网络错误响应。"""
    return FeishuUserResponse(False, status, 0, -1, detail, {}, "", False, "")


def _should_retry_response(response: FeishuUserResponse) -> bool:
    """判断响应是否适合普通重试。"""
    return response.status in {"timeout", "transient_error", "rate_limited"}


def _is_token_invalid_response(response: FeishuUserResponse) -> bool:
    """判断响应是否应该触发一次强制刷新后重试。"""
    return response.http_status == 401 or response.feishu_code in _TOKEN_INVALID_FEISHU_CODES


def _with_page_token(
    request: FeishuUserRequest,
    field_name: str,
    page_token: str,
) -> FeishuUserRequest:
    """把下一页 token 写入 params 或 json_body。"""
    if not page_token:
        return request
    if request.json_body is not None:
        body = dict(request.json_body)
        body[field_name] = page_token
        return replace(request, json_body=body)
    params = dict(request.params or {})
    params[field_name] = page_token
    return replace(request, params=params)


def _extract_page_token(data: Mapping[str, Any]) -> str:
    """从飞书分页 data 中提取下一页 token。"""
    return str(data.get("page_token") or data.get("next_page_token") or "")


def _dict_value(value: Any) -> dict[str, Any]:
    """把飞书 data 字段稳定转为 dict。"""
    return dict(value) if isinstance(value, Mapping) else {}


def _int_value(value: Any, default: int) -> int:
    """把飞书 code 字段稳定转为 int。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _detail_from_body(body: Mapping[str, Any]) -> str:
    """从飞书错误响应中提取人可读原因。"""
    detail = body.get("msg") or body.get("error_description") or body.get("error")
    if detail:
        return str(detail)
    return build_audit_preview(body, max_chars=300)


def _raw_response_path(trace_id: str) -> Path:
    """返回 raw 响应相对落盘路径。"""
    today = datetime.now(timezone.utc).date().isoformat()
    return Path("data/feishu/raw") / today / f"raw_{_safe_file_part(trace_id)}.md"


def _raw_frontmatter(
    request: FeishuUserRequest,
    http_response: Any,
    body: Any,
) -> dict[str, str]:
    """构造 raw 响应 Markdown frontmatter。"""
    return {
        "schema": "dutyflow.feishu_user_raw_response.v1",
        "id": "raw_" + _safe_file_part(request.trace_id),
        "trace_id": request.trace_id,
        "collector_name": request.collector_name,
        "method": request.method,
        "endpoint": _endpoint_without_query(request.url),
        "http_status": str(http_response.status_code),
        "feishu_code": str(
            _int_value(body.get("code"), -1) if isinstance(body, Mapping) else -1
        ),
        "created_at": _now_iso(),
    }


def _raw_body(request: FeishuUserRequest, body: Any, raw_text: str) -> str:
    """渲染脱敏 raw 响应正文。"""
    safe_body = _record_safe_value(body) if body is not None else _redact_text(raw_text)
    text = _render_value(safe_body)
    if len(text) > MAX_RAW_RESPONSE_CHARS:
        text = text[:MAX_RAW_RESPONSE_CHARS] + "\n...(truncated)"
    return (
        "# Feishu User Raw Response\n\n"
        "## Request\n\n"
        f"- collector_name: {request.collector_name}\n"
        f"- endpoint: {_endpoint_without_query(request.url)}\n\n"
        "## Response Body\n\n"
        "```json\n"
        f"{text}\n"
        "```\n"
    )


def _record_safe_value(value: Any) -> Any:
    """递归脱敏 dict/list/string 中的敏感字段和值。"""
    if isinstance(value, Mapping):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if _is_sensitive_key(key_text):
                redacted[key_text] = "[redacted]"
            else:
                redacted[key_text] = _record_safe_value(item)
        return redacted
    if isinstance(value, list):
        return [_record_safe_value(item) for item in value]
    if isinstance(value, str):
        return _redact_text(value)
    return value


def _redact_text(text: str) -> str:
    """对普通文本中的明显认证片段做保守脱敏。"""
    if not text:
        return ""
    return _AUTHORIZATION_TEXT_PATTERN.sub("Bearer [redacted]", text)


def _is_sensitive_key(key: str) -> bool:
    """判断字段名是否包含敏感信息标记。"""
    lowered = key.lower()
    return any(marker in lowered for marker in _SENSITIVE_KEY_MARKERS)


def _render_value(value: Any) -> str:
    """把 raw 内容稳定渲染为 JSON 文本。"""
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def _endpoint_without_query(url: str) -> str:
    """移除 query，仅保留接口端点用于日志和 raw frontmatter。"""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}{parts.path}"


def _safe_file_part(value: str) -> str:
    """把 trace_id 等外部字符串转成安全文件名片段。"""
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value)
    return safe[:80] or "unknown"


def _now_iso() -> str:
    """返回 UTC ISO 时间字符串。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _self_test() -> None:
    """验证 token 缺失时请求层返回 token_missing，不发起真实网络请求。"""
    import tempfile
    from unittest.mock import MagicMock

    with tempfile.TemporaryDirectory() as tmp:
        provider = MagicMock()
        provider.get_token.side_effect = RuntimeError("尚未完成 OAuth 授权")
        client = FeishuUserRequestClient(provider, Path(tmp))
        response = client.request(
            FeishuUserRequest(method="GET", url="https://open.feishu.cn/open-apis/test")
        )
        assert not response.ok
        assert response.status == "token_missing"


if __name__ == "__main__":
    _self_test()
    print("dutyflow feishu user request self-test passed")
