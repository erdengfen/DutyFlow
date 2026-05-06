# 本文件负责以 owner 用户身份读取飞书文档正文、云盘搜索和文件元信息。

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from dutyflow.feishu.oauth import FeishuOAuthManager
from dutyflow.feishu.user_client import FeishuUserClient
from dutyflow.feishu.user_request import FeishuUserResponse

_FEISHU_DOCX_INFO_URL = "https://open.feishu.cn/open-apis/docx/v1/documents/{doc_token}"
_FEISHU_DOCX_CONTENT_URL = (
    "https://open.feishu.cn/open-apis/docx/v1/documents/{doc_token}/raw_content"
)
_FEISHU_DRIVE_META_URL = "https://open.feishu.cn/open-apis/drive/v1/metas/batch_query"
_FEISHU_DRIVE_SEARCH_URL = "https://open.feishu.cn/open-apis/drive/v1/files/search"
# 单次搜索最多返回 20 条，防止结果列表撑大模型上下文。
_MAX_SEARCH_COUNT = 20


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
class DriveFileItem:
    """表示云盘搜索结果中的单个文件条目。"""

    token: str
    name: str
    file_type: str
    url: str
    owner_id: str
    modified_time: str


@dataclass(frozen=True)
class DriveSearchResult:
    """表示 search_drive() 的完整执行结果。"""

    ok: bool
    status: str  # "ok" | "token_missing" | "permission_denied" | "api_error"
    query: str
    files: tuple[DriveFileItem, ...]
    has_more: bool
    total: int
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

    不直接暴露 user_access_token，内部通过 FeishuUserClient 取得有效凭证并统一请求。
    所有方法均返回结果对象，不抛出异常，调用方通过 result.ok 判断成功与否。
    """

    def __init__(
        self,
        oauth_manager: FeishuOAuthManager,
        user_client: FeishuUserClient | None = None,
    ) -> None:
        """绑定 OAuth 管理器兼容旧入口，内部统一使用 FeishuUserClient。"""
        self.oauth_manager = oauth_manager
        self.user_client = user_client or FeishuUserClient.from_oauth_manager(oauth_manager)

    def read_doc(self, doc_token: str) -> DocReadResult:
        """读取飞书 docx 文档正文，title 为尽力获取（失败时返回空字符串）。

        user_access_token 不存在或无法刷新时返回 status="token_missing"。
        """
        now = _now_iso()
        try:
            content = _fetch_doc_content(doc_token, self.user_client)
        except _FeishuResourceError as exc:
            return DocReadResult(
                ok=False, status=exc.status, doc_token=doc_token,
                title="", content="", fetched_at=now, detail=exc.detail,
            )

        title = ""
        try:
            title = _fetch_doc_title(doc_token, self.user_client)
        except Exception:  # noqa: BLE001
            pass  # title 为尽力获取，不影响正文读取结果

        return DocReadResult(
            ok=True, status="ok", doc_token=doc_token,
            title=title, content=content, fetched_at=now, detail="",
        )

    def search_drive(self, query: str, count: int = 10) -> DriveSearchResult:
        """在用户云盘按关键词搜索文件，使用 user_access_token 确保只返回授权范围内内容。

        user_access_token 不存在或无法刷新时返回 status="token_missing"。
        """
        now = _now_iso()
        try:
            return _search_drive_files(self.user_client, query, count, now)
        except _FeishuResourceError as exc:
            return DriveSearchResult(
                ok=False, status=exc.status, query=query,
                files=(), has_more=False, total=0, fetched_at=now, detail=exc.detail,
            )

    def get_file_meta(self, file_token: str, file_type: str) -> FileMetaResult:
        """读取飞书云盘文件或文档的元信息（不读正文）。

        user_access_token 不存在或无法刷新时返回 status="token_missing"。
        """
        now = _now_iso()
        try:
            meta = _batch_query_single(file_token, file_type, self.user_client)
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


def _fetch_doc_content(doc_token: str, user_client: FeishuUserClient) -> str:
    """调用 docx raw_content 接口，返回文档纯文本正文。"""
    url = _FEISHU_DOCX_CONTENT_URL.format(doc_token=doc_token)
    response = user_client.get(
        url,
        timeout_seconds=15.0,
        collector_name="feishu_read_doc",
    )
    data = _data_or_raise(response, "docx 正文读取")
    return str(data.get("content", ""))


def _fetch_doc_title(doc_token: str, user_client: FeishuUserClient) -> str:
    """调用 docx 文档信息接口，返回文档标题。失败时由调用方决定如何处理。"""
    url = _FEISHU_DOCX_INFO_URL.format(doc_token=doc_token)
    response = user_client.get(
        url,
        timeout_seconds=10.0,
        collector_name="feishu_read_doc",
    )
    data = _data_or_raise(response, "docx 文档信息")
    doc = data.get("document")
    if not isinstance(doc, dict):
        return ""
    return str(doc.get("title", ""))


def _batch_query_single(
    file_token: str,
    file_type: str,
    user_client: FeishuUserClient,
) -> dict[str, Any]:
    """调用 drive batch_query 接口，返回单条文件元信息字典。"""
    body = {"request_docs": [{"doc_token": file_token, "doc_type": file_type}]}
    response = user_client.post(
        _FEISHU_DRIVE_META_URL,
        json_body=body,
        timeout_seconds=10.0,
        collector_name="feishu_get_file_meta",
    )
    data = _data_or_raise(response, "文件元信息查询")
    metas = data.get("metas") or []
    if not metas:
        failed = data.get("failed_list") or []
        detail = json.dumps(failed, ensure_ascii=False) if failed else "未返回元信息"
        raise _FeishuResourceError("api_error", f"文件元信息查询无结果：{detail}")
    return dict(metas[0]) if isinstance(metas[0], dict) else {}


def _data_or_raise(response: FeishuUserResponse, context: str) -> dict[str, Any]:
    """把统一请求响应转成资源层 data，失败时映射到资源层稳定 status。"""
    if response.ok:
        return dict(response.data)
    status = _resource_status(response.status)
    detail = response.detail or response.status
    raise _FeishuResourceError(status, f"飞书 {context} 失败：{detail}")


def _resource_status(status: str) -> str:
    """把请求层状态收束为资源工具已公开的稳定错误状态。"""
    if status in {"token_missing", "reauth_required"}:
        return "token_missing"
    if status in {"permission_denied", "not_found"}:
        return status
    return "api_error"


def _search_drive_files(
    user_client: FeishuUserClient,
    query: str,
    count: int,
    fetched_at: str,
) -> DriveSearchResult:
    """调用飞书 Drive 搜索接口，返回结构化搜索结果。"""
    body = {
        "search_key": query,
        "count": min(max(1, count), _MAX_SEARCH_COUNT),
        "offset": 0,
        # 飞书 /drive/v1/files/search docs_types 合法值：[doc,sheet,slide,bitable,mindnote,file]。
        # "docx"/"wiki" 不是此 API 的合法枚举值（"docx" 仅用于 docx content API），不能传入。
        "docs_types": ["doc", "sheet", "slide", "bitable", "mindnote", "file"],
    }
    response = user_client.post(
        _FEISHU_DRIVE_SEARCH_URL,
        json_body=body,
        timeout_seconds=15.0,
        collector_name="feishu_search_drive",
    )
    data = _data_or_raise(response, "云盘搜索")
    files_raw = data.get("files") or []
    files = tuple(
        DriveFileItem(
            token=str(item.get("token", "")),
            name=str(item.get("name", "")),
            file_type=str(item.get("type", "")),
            url=str(item.get("url", "")),
            owner_id=str(item.get("owner_id", "")),
            modified_time=str(item.get("modified_time", "")),
        )
        for item in files_raw
        if isinstance(item, dict)
    )
    return DriveSearchResult(
        ok=True,
        status="ok",
        query=query,
        files=files,
        has_more=bool(data.get("has_more")),
        total=int(data.get("total", len(files))),
        fetched_at=fetched_at,
        detail="",
    )


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

    search = client.search_drive("季报")
    assert not search.ok, "should fail with empty token"
    assert search.status == "token_missing", f"unexpected status: {search.status}"
    assert search.files == ()


if __name__ == "__main__":
    _self_test()
    print("dutyflow feishu user_resource self-test passed")
