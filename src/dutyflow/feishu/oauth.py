# 本文件负责飞书 OAuth 授权流程：授权 URL 构造、本地 callback 服务器、
# code 换 token、用户信息补全和 token 持久化。

from __future__ import annotations

import base64
import http.server
import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

from dutyflow.config.env import EnvConfig, save_env_values

_FEISHU_AUTHORIZE_URL = "https://open.feishu.cn/open-apis/authen/v1/authorize"
_FEISHU_TOKEN_URL = "https://open.feishu.cn/open-apis/authen/v1/oidc/access_token"
_FEISHU_USER_INFO_URL = "https://open.feishu.cn/open-apis/authen/v1/user_info"
# 关键开关：本地 callback 监听端口，必须与飞书后台登记的回调地址一致。
OAUTH_CALLBACK_PORT = 9768
# 关键开关：等待用户在浏览器完成授权的最长秒数。
OAUTH_TIMEOUT_SECONDS = 300.0


class FeishuOAuthManager:
    """负责飞书 OAuth 授权流程的全生命周期管理，包括 URL 构造、callback 等待、token 换取和持久化。"""

    def __init__(self, config: EnvConfig, project_root: Path) -> None:
        """绑定应用配置和项目根目录（用于持久化 .env）。"""
        self.config = config
        self.project_root = project_root

    def build_authorize_url(self, state: str) -> str:
        """构造飞书 OAuth 授权链接，引导用户完成登录和权限授权。"""
        scopes = " ".join(self.config.feishu_oauth_default_scopes)
        params = {
            "app_id": self.config.feishu_app_id,
            "redirect_uri": self.config.feishu_oauth_redirect_uri,
            "scope": scopes,
            "state": state,
        }
        return f"{_FEISHU_AUTHORIZE_URL}?{urlencode(params)}"

    def start_callback_server(
        self, state: str, timeout: float = OAUTH_TIMEOUT_SECONDS
    ) -> str:
        """在本地端口启动一次性 callback server，阻塞等待飞书回调并返回授权码 code。

        超时未收到回调时抛出 TimeoutError。
        """
        return _wait_for_oauth_code(OAUTH_CALLBACK_PORT, state, timeout)

    def exchange_code(self, code: str) -> dict[str, Any]:
        """用授权码换取 user_access_token，返回飞书 token 接口的 data 字段。"""
        import httpx

        credentials = _build_basic_credentials(
            self.config.feishu_app_id, self.config.feishu_app_secret
        )
        headers = {
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/json",
        }
        body = {"grant_type": "authorization_code", "code": code}
        response = httpx.post(_FEISHU_TOKEN_URL, headers=headers, json=body, timeout=15.0)
        response.raise_for_status()
        return _parse_feishu_response(response.json(), "token 换取")

    def fetch_user_info(self, access_token: str) -> dict[str, Any]:
        """用 user_access_token 查询飞书用户身份信息，返回 data 字段。"""
        import httpx

        headers = {"Authorization": f"Bearer {access_token}"}
        response = httpx.get(_FEISHU_USER_INFO_URL, headers=headers, timeout=10.0)
        response.raise_for_status()
        return _parse_feishu_response(response.json(), "用户信息查询")

    def persist_token_result(
        self,
        token_data: dict[str, Any],
        user_info: dict[str, Any],
    ) -> list[str]:
        """把 token 和用户身份字段写入 .env，返回实际写入的字段名列表。"""
        access_token = token_data.get("access_token") or token_data.get(
            "user_access_token", ""
        )
        expires_in = int(token_data.get("expires_in", 0))
        env_values = {
            "DUTYFLOW_FEISHU_OWNER_USER_ACCESS_TOKEN": access_token,
            "DUTYFLOW_FEISHU_OWNER_USER_REFRESH_TOKEN": token_data.get("refresh_token", ""),
            "DUTYFLOW_FEISHU_OWNER_USER_TOKEN_EXPIRES_AT": _compute_expires_at(expires_in),
            "DUTYFLOW_FEISHU_OWNER_USER_ID": user_info.get("user_id", ""),
            "DUTYFLOW_FEISHU_OWNER_UNION_ID": user_info.get("union_id", ""),
        }
        return save_env_values(self.project_root, env_values)


def _wait_for_oauth_code(port: int, state: str, timeout: float) -> str:
    """在指定端口启动临时 HTTP server，等待 OAuth callback 并提取 code。"""
    result_holder: list[str] = []
    shutdown_event = threading.Event()

    class _CallbackHandler(http.server.BaseHTTPRequestHandler):
        """处理单次 OAuth callback GET 请求的最小 HTTP handler。"""

        def do_GET(self) -> None:
            """接收飞书回调，校验 state 并提取 code。"""
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            received_code = (params.get("code") or [""])[0]
            received_state = (params.get("state") or [""])[0]

            if received_state != state:
                _write_html_response(self, 400, "state 参数不匹配，请重试。")
                return
            if not received_code:
                _write_html_response(self, 400, "未收到授权码，请重试。")
                return

            result_holder.append(received_code)
            _write_html_response(self, 200, "授权成功！可以关闭此页面，回到飞书继续操作。")
            shutdown_event.set()

        def log_message(self, format: str, *args: object) -> None:
            pass  # 抑制 HTTP server 默认 stderr 输出，避免污染日志

    server = http.server.HTTPServer(("127.0.0.1", port), _CallbackHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    try:
        shutdown_event.wait(timeout=timeout)
    finally:
        server.shutdown()
        server_thread.join(timeout=5.0)

    if not result_holder:
        raise TimeoutError(f"等待 OAuth callback 超时（{timeout:.0f} 秒）")
    return result_holder[0]


def _write_html_response(
    handler: http.server.BaseHTTPRequestHandler,
    status: int,
    message: str,
) -> None:
    """向浏览器写出包含单条提示信息的简单 HTML 响应。"""
    body = f"<html><body><p>{message}</p></body></html>".encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _build_basic_credentials(app_id: str, app_secret: str) -> str:
    """把 app_id:app_secret 编码为 HTTP Basic 认证凭据。"""
    return base64.b64encode(f"{app_id}:{app_secret}".encode()).decode()


def _parse_feishu_response(resp: dict[str, Any], context: str) -> dict[str, Any]:
    """解析飞书 API 响应，非零 code 时抛出带上下文的 RuntimeError。"""
    if resp.get("code") != 0:
        raise RuntimeError(f"飞书 {context} 失败：{resp.get('msg', '未知错误')}")
    return resp.get("data") or {}


def _compute_expires_at(expires_in_seconds: int) -> str:
    """根据 expires_in 秒数计算 ISO-8601 格式的绝对过期时间字符串。"""
    if expires_in_seconds <= 0:
        return ""
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in_seconds)
    return expires_at.isoformat(timespec="seconds")


def _self_test() -> None:
    """验证 URL 构造、过期时间计算和飞书响应解析（不发起真实网络请求）。"""
    from unittest.mock import MagicMock

    config = MagicMock()
    config.feishu_app_id = "app_test"
    config.feishu_app_secret = "secret_test"
    config.feishu_oauth_redirect_uri = "http://127.0.0.1:9768/feishu/oauth/callback"
    config.feishu_oauth_default_scopes = ["docx:document:readonly", "drive:drive:readonly"]

    manager = FeishuOAuthManager(config, Path("/tmp"))
    url = manager.build_authorize_url("state_abc")
    assert "app_id=app_test" in url, f"app_id missing: {url}"
    assert "state=state_abc" in url, f"state missing: {url}"
    assert _FEISHU_AUTHORIZE_URL in url, f"base url missing: {url}"

    expires_str = _compute_expires_at(7140)
    assert "T" in expires_str, "expected ISO format"
    assert expires_str.endswith("+00:00"), "expected UTC offset"
    assert _compute_expires_at(0) == "", "zero should return empty"

    good_resp = {"code": 0, "data": {"access_token": "tok123"}}
    assert _parse_feishu_response(good_resp, "test")["access_token"] == "tok123"

    bad_resp = {"code": 99200, "msg": "invalid code"}
    try:
        _parse_feishu_response(bad_resp, "test")
        assert False, "should have raised"
    except RuntimeError:
        pass

    creds = _build_basic_credentials("id1", "sec1")
    import base64 as b64
    decoded = b64.b64decode(creds).decode()
    assert decoded == "id1:sec1", f"unexpected credentials: {decoded}"


if __name__ == "__main__":
    _self_test()
    print("dutyflow feishu oauth self-test passed")
