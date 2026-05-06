# 本文件验证飞书用户面 FeishuUserClient 对 token provider 和统一请求层的薄封装。

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dutyflow.feishu.user_client import FeishuUserClient  # noqa: E402
from dutyflow.feishu.user_request import FeishuUserRequestClient  # noqa: E402


class _FakeTokenProvider:
    """测试替身：模拟用户 token provider 的最小接口。"""

    def __init__(self) -> None:
        """初始化固定 token。"""
        self.token = "u.fake"

    def get_token(self) -> str:
        """返回当前 token。"""
        return self.token

    def force_refresh(self) -> str:
        """模拟 token 强制刷新。"""
        self.token = "u.refreshed"
        return self.token

    def account_scope(self) -> dict[str, object]:
        """返回非敏感账号边界。"""
        return {"owner_user_id": "uid_1", "scopes": ["docx:document:readonly"]}

    def health_snapshot(self) -> dict[str, object]:
        """返回简化健康状态。"""
        return {"status": "valid"}


class _FakeHttpResponse:
    """测试替身：模拟 httpx.Response 的最小接口。"""

    status_code = 200
    text = '{"code":0,"data":{"ok":true}}'

    def json(self) -> dict[str, object]:
        """返回飞书 code=0 响应。"""
        return {"code": 0, "data": {"ok": True}}


class TestFeishuUserClient(unittest.TestCase):
    """验证用户面 client 的 GET/POST 和账号边界透传。"""

    def _make_client(self, root: Path) -> tuple[FeishuUserClient, _FakeTokenProvider]:
        """构造测试 client。"""
        provider = _FakeTokenProvider()
        request_client = FeishuUserRequestClient(provider, root)
        return FeishuUserClient(provider, request_client), provider

    def test_get_delegates_to_user_request_layer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client, _provider = self._make_client(Path(tmp))

            with patch("httpx.request", return_value=_FakeHttpResponse()) as mocked:
                result = client.get("https://open.feishu.cn/open-apis/test")

            kwargs = mocked.call_args.kwargs

        self.assertTrue(result.ok)
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer u.fake")
        self.assertEqual(kwargs["timeout"], 15.0)

    def test_post_passes_json_body(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client, _provider = self._make_client(Path(tmp))

            with patch("httpx.request", return_value=_FakeHttpResponse()) as mocked:
                result = client.post(
                    "https://open.feishu.cn/open-apis/test",
                    json_body={"name": "demo"},
                )

            kwargs = mocked.call_args.kwargs

        self.assertTrue(result.ok)
        self.assertEqual(kwargs["json"], {"name": "demo"})

    def test_account_scope_passthrough(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client, _provider = self._make_client(Path(tmp))

            scope = client.account_scope()

        self.assertEqual(scope["owner_user_id"], "uid_1")
        self.assertNotIn("token", scope)


def _self_test() -> None:
    """运行本文件所有单元测试。"""
    unittest.main(verbosity=2)


if __name__ == "__main__":
    _self_test()
