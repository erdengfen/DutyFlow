# 本文件验证飞书用户 OAuth token provider 的刷新锁、健康状态和持久化行为。

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dutyflow.feishu.user_token_provider import FeishuUserTokenProvider  # noqa: E402


def _future_expires_at(hours: int = 2) -> str:
    """构造远期过期时间，表示当前 token 仍健康。"""
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat(timespec="seconds")


def _soon_expires_at() -> str:
    """构造即将过期时间，用于触发提前刷新。"""
    return (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat(timespec="seconds")


def _make_config(
    *,
    access_token: str = "u.old",
    refresh_token: str = "ur.old",
    expires_at: str = "",
) -> MagicMock:
    """构造 provider 测试所需的最小飞书配置。"""
    config = MagicMock()
    config.feishu_app_id = "app_test"
    config.feishu_app_secret = "sec_test"
    config.feishu_tenant_key = "tenant_test"
    config.feishu_owner_open_id = "ou_owner"
    config.feishu_owner_user_id = "uid_owner"
    config.feishu_owner_union_id = "union_owner"
    config.feishu_oauth_default_scopes = ["docx:document:readonly"]
    config.feishu_oauth_redirect_uri = "http://127.0.0.1:9768/feishu/oauth/callback"
    config.feishu_owner_user_access_token = access_token
    config.feishu_owner_user_refresh_token = refresh_token
    config.feishu_owner_user_token_expires_at = expires_at
    return config


class _FakeOAuthManager:
    """测试替身：记录刷新次数，并按需更新进程内 config。"""

    def __init__(
        self,
        config: MagicMock,
        *,
        delay_seconds: float = 0.0,
        error: Exception | None = None,
    ) -> None:
        """绑定测试配置和可选延迟、异常。"""
        self.config = config
        self.delay_seconds = delay_seconds
        self.error = error
        self.refresh_count = 0
        self._lock = threading.Lock()

    def refresh_token(self, refresh_token: str) -> dict[str, object]:
        """模拟刷新接口，并把新 token 写回 config。"""
        with self._lock:
            self.refresh_count += 1
        if self.delay_seconds:
            time.sleep(self.delay_seconds)
        if self.error:
            raise self.error
        token_data = {
            "access_token": "u.refreshed",
            "refresh_token": "ur.refreshed",
            "expires_in": 7140,
        }
        self.config.feishu_owner_user_access_token = "u.refreshed"
        self.config.feishu_owner_user_refresh_token = "ur.refreshed"
        self.config.feishu_owner_user_token_expires_at = _future_expires_at()
        return token_data


class TestFeishuUserTokenProvider(unittest.TestCase):
    """验证用户 token provider 的正常路径、刷新路径和失败状态。"""

    def test_valid_token_returns_without_refresh(self) -> None:
        config = _make_config(expires_at=_future_expires_at())
        fake_manager = _FakeOAuthManager(config)
        provider = FeishuUserTokenProvider(config, Path("/tmp"), fake_manager)

        token = provider.get_token()

        self.assertEqual(token, "u.old")
        self.assertEqual(fake_manager.refresh_count, 0)
        self.assertEqual(provider.health_snapshot().status, "valid")

    def test_near_expiry_triggers_refresh(self) -> None:
        config = _make_config(expires_at=_soon_expires_at())
        fake_manager = _FakeOAuthManager(config)
        provider = FeishuUserTokenProvider(config, Path("/tmp"), fake_manager)

        token = provider.get_token()

        self.assertEqual(token, "u.refreshed")
        self.assertEqual(fake_manager.refresh_count, 1)
        self.assertTrue(provider.health_snapshot().refreshed)

    def test_concurrent_refresh_only_runs_once(self) -> None:
        config = _make_config(expires_at="")
        fake_manager = _FakeOAuthManager(config, delay_seconds=0.05)
        provider = FeishuUserTokenProvider(config, Path("/tmp"), fake_manager)
        results: list[str] = []

        def _worker() -> None:
            results.append(provider.get_token())

        threads = [threading.Thread(target=_worker) for _ in range(5)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(results, ["u.refreshed"] * 5)
        self.assertEqual(fake_manager.refresh_count, 1)

    def test_force_refresh_updates_env_and_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".env").write_text("")
            config = _make_config(expires_at=_future_expires_at())
            provider = FeishuUserTokenProvider(config, root)
            app_resp = _make_app_token_resp()
            refresh_resp = _make_refresh_resp()

            with patch("httpx.post", side_effect=[app_resp, refresh_resp]):
                token = provider.force_refresh()

            env_text = (root / ".env").read_text()

        self.assertEqual(token, "u.new")
        self.assertEqual(config.feishu_owner_user_access_token, "u.new")
        self.assertEqual(config.feishu_owner_user_refresh_token, "ur.new")
        self.assertIn("DUTYFLOW_FEISHU_OWNER_USER_TOKEN_EXPIRES_AT=", env_text)
        self.assertIn("u.new", env_text)

    def test_refresh_failure_updates_health(self) -> None:
        config = _make_config(expires_at="")
        error = RuntimeError("refresh token expired")
        fake_manager = _FakeOAuthManager(config, error=error)
        provider = FeishuUserTokenProvider(config, Path("/tmp"), fake_manager)

        with self.assertRaises(RuntimeError):
            provider.get_token()

        snapshot = provider.health_snapshot()
        self.assertEqual(snapshot.status, "reauth_required")
        self.assertTrue(snapshot.reauth_required)
        self.assertIn("refresh token expired", snapshot.latest_error)

    def test_account_scope_excludes_token_values(self) -> None:
        config = _make_config(expires_at=_future_expires_at())
        provider = FeishuUserTokenProvider(config, Path("/tmp"))

        scope = provider.account_scope()

        self.assertEqual(scope["owner_user_id"], "uid_owner")
        self.assertNotIn("access_token", scope)
        self.assertNotIn("refresh_token", scope)


def _make_app_token_resp() -> MagicMock:
    """构造 app_access_token mock 响应。"""
    resp = MagicMock()
    resp.json.return_value = {"code": 0, "app_access_token": "at.fake"}
    resp.raise_for_status = MagicMock()
    return resp


def _make_refresh_resp() -> MagicMock:
    """构造 user token 刷新 mock 响应。"""
    resp = MagicMock()
    resp.json.return_value = {
        "code": 0,
        "data": {
            "access_token": "u.new",
            "refresh_token": "ur.new",
            "expires_in": 7140,
        },
    }
    resp.raise_for_status = MagicMock()
    return resp


def _self_test() -> None:
    """运行本文件所有单元测试。"""
    unittest.main(verbosity=2)


if __name__ == "__main__":
    _self_test()
