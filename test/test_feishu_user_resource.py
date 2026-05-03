# 本文件验证 FeishuUserResourceClient 的资源读取逻辑：
# read_doc（正文 + 标题）和 get_file_meta（drive batch_query），
# 覆盖成功路径、token_missing、permission_denied、not_found、api_error。

from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dutyflow.feishu.oauth import FeishuOAuthManager  # noqa: E402
from dutyflow.feishu.user_resource import (  # noqa: E402
    FeishuUserResourceClient,
    DocReadResult,
    FileMetaResult,
)


def _make_oauth_manager(
    *,
    access_token: str = "u.valid_tok",
    refresh_token: str = "ur.ref",
    expires_at: str = "",
) -> FeishuOAuthManager:
    """构造绑定 mock config 的 FeishuOAuthManager。"""
    config = MagicMock()
    config.feishu_app_id = "app_test"
    config.feishu_app_secret = "sec_test"
    config.feishu_owner_user_access_token = access_token
    config.feishu_owner_user_refresh_token = refresh_token
    config.feishu_owner_user_token_expires_at = expires_at
    config.feishu_oauth_redirect_uri = "http://127.0.0.1:9768/feishu/oauth/callback"
    config.feishu_oauth_default_scopes = ["docx:document:readonly"]
    return FeishuOAuthManager(config, Path("/tmp"))


def _make_client(*, access_token: str = "u.valid_tok") -> FeishuUserResourceClient:
    """构造持有有效 token 的资源客户端，ensure_valid_token 返回固定值。"""
    manager = _make_oauth_manager(access_token=access_token)
    client = FeishuUserResourceClient(manager)
    return client


def _http_resp(status_code: int, body: dict) -> MagicMock:
    """构造最小化 httpx response mock。"""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = body
    resp.text = str(body)
    return resp


def _ok_content_resp(content: str = "文档正文内容") -> MagicMock:
    return _http_resp(200, {"code": 0, "data": {"content": content}})


def _ok_title_resp(title: str = "测试文档") -> MagicMock:
    return _http_resp(200, {"code": 0, "data": {"document": {"title": title}}})


def _ok_meta_resp(
    file_token: str = "boxcnXXX",
    title: str = "测试文件",
    owner_id: str = "ou_owner",
    create_time: str = "1700000000",
    modify_time: str = "1700001000",
) -> MagicMock:
    return _http_resp(
        200,
        {
            "code": 0,
            "data": {
                "metas": [
                    {
                        "doc_token": file_token,
                        "doc_type": "file",
                        "title": title,
                        "owner_id": owner_id,
                        "create_time": create_time,
                        "latest_modify_time": modify_time,
                    }
                ],
                "failed_list": [],
            },
        },
    )


class TestReadDocSuccess(unittest.TestCase):
    """验证 read_doc() 正常路径：正文 + 标题均成功返回。"""

    def test_returns_ok_status(self) -> None:
        client = _make_client()
        with (
            patch.object(client.oauth_manager, "ensure_valid_token", return_value="u.tok"),
            patch("httpx.get", side_effect=[_ok_content_resp("正文"), _ok_title_resp("标题")]),
        ):
            result = client.read_doc("doxcnXXX")
        self.assertTrue(result.ok)
        self.assertEqual(result.status, "ok")

    def test_returns_content(self) -> None:
        client = _make_client()
        with (
            patch.object(client.oauth_manager, "ensure_valid_token", return_value="u.tok"),
            patch("httpx.get", side_effect=[_ok_content_resp("正文内容"), _ok_title_resp()]),
        ):
            result = client.read_doc("doxcnXXX")
        self.assertEqual(result.content, "正文内容")

    def test_returns_title(self) -> None:
        client = _make_client()
        with (
            patch.object(client.oauth_manager, "ensure_valid_token", return_value="u.tok"),
            patch("httpx.get", side_effect=[_ok_content_resp(), _ok_title_resp("我的文档")]),
        ):
            result = client.read_doc("doxcnXXX")
        self.assertEqual(result.title, "我的文档")

    def test_doc_token_preserved(self) -> None:
        client = _make_client()
        with (
            patch.object(client.oauth_manager, "ensure_valid_token", return_value="u.tok"),
            patch("httpx.get", side_effect=[_ok_content_resp(), _ok_title_resp()]),
        ):
            result = client.read_doc("doxcnABC")
        self.assertEqual(result.doc_token, "doxcnABC")

    def test_fetched_at_is_iso(self) -> None:
        client = _make_client()
        with (
            patch.object(client.oauth_manager, "ensure_valid_token", return_value="u.tok"),
            patch("httpx.get", side_effect=[_ok_content_resp(), _ok_title_resp()]),
        ):
            result = client.read_doc("doxcnXXX")
        self.assertIn("T", result.fetched_at)


class TestReadDocTitleFallback(unittest.TestCase):
    """验证 title 调用失败时 read_doc() 仍返回正文。"""

    def test_title_http_error_still_returns_content(self) -> None:
        client = _make_client()
        title_fail = _http_resp(403, {})
        with (
            patch.object(client.oauth_manager, "ensure_valid_token", return_value="u.tok"),
            patch("httpx.get", side_effect=[_ok_content_resp("正文"), title_fail]),
        ):
            result = client.read_doc("doxcnXXX")
        self.assertTrue(result.ok)
        self.assertEqual(result.content, "正文")
        self.assertEqual(result.title, "")

    def test_title_api_error_still_returns_content(self) -> None:
        client = _make_client()
        title_api_err = _http_resp(200, {"code": 99991668, "msg": "no permission"})
        with (
            patch.object(client.oauth_manager, "ensure_valid_token", return_value="u.tok"),
            patch("httpx.get", side_effect=[_ok_content_resp("内容"), title_api_err]),
        ):
            result = client.read_doc("doxcnXXX")
        self.assertTrue(result.ok)
        self.assertEqual(result.content, "内容")


class TestReadDocTokenMissing(unittest.TestCase):
    """验证 ensure_valid_token 失败时返回 token_missing。"""

    def test_no_token_returns_token_missing(self) -> None:
        manager = _make_oauth_manager(access_token="")
        client = FeishuUserResourceClient(manager)
        result = client.read_doc("doxcnXXX")
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "token_missing")

    def test_token_missing_has_detail(self) -> None:
        manager = _make_oauth_manager(access_token="")
        client = FeishuUserResourceClient(manager)
        result = client.read_doc("doxcnXXX")
        self.assertTrue(len(result.detail) > 0)

    def test_token_missing_content_is_empty(self) -> None:
        manager = _make_oauth_manager(access_token="")
        client = FeishuUserResourceClient(manager)
        result = client.read_doc("doxcnXXX")
        self.assertEqual(result.content, "")


class TestReadDocHttpErrors(unittest.TestCase):
    """验证各种 HTTP 错误码映射到正确的 status。"""

    def _run(self, status_code: int) -> DocReadResult:
        client = _make_client()
        fail = _http_resp(status_code, {})
        with (
            patch.object(client.oauth_manager, "ensure_valid_token", return_value="u.tok"),
            patch("httpx.get", return_value=fail),
        ):
            return client.read_doc("doxcnXXX")

    def test_403_returns_permission_denied(self) -> None:
        result = self._run(403)
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "permission_denied")

    def test_401_returns_permission_denied(self) -> None:
        result = self._run(401)
        self.assertEqual(result.status, "permission_denied")

    def test_404_returns_not_found(self) -> None:
        result = self._run(404)
        self.assertEqual(result.status, "not_found")

    def test_500_returns_api_error(self) -> None:
        result = self._run(500)
        self.assertEqual(result.status, "api_error")


class TestReadDocApiError(unittest.TestCase):
    """验证飞书 API 非零 code 时返回 api_error。"""

    def test_nonzero_code_returns_api_error(self) -> None:
        client = _make_client()
        api_err = _http_resp(200, {"code": 99991668, "msg": "no permission"})
        with (
            patch.object(client.oauth_manager, "ensure_valid_token", return_value="u.tok"),
            patch("httpx.get", return_value=api_err),
        ):
            result = client.read_doc("doxcnXXX")
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "api_error")
        self.assertIn("no permission", result.detail)


class TestGetFileMetaSuccess(unittest.TestCase):
    """验证 get_file_meta() 正常路径。"""

    def test_returns_ok_status(self) -> None:
        client = _make_client()
        with (
            patch.object(client.oauth_manager, "ensure_valid_token", return_value="u.tok"),
            patch("httpx.post", return_value=_ok_meta_resp()),
        ):
            result = client.get_file_meta("boxcnXXX", "file")
        self.assertTrue(result.ok)
        self.assertEqual(result.status, "ok")

    def test_returns_title(self) -> None:
        client = _make_client()
        with (
            patch.object(client.oauth_manager, "ensure_valid_token", return_value="u.tok"),
            patch("httpx.post", return_value=_ok_meta_resp(title="季报.xlsx")),
        ):
            result = client.get_file_meta("boxcnXXX", "file")
        self.assertEqual(result.title, "季报.xlsx")

    def test_returns_owner_id(self) -> None:
        client = _make_client()
        with (
            patch.object(client.oauth_manager, "ensure_valid_token", return_value="u.tok"),
            patch("httpx.post", return_value=_ok_meta_resp(owner_id="ou_abc")),
        ):
            result = client.get_file_meta("boxcnXXX", "file")
        self.assertEqual(result.owner_id, "ou_abc")

    def test_returns_create_and_edit_time(self) -> None:
        client = _make_client()
        with (
            patch.object(client.oauth_manager, "ensure_valid_token", return_value="u.tok"),
            patch(
                "httpx.post",
                return_value=_ok_meta_resp(create_time="1700000000", modify_time="1700001000"),
            ),
        ):
            result = client.get_file_meta("boxcnXXX", "file")
        self.assertEqual(result.create_time, "1700000000")
        self.assertEqual(result.edit_time, "1700001000")

    def test_file_token_and_type_preserved(self) -> None:
        client = _make_client()
        with (
            patch.object(client.oauth_manager, "ensure_valid_token", return_value="u.tok"),
            patch("httpx.post", return_value=_ok_meta_resp()),
        ):
            result = client.get_file_meta("boxcnABC", "docx")
        self.assertEqual(result.file_token, "boxcnABC")
        self.assertEqual(result.file_type, "docx")


class TestGetFileMetaTokenMissing(unittest.TestCase):
    """验证 ensure_valid_token 失败时返回 token_missing。"""

    def test_no_token_returns_token_missing(self) -> None:
        manager = _make_oauth_manager(access_token="")
        client = FeishuUserResourceClient(manager)
        result = client.get_file_meta("boxcnXXX", "file")
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "token_missing")


class TestGetFileMetaErrors(unittest.TestCase):
    """验证 get_file_meta() 的 HTTP 错误和 API 错误映射。"""

    def test_403_returns_permission_denied(self) -> None:
        client = _make_client()
        with (
            patch.object(client.oauth_manager, "ensure_valid_token", return_value="u.tok"),
            patch("httpx.post", return_value=_http_resp(403, {})),
        ):
            result = client.get_file_meta("boxcnXXX", "file")
        self.assertEqual(result.status, "permission_denied")

    def test_empty_metas_returns_api_error(self) -> None:
        client = _make_client()
        empty_resp = _http_resp(
            200, {"code": 0, "data": {"metas": [], "failed_list": [{"doc_token": "boxcnXXX"}]}}
        )
        with (
            patch.object(client.oauth_manager, "ensure_valid_token", return_value="u.tok"),
            patch("httpx.post", return_value=empty_resp),
        ):
            result = client.get_file_meta("boxcnXXX", "file")
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "api_error")

    def test_api_nonzero_code_returns_api_error(self) -> None:
        client = _make_client()
        with (
            patch.object(client.oauth_manager, "ensure_valid_token", return_value="u.tok"),
            patch("httpx.post", return_value=_http_resp(200, {"code": 99, "msg": "no perm"})),
        ):
            result = client.get_file_meta("boxcnXXX", "file")
        self.assertEqual(result.status, "api_error")
        self.assertIn("no perm", result.detail)


def _self_test() -> None:
    """运行本文件所有单元测试。"""
    loader = unittest.defaultTestLoader
    suite = unittest.TestSuite()
    for cls in (
        TestReadDocSuccess,
        TestReadDocTitleFallback,
        TestReadDocTokenMissing,
        TestReadDocHttpErrors,
        TestReadDocApiError,
        TestGetFileMetaSuccess,
        TestGetFileMetaTokenMissing,
        TestGetFileMetaErrors,
    ):
        suite.addTests(loader.loadTestsFromTestCase(cls))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    _self_test()
