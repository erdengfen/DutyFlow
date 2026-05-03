# 本文件负责以 owner 用户身份读取飞书文档正文和文件元信息，
# 内部通过 FeishuOAuthManager.ensure_valid_token() 取得有效 token。

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from dutyflow.feishu.oauth import FeishuOAuthManager

_FEISHU_DOCX_INFO_URL = "https://open.feishu.cn/open-apis/docx/v1/documents/{doc_token}"
_FEISHU_DOCX_CONTENT_URL = (
    "https://open.feishu.cn/open-apis/docx/v1/documents/{doc_token}/raw_content"
)
_FEISHU_DRIVE_META_URL = "https://open.feishu.cn/open-apis/drive/v1/metas/batch_query"


@dataclass(frozen=True)
class DocReadResult:
    """表示 read_doc() 的完整结果，包含内容数据或错误状态。"""

    ok: bool
    status: str  # "ok" | "token_missing" | "permission_denied" | "not_found" | "api_error"
    doc_token: str
    title: str
    content: str
    fetched_at: str
    detail: str


@dataclass(frozen=True)
class FileMetaResult:
    """表示 get_file_meta() 的完整结果，包含元信息字段或错误状态。"""

    ok: bool
    status: str  # "ok" | "token_missing" | "permission_denied" | "not_found" | "api_error"
    file_token: str
    file_type: str
    title: str
    owner_id: str
    create_time: str
    edit_time: str
    fetched_at: str
    detail: str


class FeishuUserResourceClient:
    """以 owner 用户身份读取飞书文档正文和文件元信息。

    不直接暴露 user_access_token，内部通过 ensure_valid_token() 取得有效凭证。
    所有方法均返回结果对象，不抛出异常，调用方通过 result.ok 判断成功与否。
    """

    def __init__(self, oauth_manager: FeishuOAuthManager) -> None:
        """绑定 OAuth 管理器，用于透明的 token 有效性检查和刷新。"""
        self.oauth_manager = oauth_manager

    def read_doc(self, doc_token: str) -> DocReadResult:
        """读取飞书 docx 文档正文，title 为尽力获取（失败时返回空字符串）。

        user_access_token 不存在或无法刷新时返回 status="token_missing"。
        """
        now = _now_iso()
        try:
            token = self.oauth_manager.ensure_valid_token()
        except RuntimeError as exc:
            return DocReadResult(
                ok=False, status="token_missing", doc_token=doc_token,
                title="", content="", fetched_at=now, detail=str(exc),
            )

        try:
            content = _fetch_doc_content(doc_token, token)
        except _FeishuResourceError as exc:
            return DocReadResult(
                ok=False, status=exc.status, doc_token=doc_token,
                title="", content="", fetched_at=now, detail=exc.detail,
            )

        title = ""
        try:
            title = _fetch_doc_title(doc_token, token)
        except Exception:  # noqa: BLE001
            pass  # title 为尽力获取，不影响正文读取结果

        return DocReadResult(
            ok=True, status="ok", doc_token=doc_token,
            title=title, content=content, fetched_at=now, detail="",
        )

    def get_file_meta(self, file_token: str, file_type: str) -> FileMetaResult:
        """读取飞书云盘文件或文档的元信息（不读正文）。

        user_access_token 不存在或无法刷新时返回 status="token_missing"。
        """
        now = _now_iso()
        try:
            token = self.oauth_manager.ensure_valid_token()
        except RuntimeError as exc:
            return FileMetaResult(
                ok=False, status="token_missing", file_token=file_token,
                file_type=file_type, title="", owner_id="", create_time="",
                edit_time="", fetched_at=now, detail=str(exc),
            )

        try:
            meta = _batch_query_single(file_token, file_type, token)
        except _FeishuResourceError as exc:
            return FileMetaResult(
                ok=False, status=exc.status, file_token=file_token,
                file_type=file_type, title="", owner_id="", create_time="",
                edit_time="", fetched_at=now, detail=exc.detail,
            )

        return FileMetaResult(
            ok=True, status="ok",
            file_token=file_token, file_type=file_type,
            title=str(meta.get("title", "")),
            owner_id=str(meta.get("owner_id", "")),
            create_time=str(meta.get("create_time", "")),
            edit_time=str(meta.get("latest_modify_time", "")),
            fetched_at=now, detail="",
        )


class _FeishuResourceError(Exception):
    """内部错误载体，携带语义化 status 和人可读 detail，不对外暴露。"""

    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _fetch_doc_content(doc_token: str, user_access_token: str) -> str:
    """调用 docx raw_content 接口，返回文档纯文本正文。"""
    import httpx

    url = _FEISHU_DOCX_CONTENT_URL.format(doc_token=doc_token)
    headers = {"Authorization": f"Bearer {user_access_token}"}
    resp = httpx.get(url, headers=headers, timeout=15.0)
    _check_http_status(resp)
    data = _parse_resource_response(resp.json(), "docx 正文读取")
    return str(data.get("content", ""))


def _fetch_doc_title(doc_token: str, user_access_token: str) -> str:
    """调用 docx 文档信息接口，返回文档标题。失败时由调用方决定如何处理。"""
    import httpx

    url = _FEISHU_DOCX_INFO_URL.format(doc_token=doc_token)
    headers = {"Authorization": f"Bearer {user_access_token}"}
    resp = httpx.get(url, headers=headers, timeout=10.0)
    _check_http_status(resp)
    data = _parse_resource_response(resp.json(), "docx 文档信息")
    doc = data.get("document")
    if not isinstance(doc, dict):
        return ""
    return str(doc.get("title", ""))


def _batch_query_single(
    file_token: str,
    file_type: str,
    user_access_token: str,
) -> dict[str, Any]:
    """调用 drive batch_query 接口，返回单条文件元信息字典。"""
    import httpx

    headers = {
        "Authorization": f"Bearer {user_access_token}",
        "Content-Type": "application/json",
    }
    body = {"request_docs": [{"doc_token": file_token, "doc_type": file_type}]}
    resp = httpx.post(_FEISHU_DRIVE_META_URL, headers=headers, json=body, timeout=10.0)
    _check_http_status(resp)
    data = _parse_resource_response(resp.json(), "文件元信息查询")
    metas = data.get("metas") or []
    if not metas:
        failed = data.get("failed_list") or []
        detail = json.dumps(failed, ensure_ascii=False) if failed else "未返回元信息"
        raise _FeishuResourceError("api_error", f"文件元信息查询无结果：{detail}")
    return dict(metas[0]) if isinstance(metas[0], dict) else {}


def _check_http_status(resp: Any) -> None:
    """把 HTTP 4xx/5xx 转成语义化 _FeishuResourceError，200 直接返回。"""
    code = resp.status_code
    if code == 200:
        return
    if code in {401, 403}:
        raise _FeishuResourceError("permission_denied", f"HTTP {code}：无权访问此资源")
    if code == 404:
        raise _FeishuResourceError("not_found", f"HTTP {code}：资源不存在")
    raise _FeishuResourceError("api_error", f"HTTP {code}：{resp.text[:300]}")


def _parse_resource_response(resp_body: dict[str, Any], context: str) -> dict[str, Any]:
    """解析飞书 API 统一响应格式，非零 code 转为 _FeishuResourceError。"""
    if resp_body.get("code") != 0:
        detail = resp_body.get("msg") or json.dumps(resp_body, ensure_ascii=False)
        raise _FeishuResourceError("api_error", f"飞书 {context} 失败：{detail}")
    return dict(resp_body.get("data") or {})


def _now_iso() -> str:
    """返回当前 UTC 时间的 ISO-8601 字符串。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _self_test() -> None:
    """验证 token_missing 路径在无真实网络时可正确返回。"""
    from unittest.mock import MagicMock

    config = MagicMock()
    config.feishu_app_id = "app_test"
    config.feishu_app_secret = "sec_test"
    config.feishu_owner_user_access_token = ""
    config.feishu_owner_user_refresh_token = ""
    config.feishu_owner_user_token_expires_at = ""
    config.feishu_oauth_redirect_uri = "http://127.0.0.1:9768/feishu/oauth/callback"
    config.feishu_oauth_default_scopes = ["docx:document:readonly"]

    from pathlib import Path
    from dutyflow.feishu.oauth import FeishuOAuthManager

    manager = FeishuOAuthManager(config, Path("/tmp"))
    client = FeishuUserResourceClient(manager)

    result = client.read_doc("doxcnXXXXX")
    assert not result.ok, "should fail with empty token"
    assert result.status == "token_missing", f"unexpected status: {result.status}"

    meta = client.get_file_meta("boxcnXXXXX", "file")
    assert not meta.ok, "should fail with empty token"
    assert meta.status == "token_missing", f"unexpected status: {meta.status}"


if __name__ == "__main__":
    _self_test()
    print("dutyflow feishu user_resource self-test passed")
