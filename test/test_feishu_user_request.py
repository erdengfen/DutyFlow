# 本文件验证飞书用户面统一请求层的鉴权、错误归一、日志和 raw 落盘。

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dutyflow.feishu.user_request import (  # noqa: E402
    FeishuUserRequest,
    FeishuUserRequestClient,
)


class _FakeTokenProvider:
    """测试替身：提供可变 user token 并记录强制刷新次数。"""

    def __init__(self, token: str = "u.old") -> None:
        """初始化当前 token。"""
        self.token = token
        self.force_refresh_count = 0

    def get_token(self) -> str:
        """返回当前 token。"""
        return self.token

    def force_refresh(self) -> str:
        """模拟强制刷新，并切换到新 token。"""
        self.force_refresh_count += 1
        self.token = "u.new"
        return self.token


class _FakeHttpResponse:
    """测试替身：模拟 httpx.Response 的最小接口。"""

    def __init__(self, status_code: int, body: object, text: str = "") -> None:
        """绑定 HTTP 状态、JSON body 和原始文本。"""
        self.status_code = status_code
        self._body = body
        self.text = text or str(body)

    def json(self) -> object:
        """返回预设 JSON body，异常对象用于模拟解析失败。"""
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class TestFeishuUserRequestClient(unittest.TestCase):
    """验证用户面请求层的主要行为。"""

    def test_authorization_header_added_and_log_redacts_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            provider = _FakeTokenProvider("u.secret")
            client = FeishuUserRequestClient(provider, root)
            response = _ok_response({"name": "demo"})

            with patch("httpx.request", return_value=response) as mocked:
                result = client.request(_request(trace_id="trace_auth"))

            headers = mocked.call_args.kwargs["headers"]
            log_text = _read_all_logs(root)

        self.assertTrue(result.ok)
        self.assertEqual(headers["Authorization"], "Bearer u.secret")
        self.assertIn("feishu_user_request", log_text)
        self.assertNotIn("u.secret", log_text)
        self.assertNotIn("Bearer u.secret", log_text)

    def test_code_zero_returns_ok_response(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = FeishuUserRequestClient(_FakeTokenProvider(), Path(tmp))

            with patch("httpx.request", return_value=_ok_response({"value": 1})):
                result = client.request(_request())

        self.assertTrue(result.ok)
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.data["value"], 1)

    def test_401_triggers_one_force_refresh_retry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            provider = _FakeTokenProvider("u.old")
            client = FeishuUserRequestClient(provider, Path(tmp))
            first = _FakeHttpResponse(401, {"code": 99991663, "msg": "expired"})
            second = _ok_response({"value": "ok"})

            with patch("httpx.request", side_effect=[first, second]) as mocked:
                result = client.request_with_token_retry(_request())

            first_headers = mocked.call_args_list[0].kwargs["headers"]
            second_headers = mocked.call_args_list[1].kwargs["headers"]

        self.assertTrue(result.ok)
        self.assertEqual(provider.force_refresh_count, 1)
        self.assertEqual(first_headers["Authorization"], "Bearer u.old")
        self.assertEqual(second_headers["Authorization"], "Bearer u.new")

    def test_403_maps_permission_denied_without_retry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            provider = _FakeTokenProvider()
            client = FeishuUserRequestClient(provider, Path(tmp))
            forbidden = _FakeHttpResponse(403, {"code": 0, "msg": "forbidden"})

            with patch("httpx.request", return_value=forbidden) as mocked:
                result = client.request_with_token_retry(_request())

        self.assertFalse(result.ok)
        self.assertEqual(result.status, "permission_denied")
        self.assertEqual(provider.force_refresh_count, 0)
        self.assertEqual(mocked.call_count, 1)

    def test_timeout_maps_timeout_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = FeishuUserRequestClient(
                _FakeTokenProvider(),
                Path(tmp),
                max_retries=0,
            )

            with patch("httpx.request", side_effect=httpx.TimeoutException("slow")):
                result = client.request(_request())

        self.assertFalse(result.ok)
        self.assertEqual(result.status, "timeout")

    def test_5xx_maps_transient_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = FeishuUserRequestClient(
                _FakeTokenProvider(),
                Path(tmp),
                max_retries=0,
            )

            with patch("httpx.request", return_value=_FakeHttpResponse(503, {"code": 1})):
                result = client.request(_request())

        self.assertFalse(result.ok)
        self.assertEqual(result.status, "transient_error")

    def test_nonzero_feishu_code_maps_api_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = FeishuUserRequestClient(_FakeTokenProvider(), Path(tmp))
            api_error = _FakeHttpResponse(200, {"code": 12345, "msg": "bad request"})

            with patch("httpx.request", return_value=api_error):
                result = client.request(_request())

        self.assertFalse(result.ok)
        self.assertEqual(result.status, "api_error")
        self.assertEqual(result.feishu_code, 12345)

    def test_invalid_json_maps_invalid_response(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = FeishuUserRequestClient(_FakeTokenProvider(), Path(tmp))
            invalid = _FakeHttpResponse(200, ValueError("not json"), text="not json")

            with patch("httpx.request", return_value=invalid):
                result = client.request(_request())

        self.assertFalse(result.ok)
        self.assertEqual(result.status, "invalid_response")

    def test_paged_request_stops_at_max_pages_and_sets_next_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = FeishuUserRequestClient(_FakeTokenProvider(), Path(tmp))
            first = _ok_response({"items": [1], "has_more": True, "page_token": "p2"})
            second = _ok_response({"items": [2], "has_more": True, "page_token": "p3"})

            with patch("httpx.request", side_effect=[first, second]) as mocked:
                responses = client.paged_request(
                    _request(params={"page_size": 1}),
                    max_pages=2,
                )

            first_params = mocked.call_args_list[0].kwargs["params"]
            second_params = mocked.call_args_list[1].kwargs["params"]

        self.assertEqual(len(responses), 2)
        self.assertNotIn("page_token", first_params)
        self.assertEqual(second_params["page_token"], "p2")
        self.assertTrue(responses[-1].has_more)
        self.assertEqual(responses[-1].page_token, "p3")

    def test_raw_response_is_written_and_redacted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            client = FeishuUserRequestClient(_FakeTokenProvider(), root)
            body = {"code": 0, "data": {"access_token": "secret-token", "name": "ok"}}

            with patch("httpx.request", return_value=_FakeHttpResponse(200, body)):
                result = client.request(_request(trace_id="raw_trace"), save_raw=True)

            raw_text = Path(result.raw_path).read_text(encoding="utf-8")

        self.assertTrue(result.ok)
        self.assertIn("raw_trace", raw_text)
        self.assertIn('"name": "ok"', raw_text)
        self.assertNotIn("secret-token", raw_text)


def _request(
    *,
    trace_id: str = "trace_test",
    params: dict[str, object] | None = None,
) -> FeishuUserRequest:
    """构造测试请求。"""
    return FeishuUserRequest(
        method="GET",
        url="https://open.feishu.cn/open-apis/test/v1/items",
        params=params,
        trace_id=trace_id,
        collector_name="test_collector",
    )


def _ok_response(data: dict[str, object]) -> _FakeHttpResponse:
    """构造飞书 code=0 响应。"""
    return _FakeHttpResponse(200, {"code": 0, "data": data})


def _read_all_logs(root: Path) -> str:
    """读取临时目录下的全部审计日志内容。"""
    return "\n".join(
        path.read_text(encoding="utf-8") for path in root.glob("data/logs/*.md")
    )


def _self_test() -> None:
    """运行本文件所有单元测试。"""
    unittest.main(verbosity=2)


if __name__ == "__main__":
    _self_test()
