# 本文件验证飞书 OAuth 授权流程：URL 构造、token 换取、用户信息补全、
# env 持久化、callback server state 校验和 runtime 指令处理。

from pathlib import Path
import json
import sys
import threading
import time
import unittest
from unittest.mock import MagicMock, patch
import urllib.request

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dutyflow.feishu.oauth import (  # noqa: E402
    FeishuOAuthManager,
    OAUTH_CALLBACK_PORT,
    _compute_expires_at,
    _build_basic_credentials,
    _parse_feishu_response,
    _token_needs_refresh,
)
from dutyflow.feishu.events import FeishuEventAdapter  # noqa: E402


def _make_config(
    *,
    redirect_uri: str = "http://127.0.0.1:9768/feishu/oauth/callback",
    scopes: list[str] | None = None,
    app_id: str = "app_test",
    app_secret: str = "sec_test",
) -> MagicMock:
    config = MagicMock()
    config.feishu_app_id = app_id
    config.feishu_app_secret = app_secret
    config.feishu_oauth_redirect_uri = redirect_uri
    config.feishu_oauth_default_scopes = scopes or ["docx:document:readonly"]
    return config


class TestBuildAuthorizeUrl(unittest.TestCase):
    """验证 OAuth 授权 URL 构造的正确性。"""

    def _manager(self) -> FeishuOAuthManager:
        return FeishuOAuthManager(_make_config(), Path("/tmp"))

    def test_url_starts_with_feishu_authorize_endpoint(self) -> None:
        url = self._manager().build_authorize_url("st1")
        self.assertTrue(url.startswith("https://open.feishu.cn/open-apis/authen/v1/authorize"))

    def test_url_contains_app_id(self) -> None:
        url = self._manager().build_authorize_url("st1")
        self.assertIn("app_id=app_test", url)

    def test_url_contains_state(self) -> None:
        url = self._manager().build_authorize_url("my_state_xyz")
        self.assertIn("state=my_state_xyz", url)

    def test_url_contains_redirect_uri(self) -> None:
        url = self._manager().build_authorize_url("st1")
        self.assertIn("redirect_uri=", url)

    def test_url_contains_scope(self) -> None:
        url = self._manager().build_authorize_url("st1")
        self.assertIn("scope=", url)

    def test_multiple_scopes_joined(self) -> None:
        config = _make_config(scopes=["scope_a", "scope_b"])
        manager = FeishuOAuthManager(config, Path("/tmp"))
        url = manager.build_authorize_url("st1")
        self.assertIn("scope=scope_a", url)


class TestComputeExpiresAt(unittest.TestCase):
    """验证过期时间计算辅助函数。"""

    def test_positive_seconds_returns_iso_utc_string(self) -> None:
        result = _compute_expires_at(3600)
        self.assertIn("T", result)
        self.assertTrue(result.endswith("+00:00"))

    def test_zero_returns_empty_string(self) -> None:
        self.assertEqual(_compute_expires_at(0), "")

    def test_negative_returns_empty_string(self) -> None:
        self.assertEqual(_compute_expires_at(-1), "")


class TestBuildBasicCredentials(unittest.TestCase):
    """验证 HTTP Basic 认证凭据编码。"""

    def test_base64_encodes_app_id_and_secret(self) -> None:
        import base64
        creds = _build_basic_credentials("id1", "sec1")
        decoded = base64.b64decode(creds).decode()
        self.assertEqual(decoded, "id1:sec1")


class TestParseFeishuResponse(unittest.TestCase):
    """验证飞书 API 响应解析和错误处理。"""

    def test_code_zero_returns_data(self) -> None:
        resp = {"code": 0, "data": {"access_token": "tok"}}
        data = _parse_feishu_response(resp, "test")
        self.assertEqual(data["access_token"], "tok")

    def test_nonzero_code_raises_runtime_error(self) -> None:
        resp = {"code": 99200, "msg": "invalid code"}
        with self.assertRaises(RuntimeError) as ctx:
            _parse_feishu_response(resp, "token 换取")
        self.assertIn("token 换取", str(ctx.exception))

    def test_nonzero_code_with_empty_msg_shows_full_body(self) -> None:
        resp = {"code": 99991663, "msg": ""}
        with self.assertRaises(RuntimeError) as ctx:
            _parse_feishu_response(resp, "token 换取")
        self.assertIn("99991663", str(ctx.exception))

    def test_oauth_error_format_raises_with_description(self) -> None:
        resp = {"error": "invalid_grant", "error_description": "code has expired"}
        with self.assertRaises(RuntimeError) as ctx:
            _parse_feishu_response(resp, "token 换取")
        self.assertIn("code has expired", str(ctx.exception))

    def test_oauth_error_format_without_description(self) -> None:
        resp = {"error": "invalid_client"}
        with self.assertRaises(RuntimeError) as ctx:
            _parse_feishu_response(resp, "token 换取")
        self.assertIn("invalid_client", str(ctx.exception))

    def test_missing_data_returns_empty_dict(self) -> None:
        resp = {"code": 0}
        data = _parse_feishu_response(resp, "test")
        self.assertEqual(data, {})


class TestExchangeCode(unittest.TestCase):
    """验证 code → token 换取逻辑（mock HTTP）。"""

    def _make_app_token_resp(self) -> MagicMock:
        """构造 app_access_token 接口的 mock 响应。"""
        resp = MagicMock()
        resp.json.return_value = {"code": 0, "app_access_token": "at.fake_app_token"}
        resp.raise_for_status = MagicMock()
        return resp

    def test_successful_exchange_returns_token_data(self) -> None:
        manager = FeishuOAuthManager(_make_config(), Path("/tmp"))
        token_resp = MagicMock()
        token_resp.json.return_value = {
            "code": 0,
            "data": {
                "access_token": "u.tok123",
                "refresh_token": "ref456",
                "expires_in": 7140,
            },
        }
        token_resp.raise_for_status = MagicMock()
        with patch("httpx.post", side_effect=[self._make_app_token_resp(), token_resp]):
            data = manager.exchange_code("code_abc")
        self.assertEqual(data["access_token"], "u.tok123")
        self.assertEqual(data["refresh_token"], "ref456")

    def test_feishu_error_code_raises(self) -> None:
        manager = FeishuOAuthManager(_make_config(), Path("/tmp"))
        error_resp = MagicMock()
        error_resp.json.return_value = {"code": 99201, "msg": "expired code"}
        error_resp.raise_for_status = MagicMock()
        with patch("httpx.post", side_effect=[self._make_app_token_resp(), error_resp]):
            with self.assertRaises(RuntimeError):
                manager.exchange_code("bad_code")


class TestFetchUserInfo(unittest.TestCase):
    """验证用户信息查询逻辑（mock HTTP）。"""

    def test_successful_query_returns_user_data(self) -> None:
        manager = FeishuOAuthManager(_make_config(), Path("/tmp"))
        fake_resp = MagicMock()
        fake_resp.json.return_value = {
            "code": 0,
            "data": {
                "user_id": "uid_001",
                "union_id": "unid_001",
                "name": "Test User",
            },
        }
        fake_resp.raise_for_status = MagicMock()
        with patch("httpx.get", return_value=fake_resp):
            data = manager.fetch_user_info("u.tok123")
        self.assertEqual(data["user_id"], "uid_001")
        self.assertEqual(data["union_id"], "unid_001")


class TestPersistTokenResult(unittest.TestCase):
    """验证 token 和用户身份字段写入 .env 的持久化逻辑。"""

    def test_saved_keys_include_required_fields(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".env").write_text("DUTYFLOW_FEISHU_OWNER_USER_ACCESS_TOKEN=\n")
            manager = FeishuOAuthManager(_make_config(), root)
            token_data = {
                "access_token": "u.tok",
                "refresh_token": "ref",
                "expires_in": 7140,
            }
            user_info = {"user_id": "uid_1", "union_id": "unid_1"}
            saved = manager.persist_token_result(token_data, user_info)
            self.assertIn("DUTYFLOW_FEISHU_OWNER_USER_ACCESS_TOKEN", saved)
            self.assertIn("DUTYFLOW_FEISHU_OWNER_USER_REFRESH_TOKEN", saved)
            self.assertIn("DUTYFLOW_FEISHU_OWNER_USER_ID", saved)

    def test_env_file_written_with_token_value(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".env").write_text("")
            manager = FeishuOAuthManager(_make_config(), root)
            token_data = {"access_token": "u.tok_xyz", "refresh_token": "ref", "expires_in": 100}
            manager.persist_token_result(token_data, {"user_id": "u1", "union_id": "un1"})
            env_text = (root / ".env").read_text()
            self.assertIn("u.tok_xyz", env_text)


class TestCallbackServerStateValidation(unittest.TestCase):
    """验证 callback server 在收到错误 state 时不提取 code，正确 state 时返回 code。"""

    def _hit_callback(self, path: str, delay: float = 0.05) -> None:
        """在后台线程向 callback server 发送 GET 请求。"""
        def _send() -> None:
            time.sleep(delay)
            try:
                urllib.request.urlopen(
                    f"http://127.0.0.1:{OAUTH_CALLBACK_PORT}{path}", timeout=3
                )
            except Exception:
                pass

        threading.Thread(target=_send, daemon=True).start()

    def test_correct_state_returns_code(self) -> None:
        self._hit_callback("/feishu/oauth/callback?code=abc123&state=good_state")
        manager = FeishuOAuthManager(_make_config(), Path("/tmp"))
        code = manager.start_callback_server("good_state", timeout=5.0)
        self.assertEqual(code, "abc123")

    def test_wrong_state_causes_timeout(self) -> None:
        self._hit_callback("/feishu/oauth/callback?code=abc123&state=wrong_state")
        manager = FeishuOAuthManager(_make_config(), Path("/tmp"))
        with self.assertRaises(TimeoutError):
            manager.start_callback_server("expected_state", timeout=1.0)


class TestOAuthRequestDetection(unittest.TestCase):
    """验证 FeishuEventEnvelope.is_oauth_request() 正确识别 OAuth 指令。"""

    def _make_envelope(self, text: str, chat_type: str = "p2p") -> object:
        adapter = FeishuEventAdapter()
        raw = adapter.create_local_fixture_event(text, chat_type=chat_type)
        return adapter.build_event_envelope(raw)

    def test_slash_oauth_detected(self) -> None:
        envelope = self._make_envelope("/oauth")
        self.assertTrue(envelope.is_oauth_request())

    def test_oauth_authorize_text_detected(self) -> None:
        envelope = self._make_envelope("oauth 授权")
        self.assertTrue(envelope.is_oauth_request())

    def test_regular_message_not_detected(self) -> None:
        envelope = self._make_envelope("帮我查一下今天的消息")
        self.assertFalse(envelope.is_oauth_request())

    def test_bind_command_not_detected_as_oauth(self) -> None:
        envelope = self._make_envelope("/bind")
        self.assertFalse(envelope.is_oauth_request())

    def test_group_message_not_detected_as_oauth(self) -> None:
        envelope = self._make_envelope("/oauth", chat_type="group")
        self.assertFalse(envelope.is_oauth_request())


class TestRuntimeOAuthHandling(unittest.TestCase):
    """验证 FeishuIngressService 对 /oauth 指令的路由和 config_missing 处理。"""

    def setUp(self) -> None:
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self._root = Path(self._tmp.name)
        (self._root / "data").mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _make_service(self, has_oauth_config: bool = True) -> object:
        from dutyflow.feishu.runtime import FeishuIngressService

        config = MagicMock()
        config.feishu_app_id = "app_demo"
        config.feishu_app_secret = "sec_demo"
        config.feishu_event_mode = "fixture"
        config.feishu_tenant_key = "tenant_demo"
        config.feishu_owner_open_id = "ou_owner"
        config.feishu_owner_report_chat_id = "oc_owner"
        config.feishu_owner_user_id = ""
        config.feishu_owner_union_id = ""
        config.feishu_oauth_redirect_uri = (
            "http://127.0.0.1:9768/feishu/oauth/callback" if has_oauth_config else ""
        )
        config.feishu_oauth_default_scopes = (
            ["docx:document:readonly"] if has_oauth_config else []
        )
        config.feishu_owner_user_access_token = ""
        config.feishu_owner_user_refresh_token = ""
        config.feishu_owner_user_token_expires_at = ""
        config.data_dir = self._root / "data"
        config.log_level = "INFO"
        config.permission_mode = "default"
        config.model_name = ""
        return FeishuIngressService(self._root, config)

    def _make_envelope(self, text: str) -> object:
        adapter = FeishuEventAdapter()
        raw = adapter.create_local_fixture_event(text, chat_type="p2p")
        return adapter.build_event_envelope(raw)

    def test_config_missing_returns_config_missing_action(self) -> None:
        service = self._make_service(has_oauth_config=False)
        envelope = self._make_envelope("/oauth")
        result = service._handle_oauth_request(envelope)
        self.assertEqual(result.get("oauth_action"), "config_missing")

    def test_oauth_request_returns_started_action(self) -> None:
        service = self._make_service(has_oauth_config=True)
        envelope = self._make_envelope("/oauth")

        with patch.object(
            FeishuOAuthManager, "start_callback_server", side_effect=TimeoutError("test")
        ):
            result = service._handle_oauth_request(envelope)

        self.assertEqual(result.get("oauth_action"), "started")
        self.assertIn("oauth_state", result)


class TestTokenNeedsRefresh(unittest.TestCase):
    """验证 _token_needs_refresh() 在各种 expires_at 值下的行为。"""

    def test_empty_string_returns_true(self) -> None:
        self.assertTrue(_token_needs_refresh(""))

    def test_invalid_format_returns_true(self) -> None:
        self.assertTrue(_token_needs_refresh("not-a-date"))

    def test_far_future_returns_false(self) -> None:
        from datetime import datetime, timedelta, timezone
        future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(timespec="seconds")
        self.assertFalse(_token_needs_refresh(future))

    def test_within_300s_returns_true(self) -> None:
        from datetime import datetime, timedelta, timezone
        soon = (datetime.now(timezone.utc) + timedelta(seconds=200)).isoformat(timespec="seconds")
        self.assertTrue(_token_needs_refresh(soon))

    def test_past_expiry_returns_true(self) -> None:
        from datetime import datetime, timedelta, timezone
        past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(timespec="seconds")
        self.assertTrue(_token_needs_refresh(past))

    def test_exactly_300s_returns_true(self) -> None:
        from datetime import datetime, timedelta, timezone
        boundary = (datetime.now(timezone.utc) + timedelta(seconds=300)).isoformat(timespec="seconds")
        self.assertTrue(_token_needs_refresh(boundary))

    def test_naive_datetime_treated_as_utc(self) -> None:
        from datetime import datetime, timedelta, timezone
        naive_future = (datetime.now(timezone.utc) + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S")
        self.assertFalse(_token_needs_refresh(naive_future))


class TestRefreshToken(unittest.TestCase):
    """验证 refresh_token() 调用刷新接口、持久化并同步 config。"""

    def _make_app_token_resp(self) -> MagicMock:
        resp = MagicMock()
        resp.json.return_value = {"code": 0, "app_access_token": "at.fake"}
        resp.raise_for_status = MagicMock()
        return resp

    def _make_refresh_resp(self, *, code: int = 0, data: dict | None = None, msg: str = "") -> MagicMock:
        resp = MagicMock()
        body: dict = {"code": code}
        if code == 0:
            body["data"] = data or {
                "access_token": "u.new_tok",
                "refresh_token": "ur.new_ref",
                "expires_in": 7140,
            }
        else:
            body["msg"] = msg or "error"
        resp.json.return_value = body
        resp.raise_for_status = MagicMock()
        return resp

    def test_successful_refresh_returns_new_token_data(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".env").write_text("")
            config = _make_config()
            config.feishu_owner_user_access_token = "u.old"
            config.feishu_owner_user_refresh_token = "ur.old"
            config.feishu_owner_user_token_expires_at = ""
            manager = FeishuOAuthManager(config, root)
            with patch("httpx.post", side_effect=[self._make_app_token_resp(), self._make_refresh_resp()]):
                data = manager.refresh_token("ur.old")
        self.assertEqual(data["access_token"], "u.new_tok")
        self.assertEqual(data["refresh_token"], "ur.new_ref")

    def test_refresh_writes_new_token_to_env(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".env").write_text("")
            config = _make_config()
            config.feishu_owner_user_access_token = "u.old"
            config.feishu_owner_user_refresh_token = "ur.old"
            config.feishu_owner_user_token_expires_at = ""
            manager = FeishuOAuthManager(config, root)
            with patch("httpx.post", side_effect=[self._make_app_token_resp(), self._make_refresh_resp()]):
                manager.refresh_token("ur.old")
            env_text = (root / ".env").read_text()
        self.assertIn("u.new_tok", env_text)
        self.assertIn("ur.new_ref", env_text)

    def test_refresh_updates_config_in_memory(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".env").write_text("")
            config = _make_config()
            config.feishu_owner_user_access_token = "u.old"
            config.feishu_owner_user_refresh_token = "ur.old"
            config.feishu_owner_user_token_expires_at = ""
            manager = FeishuOAuthManager(config, root)
            with patch("httpx.post", side_effect=[self._make_app_token_resp(), self._make_refresh_resp()]):
                manager.refresh_token("ur.old")
        self.assertEqual(config.feishu_owner_user_access_token, "u.new_tok")
        self.assertEqual(config.feishu_owner_user_refresh_token, "ur.new_ref")

    def test_feishu_error_raises_runtime_error(self) -> None:
        config = _make_config()
        config.feishu_owner_user_access_token = ""
        config.feishu_owner_user_refresh_token = ""
        config.feishu_owner_user_token_expires_at = ""
        manager = FeishuOAuthManager(config, Path("/tmp"))
        error_resp = self._make_refresh_resp(code=99306, msg="refresh token expired")
        with patch("httpx.post", side_effect=[self._make_app_token_resp(), error_resp]):
            with self.assertRaises(RuntimeError) as ctx:
                manager.refresh_token("ur.expired")
        self.assertIn("token 刷新", str(ctx.exception))


class TestEnsureValidToken(unittest.TestCase):
    """验证 ensure_valid_token() 的过期检测和刷新触发逻辑。"""

    def _make_manager(
        self,
        *,
        access_token: str = "u.valid",
        refresh_token_val: str = "ur.ref",
        expires_at: str = "",
        root: Path | None = None,
    ) -> FeishuOAuthManager:
        config = _make_config()
        config.feishu_owner_user_access_token = access_token
        config.feishu_owner_user_refresh_token = refresh_token_val
        config.feishu_owner_user_token_expires_at = expires_at
        return FeishuOAuthManager(config, root or Path("/tmp"))

    def _far_future(self) -> str:
        from datetime import datetime, timedelta, timezone
        return (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(timespec="seconds")

    def test_no_access_token_raises(self) -> None:
        manager = self._make_manager(access_token="")
        with self.assertRaises(RuntimeError) as ctx:
            manager.ensure_valid_token()
        self.assertIn("/oauth", str(ctx.exception))

    def test_valid_token_far_from_expiry_returns_immediately(self) -> None:
        manager = self._make_manager(expires_at=self._far_future())
        token = manager.ensure_valid_token()
        self.assertEqual(token, "u.valid")

    def test_empty_expires_at_triggers_refresh(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".env").write_text("")
            manager = self._make_manager(expires_at="", root=root)
            app_resp = MagicMock()
            app_resp.json.return_value = {"code": 0, "app_access_token": "at.fake"}
            app_resp.raise_for_status = MagicMock()
            refresh_resp = MagicMock()
            refresh_resp.json.return_value = {
                "code": 0,
                "data": {"access_token": "u.refreshed", "refresh_token": "ur.new", "expires_in": 7140},
            }
            refresh_resp.raise_for_status = MagicMock()
            with patch("httpx.post", side_effect=[app_resp, refresh_resp]):
                token = manager.ensure_valid_token()
        self.assertEqual(token, "u.refreshed")

    def test_near_expiry_triggers_refresh(self) -> None:
        import tempfile
        from datetime import datetime, timedelta, timezone
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".env").write_text("")
            soon = (datetime.now(timezone.utc) + timedelta(seconds=100)).isoformat(timespec="seconds")
            manager = self._make_manager(expires_at=soon, root=root)
            app_resp = MagicMock()
            app_resp.json.return_value = {"code": 0, "app_access_token": "at.fake"}
            app_resp.raise_for_status = MagicMock()
            refresh_resp = MagicMock()
            refresh_resp.json.return_value = {
                "code": 0,
                "data": {"access_token": "u.renewed", "refresh_token": "ur.new", "expires_in": 7140},
            }
            refresh_resp.raise_for_status = MagicMock()
            with patch("httpx.post", side_effect=[app_resp, refresh_resp]):
                token = manager.ensure_valid_token()
        self.assertEqual(token, "u.renewed")

    def test_no_refresh_token_raises(self) -> None:
        manager = self._make_manager(refresh_token_val="", expires_at="")
        with self.assertRaises(RuntimeError) as ctx:
            manager.ensure_valid_token()
        self.assertIn("/oauth", str(ctx.exception))

    def test_refresh_failure_propagates_error(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".env").write_text("")
            manager = self._make_manager(expires_at="", root=root)
            app_resp = MagicMock()
            app_resp.json.return_value = {"code": 0, "app_access_token": "at.fake"}
            app_resp.raise_for_status = MagicMock()
            err_resp = MagicMock()
            err_resp.json.return_value = {"code": 99306, "msg": "refresh token expired"}
            err_resp.raise_for_status = MagicMock()
            with patch("httpx.post", side_effect=[app_resp, err_resp]):
                with self.assertRaises(RuntimeError):
                    manager.ensure_valid_token()


def _self_test() -> None:
    """运行本文件所有单元测试。"""
    loader = unittest.defaultTestLoader
    suite = unittest.TestSuite()
    for cls in (
        TestBuildAuthorizeUrl,
        TestComputeExpiresAt,
        TestBuildBasicCredentials,
        TestParseFeishuResponse,
        TestExchangeCode,
        TestFetchUserInfo,
        TestPersistTokenResult,
        TestCallbackServerStateValidation,
        TestOAuthRequestDetection,
        TestRuntimeOAuthHandling,
        TestTokenNeedsRefresh,
        TestRefreshToken,
        TestEnsureValidToken,
    ):
        suite.addTests(loader.loadTestsFromTestCase(cls))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    _self_test()
