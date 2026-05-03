# 本文件验证 web 工具组（web_guard、page_session、web_search、web_fetch、web_read_link）的行为。

from pathlib import Path
import json
import sys
import unittest
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dutyflow.agent.tools.types import ToolCall, ToolResultEnvelope  # noqa: E402
from dutyflow.agent.tools.logic.web_tools.web_guard import check_url_safety  # noqa: E402
from dutyflow.agent.tools.logic.web_tools import page_session  # noqa: E402
from dutyflow.agent.tools.logic.web_tools.page_session import (  # noqa: E402
    register_page, resolve_link, clear,
)
from dutyflow.agent.tools.logic.web_tools.web_search import WebSearchTool  # noqa: E402
from dutyflow.agent.tools.logic.web_tools.web_fetch import WebFetchTool  # noqa: E402
from dutyflow.agent.tools.logic.web_tools.web_read_link import WebReadLinkTool  # noqa: E402


def _call(tool_name: str, inputs: dict) -> ToolCall:
    return ToolCall("tid_test", tool_name, inputs, 0, 0)


class TestWebGuard(unittest.TestCase):
    """验证 URL 安全校验函数拒绝私有地址、非法 scheme，允许合法公网地址。"""

    def test_allows_https_public(self) -> None:
        self.assertEqual(check_url_safety("https://example.com/page"), "")

    def test_allows_http_public(self) -> None:
        self.assertEqual(check_url_safety("http://example.org/"), "")

    def test_blocks_private_192(self) -> None:
        self.assertNotEqual(check_url_safety("http://192.168.1.1/admin"), "")

    def test_blocks_private_10(self) -> None:
        self.assertNotEqual(check_url_safety("http://10.0.0.1/"), "")

    def test_blocks_private_172(self) -> None:
        self.assertNotEqual(check_url_safety("http://172.16.0.1/"), "")

    def test_blocks_localhost(self) -> None:
        self.assertNotEqual(check_url_safety("http://localhost/secret"), "")

    def test_blocks_loopback_ip(self) -> None:
        self.assertNotEqual(check_url_safety("http://127.0.0.1:8080/"), "")

    def test_blocks_ftp_scheme(self) -> None:
        self.assertNotEqual(check_url_safety("ftp://example.com/file"), "")

    def test_blocks_file_scheme(self) -> None:
        self.assertNotEqual(check_url_safety("file:///etc/passwd"), "")

    def test_blocks_empty_url(self) -> None:
        self.assertNotEqual(check_url_safety(""), "")

    def test_blocks_link_local(self) -> None:
        self.assertNotEqual(check_url_safety("http://169.254.169.254/latest/meta-data/"), "")


class TestPageSession(unittest.TestCase):
    """验证页面会话注册、链接解析和容量限制。"""

    def setUp(self) -> None:
        clear()

    def tearDown(self) -> None:
        clear()

    def test_register_and_resolve(self) -> None:
        register_page("page_001", [{"link_id": "link_001", "url": "https://a.example.com/"}])
        self.assertEqual(resolve_link("page_001", "link_001"), "https://a.example.com/")

    def test_resolve_missing_page_returns_none(self) -> None:
        self.assertIsNone(resolve_link("page_999", "link_001"))

    def test_resolve_missing_link_returns_none(self) -> None:
        register_page("page_002", [{"link_id": "link_001", "url": "https://b.example.com/"}])
        self.assertIsNone(resolve_link("page_002", "link_999"))

    def test_capacity_eviction(self) -> None:
        original_max = page_session._MAX_PAGE_SESSIONS
        page_session._MAX_PAGE_SESSIONS = 3
        try:
            for i in range(4):
                register_page(f"page_{i:03d}", [{"link_id": "link_001", "url": f"https://{i}.example.com/"}])
            # 四条注册后最旧的应被驱逐，总数不超过 3
            registered = [f"page_{i:03d}" for i in range(4) if resolve_link(f"page_{i:03d}", "link_001") is not None]
            self.assertLessEqual(len(registered), 3)
        finally:
            page_session._MAX_PAGE_SESSIONS = original_max

    def test_clear_removes_all(self) -> None:
        register_page("page_001", [{"link_id": "link_001", "url": "https://x.example.com/"}])
        clear()
        self.assertIsNone(resolve_link("page_001", "link_001"))


class TestWebSearchTool(unittest.TestCase):
    """验证 WebSearchTool 对搜索结果的封装、域名过滤和错误处理。"""

    def _make_fake_results(self) -> list[dict]:
        return [
            {"title": "Result A", "href": "https://alpha.example.com/a", "body": "snippet A"},
            {"title": "Result B", "href": "https://beta.example.com/b", "body": "snippet B"},
        ]

    def test_successful_search_returns_results(self) -> None:
        call = _call("web_search", {"query": "python httpx"})
        with patch(
            "dutyflow.agent.tools.logic.web_tools.web_search._run_search",
            return_value=self._make_fake_results(),
        ):
            result = WebSearchTool().handle(call, object())
        self.assertTrue(result.ok)
        payload = json.loads(result.content)
        self.assertEqual(payload["query"], "python httpx")
        self.assertEqual(payload["result_count"], 2)
        self.assertEqual(payload["results"][0]["url"], "https://alpha.example.com/a")

    def test_empty_query_returns_error(self) -> None:
        call = _call("web_search", {"query": "  "})
        result = WebSearchTool().handle(call, object())
        self.assertFalse(result.ok)
        self.assertEqual(result.error_kind, "invalid_input")

    def test_max_results_clamped_to_limit(self) -> None:
        call = _call("web_search", {"query": "test", "max_results": 999})
        captured: list[int] = []

        def fake_run(query, max_results, time_range):
            captured.append(max_results)
            return []

        with patch("dutyflow.agent.tools.logic.web_tools.web_search._run_search", side_effect=fake_run):
            WebSearchTool().handle(call, object())
        self.assertLessEqual(captured[0], 10)

    def test_allowed_domains_filter(self) -> None:
        call = _call("web_search", {"query": "test", "allowed_domains": ["alpha.example.com"]})
        with patch(
            "dutyflow.agent.tools.logic.web_tools.web_search._run_search",
            return_value=self._make_fake_results(),
        ):
            result = WebSearchTool().handle(call, object())
        payload = json.loads(result.content)
        self.assertEqual(payload["result_count"], 1)
        self.assertIn("alpha.example.com", payload["results"][0]["url"])

    def test_blocked_domains_filter(self) -> None:
        call = _call("web_search", {"query": "test", "blocked_domains": ["beta.example.com"]})
        with patch(
            "dutyflow.agent.tools.logic.web_tools.web_search._run_search",
            return_value=self._make_fake_results(),
        ):
            result = WebSearchTool().handle(call, object())
        payload = json.loads(result.content)
        self.assertEqual(payload["result_count"], 1)
        self.assertNotIn("beta.example.com", payload["results"][0]["url"])

    def test_search_failure_returns_error(self) -> None:
        call = _call("web_search", {"query": "fail test"})
        with patch(
            "dutyflow.agent.tools.logic.web_tools.web_search._run_search",
            side_effect=RuntimeError("network down"),
        ):
            result = WebSearchTool().handle(call, object())
        self.assertFalse(result.ok)
        self.assertEqual(result.error_kind, "search_failed")


class TestWebFetchTool(unittest.TestCase):
    """验证 WebFetchTool URL 拦截、正文提取和 Evidence 落盘路径。"""

    def setUp(self) -> None:
        clear()

    def tearDown(self) -> None:
        clear()

    def _fake_fetch_result(self, url: str = "https://example.com/") -> dict:
        html = (
            "<html><head><title>Test Page</title></head>"
            "<body><p>Hello world</p>"
            "<a href='/page2'>Next</a>"
            "<a href='https://other.com/x'>External</a>"
            "</body></html>"
        )
        return {
            "status_code": 200,
            "final_url": url,
            "content_type": "text/html; charset=utf-8",
            "html": html,
            "main_text": "Hello world",
            "title": "Test Page",
            "truncated": False,
        }

    def test_private_ip_blocked(self) -> None:
        call = _call("web_fetch", {"url": "http://192.168.1.1/"})
        result = WebFetchTool().handle(call, object())
        self.assertFalse(result.ok)
        self.assertEqual(result.error_kind, "url_blocked")

    def test_empty_url_error(self) -> None:
        call = _call("web_fetch", {"url": ""})
        result = WebFetchTool().handle(call, object())
        self.assertFalse(result.ok)
        self.assertEqual(result.error_kind, "invalid_input")

    def test_successful_fetch_returns_payload(self) -> None:
        call = _call("web_fetch", {"url": "https://example.com/"})
        ctx = MagicMock()
        ctx.cwd = None
        with patch(
            "dutyflow.agent.tools.logic.web_tools.web_fetch._fetch",
            return_value=self._fake_fetch_result(),
        ):
            result = WebFetchTool().handle(call, ctx)
        self.assertTrue(result.ok)
        payload = json.loads(result.content)
        self.assertEqual(payload["title"], "Test Page")
        self.assertEqual(payload["main_text_preview"], "Hello world")
        self.assertFalse(payload["truncated"])
        self.assertIn("page_id", payload)
        self.assertIn("fetch_time", payload)

    def test_links_extracted(self) -> None:
        call = _call("web_fetch", {"url": "https://example.com/"})
        ctx = MagicMock()
        ctx.cwd = None
        with patch(
            "dutyflow.agent.tools.logic.web_tools.web_fetch._fetch",
            return_value=self._fake_fetch_result(),
        ):
            result = WebFetchTool().handle(call, ctx)
        payload = json.loads(result.content)
        self.assertIsInstance(payload["links"], list)
        urls = [lnk["url"] for lnk in payload["links"]]
        self.assertTrue(any("page2" in u or "other.com" in u for u in urls))

    def test_page_id_registered_in_session(self) -> None:
        call = _call("web_fetch", {"url": "https://example.com/"})
        ctx = MagicMock()
        ctx.cwd = None
        with patch(
            "dutyflow.agent.tools.logic.web_tools.web_fetch._fetch",
            return_value=self._fake_fetch_result(),
        ):
            result = WebFetchTool().handle(call, ctx)
        payload = json.loads(result.content)
        if payload["links"]:
            first_link = payload["links"][0]
            resolved = resolve_link(payload["page_id"], first_link["link_id"])
            self.assertEqual(resolved, first_link["url"])

    def test_evidence_path_empty_when_no_cwd(self) -> None:
        call = _call("web_fetch", {"url": "https://example.com/"})
        ctx = MagicMock()
        ctx.cwd = None
        with patch(
            "dutyflow.agent.tools.logic.web_tools.web_fetch._fetch",
            return_value=self._fake_fetch_result(),
        ):
            result = WebFetchTool().handle(call, ctx)
        payload = json.loads(result.content)
        self.assertEqual(payload["evidence_path"], "")

    def test_fetch_failure_returns_error(self) -> None:
        call = _call("web_fetch", {"url": "https://example.com/"})
        ctx = MagicMock()
        ctx.cwd = None
        with patch(
            "dutyflow.agent.tools.logic.web_tools.web_fetch._fetch",
            side_effect=RuntimeError("timeout"),
        ):
            result = WebFetchTool().handle(call, ctx)
        self.assertFalse(result.ok)
        self.assertEqual(result.error_kind, "fetch_failed")

    def test_redirect_to_private_blocked(self) -> None:
        call = _call("web_fetch", {"url": "https://example.com/redirect"})
        ctx = MagicMock()
        ctx.cwd = None
        fake = self._fake_fetch_result()
        fake["final_url"] = "http://192.168.0.1/secret"
        with patch(
            "dutyflow.agent.tools.logic.web_tools.web_fetch._fetch",
            return_value=fake,
        ):
            result = WebFetchTool().handle(call, ctx)
        self.assertFalse(result.ok)
        self.assertEqual(result.error_kind, "redirect_blocked")


class TestWebReadLinkTool(unittest.TestCase):
    """验证 WebReadLinkTool 溯源约束和成功跳转时的 provenance 字段注入。"""

    def setUp(self) -> None:
        clear()

    def tearDown(self) -> None:
        clear()

    def test_missing_page_id_returns_error(self) -> None:
        call = _call("web_read_link", {"page_id": "page_999", "link_id": "link_001"})
        result = WebReadLinkTool().handle(call, object())
        self.assertFalse(result.ok)
        self.assertEqual(result.error_kind, "link_not_found")

    def test_missing_link_id_returns_error(self) -> None:
        register_page("page_001", [{"link_id": "link_001", "url": "https://example.com/a"}])
        call = _call("web_read_link", {"page_id": "page_001", "link_id": "link_999"})
        result = WebReadLinkTool().handle(call, object())
        self.assertFalse(result.ok)
        self.assertEqual(result.error_kind, "link_not_found")

    def test_empty_inputs_return_error(self) -> None:
        call = _call("web_read_link", {"page_id": "", "link_id": ""})
        result = WebReadLinkTool().handle(call, object())
        self.assertFalse(result.ok)
        self.assertEqual(result.error_kind, "invalid_input")

    def test_successful_read_adds_provenance(self) -> None:
        register_page("page_001", [{"link_id": "link_001", "url": "https://example.com/next"}])
        fake_payload = {
            "page_id": "page_new",
            "status_code": 200,
            "final_url": "https://example.com/next",
            "content_type": "text/html",
            "title": "Next Page",
            "main_text_preview": "content",
            "truncated": False,
            "evidence_path": "",
            "fetch_time": "2026-01-01T00:00:00+00:00",
            "links": [],
        }
        fake_result = ToolResultEnvelope(
            "tid_test", "web_fetch", True, json.dumps(fake_payload, ensure_ascii=False)
        )
        call = _call("web_read_link", {"page_id": "page_001", "link_id": "link_001"})
        ctx = MagicMock()
        with patch.object(WebFetchTool, "handle", return_value=fake_result):
            result = WebReadLinkTool().handle(call, ctx)
        self.assertTrue(result.ok)
        payload = json.loads(result.content)
        self.assertEqual(payload["from_page_id"], "page_001")
        self.assertEqual(payload["from_link_id"], "link_001")

    def test_web_fetch_failure_propagated(self) -> None:
        register_page("page_002", [{"link_id": "link_001", "url": "https://example.com/fail"}])
        fake_error = ToolResultEnvelope(
            "tid_test", "web_fetch", False, "fetch error",
            is_error=True, error_kind="fetch_failed",
        )
        call = _call("web_read_link", {"page_id": "page_002", "link_id": "link_001"})
        ctx = MagicMock()
        with patch.object(WebFetchTool, "handle", return_value=fake_error):
            result = WebReadLinkTool().handle(call, ctx)
        self.assertFalse(result.ok)


class TestWebToolsRegistry(unittest.TestCase):
    """验证三个 web 工具已正确注册到运行时工具注册表。"""

    def test_web_tools_registered(self) -> None:
        from dutyflow.agent.tools.registry import create_runtime_tool_registry
        registry = create_runtime_tool_registry()
        self.assertTrue(registry.has("web_search"))
        self.assertTrue(registry.has("web_fetch"))
        self.assertTrue(registry.has("web_read_link"))

    def test_web_tools_are_read_only(self) -> None:
        from dutyflow.agent.tools.registry import create_runtime_tool_registry
        registry = create_runtime_tool_registry()
        for name in ("web_search", "web_fetch", "web_read_link"):
            spec = registry.get(name)
            self.assertEqual(spec.idempotency, "read_only", f"{name} should be read_only")
            self.assertFalse(spec.requires_approval, f"{name} should not require approval")

    def test_web_tools_concurrency_safe(self) -> None:
        from dutyflow.agent.tools.registry import TOOL_REGISTRY
        for name in ("web_search", "web_fetch", "web_read_link"):
            self.assertTrue(TOOL_REGISTRY[name].is_concurrency_safe, f"{name} should be concurrency safe")


def _self_test() -> None:
    """运行本文件所有单元测试。"""
    loader = unittest.defaultTestLoader
    suite = unittest.TestSuite()
    for cls in (
        TestWebGuard,
        TestPageSession,
        TestWebSearchTool,
        TestWebFetchTool,
        TestWebReadLinkTool,
        TestWebToolsRegistry,
    ):
        suite.addTests(loader.loadTestsFromTestCase(cls))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    _self_test()
