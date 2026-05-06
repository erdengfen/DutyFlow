# 本文件负责封装飞书 owner 用户身份下的 API client，供用户面 collector 和资源读取复用。

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from dutyflow.feishu.oauth import FeishuOAuthManager
from dutyflow.feishu.user_request import (
    DEFAULT_USER_REQUEST_TIMEOUT_SECONDS,
    FeishuUserRequest,
    FeishuUserRequestClient,
    FeishuUserResponse,
)
from dutyflow.feishu.user_token_provider import FeishuUserTokenProvider
from dutyflow.logging.audit_log import AuditLogger


class FeishuUserClient:
    """代表本地 owner 用户身份发起飞书用户面 API 请求。

    该类不提供 bot/app 发送消息能力，只收束用户 token、统一请求和账号边界信息。
    """

    def __init__(
        self,
        token_provider: FeishuUserTokenProvider,
        request_client: FeishuUserRequestClient,
    ) -> None:
        """绑定 token provider 和统一请求层。"""
        self.token_provider = token_provider
        self.request_client = request_client

    @classmethod
    def from_oauth_manager(
        cls,
        oauth_manager: FeishuOAuthManager,
        *,
        audit_logger: AuditLogger | None = None,
        raw_response_enabled: bool = False,
    ) -> "FeishuUserClient":
        """兼容现有工具入口，从 FeishuOAuthManager 构造用户面 client。"""
        token_provider = FeishuUserTokenProvider(
            oauth_manager.config,
            oauth_manager.project_root,
            oauth_manager,
        )
        request_client = FeishuUserRequestClient(
            token_provider,
            oauth_manager.project_root,
            audit_logger=audit_logger,
            raw_response_enabled=raw_response_enabled,
        )
        return cls(token_provider, request_client)

    def get(
        self,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        timeout_seconds: float = DEFAULT_USER_REQUEST_TIMEOUT_SECONDS,
        trace_id: str = "",
        collector_name: str = "",
        save_raw: bool = False,
    ) -> FeishuUserResponse:
        """以 owner 用户身份发起 GET 请求，遇到 token 失效时刷新一次。"""
        request = FeishuUserRequest(
            method="GET",
            url=url,
            params=params,
            timeout_seconds=timeout_seconds,
            trace_id=trace_id,
            collector_name=collector_name,
        )
        return self.request_client.request_with_token_retry(request, save_raw=save_raw)

    def post(
        self,
        url: str,
        *,
        json_body: Mapping[str, Any] | None = None,
        params: Mapping[str, Any] | None = None,
        timeout_seconds: float = DEFAULT_USER_REQUEST_TIMEOUT_SECONDS,
        trace_id: str = "",
        collector_name: str = "",
        save_raw: bool = False,
    ) -> FeishuUserResponse:
        """以 owner 用户身份发起 POST 请求，遇到 token 失效时刷新一次。"""
        request = FeishuUserRequest(
            method="POST",
            url=url,
            params=params,
            json_body=json_body,
            timeout_seconds=timeout_seconds,
            trace_id=trace_id,
            collector_name=collector_name,
        )
        return self.request_client.request_with_token_retry(request, save_raw=save_raw)

    def paged_request(
        self,
        request: FeishuUserRequest,
        *,
        max_pages: int,
        page_token_field: str = "page_token",
        save_raw: bool = False,
    ) -> tuple[FeishuUserResponse, ...]:
        """透传统一请求层分页辅助，供后续 collector 复用。"""
        return self.request_client.paged_request(
            request,
            max_pages=max_pages,
            page_token_field=page_token_field,
            save_raw=save_raw,
        )

    def account_scope(self) -> dict[str, Any]:
        """返回当前用户面请求代表的非敏感账号边界。"""
        return self.token_provider.account_scope()

    def health_snapshot(self) -> Any:
        """返回当前用户 token 健康状态快照。"""
        return self.token_provider.health_snapshot()


def _self_test() -> None:
    """验证 token 缺失时用户面 client 返回 token_missing 响应。"""
    import tempfile
    from unittest.mock import MagicMock

    with tempfile.TemporaryDirectory() as tmp:
        token_provider = MagicMock()
        token_provider.get_token.side_effect = RuntimeError("尚未完成 OAuth 授权")
        request_client = FeishuUserRequestClient(token_provider, Path(tmp))
        client = FeishuUserClient(token_provider, request_client)
        result = client.get("https://open.feishu.cn/open-apis/test")
        assert not result.ok
        assert result.status == "token_missing"


if __name__ == "__main__":
    _self_test()
    print("dutyflow feishu user client self-test passed")
