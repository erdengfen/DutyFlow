# 本文件负责封装飞书 owner 用户 OAuth token 的统一读取、刷新和健康状态观测。

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dutyflow.config.env import EnvConfig
from dutyflow.feishu.oauth import FeishuOAuthManager, _token_needs_refresh

_REFRESH_LOCK = threading.Lock()


@dataclass(frozen=True)
class FeishuUserTokenHealth:
    """描述用户 token 当前健康状态，不包含任何 token 原文。"""

    status: str
    valid: bool
    refreshed: bool
    reauth_required: bool
    latest_error: str
    updated_at: str


class FeishuUserTokenProvider:
    """为用户面 collector 提供单一 user_access_token 入口。

    该类只代表本地 owner 用户身份，不负责 bot/app 身份请求，也不直接调用资源 API。
    """

    def __init__(
        self,
        config: EnvConfig,
        project_root: Path,
        oauth_manager: FeishuOAuthManager | None = None,
    ) -> None:
        """绑定配置、项目根目录和可替换的 OAuth 管理器。"""
        self.config = config
        self.project_root = project_root
        self.oauth_manager = oauth_manager or FeishuOAuthManager(config, project_root)
        self._health = _build_health("unknown", False, False, False, "")

    def get_token(self) -> str:
        """返回当前有效 user_access_token，必要时在全局刷新锁内刷新。"""
        token = self._current_access_token()
        if not token:
            self._set_health("reauth_required", False, False, True, "尚未完成 OAuth 授权")
            raise RuntimeError("尚未完成 OAuth 授权，请向 Bot 发送 /oauth 完成授权。")
        if not self._needs_refresh():
            self._set_health("valid", True, False, False, "")
            return token
        with _REFRESH_LOCK:
            token = self._current_access_token()
            if token and not self._needs_refresh():
                self._set_health("valid", True, False, False, "")
                return token
            return self._refresh_locked()

    def force_refresh(self) -> str:
        """强制刷新 token，供请求层在 401 或 token 失效码后单次重试使用。"""
        with _REFRESH_LOCK:
            return self._refresh_locked()

    def account_scope(self) -> dict[str, Any]:
        """返回当前用户面请求的非敏感身份边界和授权范围。"""
        return {
            "app_id": self.config.feishu_app_id,
            "tenant_key": self.config.feishu_tenant_key,
            "owner_open_id": self.config.feishu_owner_open_id,
            "owner_user_id": self.config.feishu_owner_user_id,
            "owner_union_id": self.config.feishu_owner_union_id,
            "scopes": list(self.config.feishu_oauth_default_scopes),
        }

    def health_snapshot(self) -> FeishuUserTokenHealth:
        """返回最近一次 token 检查或刷新结果，供日志和调试查看。"""
        return self._health

    def _current_access_token(self) -> str:
        """读取当前进程内 config 中的 user_access_token。"""
        return self.config.feishu_owner_user_access_token

    def _needs_refresh(self) -> bool:
        """判断当前 token 是否缺失、不可解析或临近过期。"""
        return _token_needs_refresh(self.config.feishu_owner_user_token_expires_at)

    def _refresh_locked(self) -> str:
        """在调用方已持有刷新锁时执行刷新，并把失败归一到健康状态。"""
        refresh_token = self.config.feishu_owner_user_refresh_token
        if not refresh_token:
            self._set_health("reauth_required", False, False, True, "缺少 refresh_token")
            raise RuntimeError("user_access_token 已过期且无可用 refresh_token，请重新 /oauth。")
        try:
            token_data = self.oauth_manager.refresh_token(refresh_token)
        except Exception as exc:  # noqa: BLE001
            self._record_refresh_exception(exc)
            raise
        new_token = token_data.get("access_token") or token_data.get("user_access_token", "")
        if not new_token:
            self._set_health("refresh_failed", False, False, False, "刷新结果缺少 access_token")
            raise RuntimeError("token 刷新成功但未返回新 access_token，请重新 /oauth 授权。")
        self._set_health("refreshed", True, True, False, "")
        return str(new_token)

    def _record_refresh_exception(self, exc: Exception) -> None:
        """记录刷新异常的健康状态，避免日志泄露 token 原文。"""
        detail = _safe_error_message(str(exc), self.config)
        if _looks_reauth_required(detail):
            self._set_health("reauth_required", False, False, True, detail)
            return
        self._set_health("refresh_failed", False, False, False, detail)

    def _set_health(
        self,
        status: str,
        valid: bool,
        refreshed: bool,
        reauth_required: bool,
        latest_error: str,
    ) -> None:
        """用一次完整快照替换最近健康状态，方便外部无锁读取。"""
        self._health = _build_health(
            status,
            valid,
            refreshed,
            reauth_required,
            latest_error,
        )


def _build_health(
    status: str,
    valid: bool,
    refreshed: bool,
    reauth_required: bool,
    latest_error: str,
) -> FeishuUserTokenHealth:
    """构造包含更新时间的健康状态对象。"""
    return FeishuUserTokenHealth(
        status=status,
        valid=valid,
        refreshed=refreshed,
        reauth_required=reauth_required,
        latest_error=latest_error,
        updated_at=_now_iso(),
    )


def _looks_reauth_required(detail: str) -> bool:
    """根据错误文本识别需要重新 OAuth 的刷新失败。"""
    normalized = detail.lower()
    keywords = ("refresh token expired", "invalid_grant", "reauth", "重新")
    return any(keyword in normalized for keyword in keywords)


def _safe_error_message(message: str, config: EnvConfig) -> str:
    """移除错误文本中可能出现的敏感 token，保留可定位原因。"""
    safe_message = message
    sensitive_values = (
        config.feishu_owner_user_access_token,
        config.feishu_owner_user_refresh_token,
        config.feishu_app_secret,
    )
    for value in sensitive_values:
        if value:
            safe_message = safe_message.replace(value, "[redacted]")
    return safe_message


def _now_iso() -> str:
    """返回 UTC ISO 时间字符串，用于健康状态更新时间。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _self_test() -> None:
    """验证有效 token 可直接返回且健康状态不包含 token。"""
    from unittest.mock import MagicMock

    config = MagicMock()
    config.feishu_owner_user_access_token = "u.valid"
    config.feishu_owner_user_refresh_token = "ur.valid"
    config.feishu_owner_user_token_expires_at = "2999-01-01T00:00:00+00:00"
    config.feishu_app_id = "cli_app"
    config.feishu_app_secret = "sec"
    config.feishu_tenant_key = "tenant"
    config.feishu_owner_open_id = "ou"
    config.feishu_owner_user_id = "uid"
    config.feishu_owner_union_id = "union"
    config.feishu_oauth_default_scopes = ["docx:document:readonly"]

    provider = FeishuUserTokenProvider(config, Path("/tmp"))
    assert provider.get_token() == "u.valid"
    snapshot = provider.health_snapshot()
    assert snapshot.status == "valid"
    assert "u.valid" not in str(snapshot)


if __name__ == "__main__":
    _self_test()
    print("dutyflow feishu user token provider self-test passed")
