# 本文件验证 feishu_read_doc、feishu_get_file_meta 和 feishu_search_drive 工具的注册、contract 结构和执行逻辑。

from pathlib import Path
import json
import sys
import unittest
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dutyflow.agent.tools.logic.feishu_tools.read_doc import FeishuReadDocTool  # noqa: E402
from dutyflow.agent.tools.logic.feishu_tools.get_file_meta import FeishuGetFileMetaTool  # noqa: E402
from dutyflow.agent.tools.logic.feishu_tools.search_drive import FeishuSearchDriveTool  # noqa: E402
from dutyflow.agent.tools.registry import create_runtime_tool_registry  # noqa: E402
from dutyflow.agent.tools.types import ToolCall  # noqa: E402
from dutyflow.feishu.user_resource import DocReadResult, DriveFileItem, DriveSearchResult, FileMetaResult  # noqa: E402


def _ctx(cwd: Path = Path("/tmp")) -> MagicMock:
    ctx = MagicMock()
    ctx.cwd = cwd
    return ctx


def _call(name: str, inputs: dict, uid: str = "tid_1") -> ToolCall:
    return ToolCall(uid, name, inputs, 0, 0)


def _ok_doc_result(
    doc_token: str = "doxcnABC",
    content: str = "文档正文",
    title: str = "测试文档",
) -> DocReadResult:
    return DocReadResult(
        ok=True, status="ok", doc_token=doc_token,
        title=title, content=content,
        fetched_at="2026-05-03T00:00:00+00:00", detail="",
    )


def _fail_doc_result(status: str = "token_missing", detail: str = "no token") -> DocReadResult:
    return DocReadResult(
        ok=False, status=status, doc_token="doxcnXXX",
        title="", content="", fetched_at="", detail=detail,
    )


def _ok_meta_result(file_token: str = "boxcnABC") -> FileMetaResult:
    return FileMetaResult(
        ok=True, status="ok", file_token=file_token, file_type="file",
        title="季报.xlsx", owner_id="ou_abc",
        create_time="1700000000", edit_time="1700001000",
        fetched_at="2026-05-03T00:00:00+00:00", detail="",
    )


def _fail_meta_result(status: str = "token_missing") -> FileMetaResult:
    return FileMetaResult(
        ok=False, status=status, file_token="boxcnXXX", file_type="file",
        title="", owner_id="", create_time="", edit_time="",
        fetched_at="", detail="no token",
    )


class TestToolRegistration(unittest.TestCase):
    """验证三个飞书工具已正确注册到运行时注册表。"""

    def setUp(self) -> None:
        self.registry = create_runtime_tool_registry()

    def test_feishu_read_doc_registered(self) -> None:
        self.assertTrue(self.registry.has("feishu_read_doc"))

    def test_feishu_get_file_meta_registered(self) -> None:
        self.assertTrue(self.registry.has("feishu_get_file_meta"))

    def test_feishu_search_drive_registered(self) -> None:
        self.assertTrue(self.registry.has("feishu_search_drive"))

    def test_search_drive_requires_approval_false(self) -> None:
        spec = self.registry.get("feishu_search_drive")
        self.assertFalse(spec.requires_approval)

    def test_search_drive_idempotency_read_only(self) -> None:
        spec = self.registry.get("feishu_search_drive")
        self.assertEqual(spec.idempotency, "read_only")

    def test_search_drive_required_input_query(self) -> None:
        spec = self.registry.get("feishu_search_drive")
        self.assertIn("query", spec.required_inputs())
        self.assertNotIn("count", spec.required_inputs())

    def test_read_doc_requires_approval_false(self) -> None:
        spec = self.registry.get("feishu_read_doc")
        self.assertFalse(spec.requires_approval)

    def test_get_file_meta_requires_approval_false(self) -> None:
        spec = self.registry.get("feishu_get_file_meta")
        self.assertFalse(spec.requires_approval)

    def test_read_doc_idempotency_read_only(self) -> None:
        spec = self.registry.get("feishu_read_doc")
        self.assertEqual(spec.idempotency, "read_only")

    def test_get_file_meta_idempotency_read_only(self) -> None:
        spec = self.registry.get("feishu_get_file_meta")
        self.assertEqual(spec.idempotency, "read_only")

    def test_read_doc_required_input_doc_token(self) -> None:
        spec = self.registry.get("feishu_read_doc")
        self.assertIn("doc_token", spec.required_inputs())

    def test_get_file_meta_required_inputs(self) -> None:
        spec = self.registry.get("feishu_get_file_meta")
        self.assertIn("file_token", spec.required_inputs())
        self.assertIn("file_type", spec.required_inputs())


class TestFeishuReadDocTool(unittest.TestCase):
    """验证 feishu_read_doc 工具的输入校验和执行逻辑。"""

    def _run(self, inputs: dict, doc_result: DocReadResult | None = None) -> object:
        tool = FeishuReadDocTool()
        call = _call("feishu_read_doc", inputs)
        ctx = _ctx()
        if doc_result is None:
            return tool.handle(call, ctx)
        with patch(
            "dutyflow.agent.tools.logic.feishu_tools.read_doc._build_client"
        ) as mock_build:
            mock_client = MagicMock()
            mock_client.read_doc.return_value = doc_result
            mock_build.return_value = mock_client
            return tool.handle(call, ctx)

    def test_empty_doc_token_returns_invalid_input(self) -> None:
        result = self._run({"doc_token": ""})
        self.assertFalse(result.ok)
        self.assertEqual(result.error_kind, "invalid_input")

    def test_missing_doc_token_returns_invalid_input(self) -> None:
        result = self._run({})
        self.assertFalse(result.ok)
        self.assertEqual(result.error_kind, "invalid_input")

    def test_token_missing_propagates_error(self) -> None:
        result = self._run({"doc_token": "doxcnXXX"}, _fail_doc_result("token_missing"))
        self.assertFalse(result.ok)
        self.assertEqual(result.error_kind, "token_missing")

    def test_permission_denied_propagates_error(self) -> None:
        result = self._run({"doc_token": "doxcnXXX"}, _fail_doc_result("permission_denied", "403"))
        self.assertEqual(result.error_kind, "permission_denied")

    def test_success_returns_ok(self) -> None:
        result = self._run({"doc_token": "doxcnABC"}, _ok_doc_result())
        self.assertTrue(result.ok)

    def test_success_payload_has_required_fields(self) -> None:
        result = self._run({"doc_token": "doxcnABC"}, _ok_doc_result(content="正文"))
        payload = json.loads(result.content)
        for key in ("doc_token", "title", "content_preview", "truncated", "evidence_path", "fetched_at"):
            self.assertIn(key, payload)

    def test_content_preview_truncated_at_1000(self) -> None:
        long_content = "x" * 2000
        result = self._run({"doc_token": "doxcnABC"}, _ok_doc_result(content=long_content))
        payload = json.loads(result.content)
        self.assertEqual(len(payload["content_preview"]), 1000)
        self.assertTrue(payload["truncated"])

    def test_short_content_not_truncated(self) -> None:
        result = self._run({"doc_token": "doxcnABC"}, _ok_doc_result(content="短文"))
        payload = json.loads(result.content)
        self.assertFalse(payload["truncated"])

    def test_title_preserved_in_payload(self) -> None:
        result = self._run({"doc_token": "doxcnABC"}, _ok_doc_result(title="我的文档"))
        payload = json.loads(result.content)
        self.assertEqual(payload["title"], "我的文档")


class TestFeishuGetFileMetaTool(unittest.TestCase):
    """验证 feishu_get_file_meta 工具的输入校验和执行逻辑。"""

    def _run(self, inputs: dict, meta_result: FileMetaResult | None = None) -> object:
        tool = FeishuGetFileMetaTool()
        call = _call("feishu_get_file_meta", inputs)
        ctx = _ctx()
        if meta_result is None:
            return tool.handle(call, ctx)
        with patch(
            "dutyflow.agent.tools.logic.feishu_tools.get_file_meta._build_client"
        ) as mock_build:
            mock_client = MagicMock()
            mock_client.get_file_meta.return_value = meta_result
            mock_build.return_value = mock_client
            return tool.handle(call, ctx)

    def test_empty_file_token_returns_invalid_input(self) -> None:
        result = self._run({"file_token": "", "file_type": "file"})
        self.assertFalse(result.ok)
        self.assertEqual(result.error_kind, "invalid_input")

    def test_invalid_file_type_returns_invalid_input(self) -> None:
        result = self._run({"file_token": "boxcnXXX", "file_type": "unknown"})
        self.assertFalse(result.ok)
        self.assertEqual(result.error_kind, "invalid_input")

    def test_token_missing_propagates_error(self) -> None:
        result = self._run(
            {"file_token": "boxcnXXX", "file_type": "file"},
            _fail_meta_result("token_missing"),
        )
        self.assertEqual(result.error_kind, "token_missing")

    def test_success_returns_ok(self) -> None:
        result = self._run(
            {"file_token": "boxcnABC", "file_type": "file"},
            _ok_meta_result(),
        )
        self.assertTrue(result.ok)

    def test_success_payload_has_required_fields(self) -> None:
        result = self._run(
            {"file_token": "boxcnABC", "file_type": "file"},
            _ok_meta_result(),
        )
        payload = json.loads(result.content)
        for key in ("file_token", "file_type", "title", "owner_id", "create_time", "edit_time", "fetched_at"):
            self.assertIn(key, payload)

    def test_all_valid_file_types_accepted(self) -> None:
        for ftype in ("doc", "docx", "sheet", "bitable", "folder", "file"):
            result = self._run(
                {"file_token": "boxcnABC", "file_type": ftype},
                _ok_meta_result(),
            )
            self.assertTrue(result.ok, f"file_type={ftype!r} should be accepted")

    def test_title_in_payload(self) -> None:
        result = self._run(
            {"file_token": "boxcnABC", "file_type": "file"},
            _ok_meta_result(),
        )
        payload = json.loads(result.content)
        self.assertEqual(payload["title"], "季报.xlsx")


def _ok_search_result(
    query: str = "季报",
    files: list[tuple[str, str, str]] | None = None,
) -> DriveSearchResult:
    """构造成功的搜索结果，files 为 (token, name, file_type) 三元组列表。"""
    if files is None:
        files = [("doxcnABC", "2024 Q4 季报", "docx")]
    items = tuple(
        DriveFileItem(
            token=t, name=n, file_type=ft,
            url=f"https://company.feishu.cn/{ft}/{t}",
            owner_id="ou_abc", modified_time="1700001000",
        )
        for t, n, ft in files
    )
    return DriveSearchResult(
        ok=True, status="ok", query=query,
        files=items, has_more=False, total=len(items),
        fetched_at="2026-05-04T00:00:00+00:00", detail="",
    )


def _fail_search_result(status: str = "token_missing") -> DriveSearchResult:
    return DriveSearchResult(
        ok=False, status=status, query="季报",
        files=(), has_more=False, total=0,
        fetched_at="", detail="no token",
    )


class TestFeishuSearchDriveTool(unittest.TestCase):
    """验证 feishu_search_drive 工具的输入校验和执行逻辑。"""

    def _run(self, inputs: dict, search_result: DriveSearchResult | None = None) -> object:
        tool = FeishuSearchDriveTool()
        call = _call("feishu_search_drive", inputs)
        ctx = _ctx()
        if search_result is None:
            return tool.handle(call, ctx)
        with patch(
            "dutyflow.agent.tools.logic.feishu_tools.search_drive._build_client"
        ) as mock_build:
            mock_client = MagicMock()
            mock_client.search_drive.return_value = search_result
            mock_build.return_value = mock_client
            return tool.handle(call, ctx)

    def test_empty_query_returns_invalid_input(self) -> None:
        result = self._run({"query": ""})
        self.assertFalse(result.ok)
        self.assertEqual(result.error_kind, "invalid_input")

    def test_missing_query_returns_invalid_input(self) -> None:
        result = self._run({})
        self.assertFalse(result.ok)
        self.assertEqual(result.error_kind, "invalid_input")

    def test_token_missing_propagates_error(self) -> None:
        result = self._run({"query": "季报"}, _fail_search_result("token_missing"))
        self.assertFalse(result.ok)
        self.assertEqual(result.error_kind, "token_missing")

    def test_api_error_propagates(self) -> None:
        result = self._run({"query": "季报"}, _fail_search_result("api_error"))
        self.assertEqual(result.error_kind, "api_error")

    def test_success_returns_ok(self) -> None:
        result = self._run({"query": "季报"}, _ok_search_result())
        self.assertTrue(result.ok)

    def test_success_payload_has_required_fields(self) -> None:
        result = self._run({"query": "季报"}, _ok_search_result())
        payload = json.loads(result.content)
        for key in ("query", "total", "has_more", "count", "files", "fetched_at"):
            self.assertIn(key, payload)

    def test_file_payload_has_required_fields(self) -> None:
        result = self._run({"query": "季报"}, _ok_search_result())
        payload = json.loads(result.content)
        file_item = payload["files"][0]
        for key in ("name", "token", "type", "url", "modified_time", "next_tool"):
            self.assertIn(key, file_item)

    def test_docx_type_next_tool_is_feishu_read_doc(self) -> None:
        result = self._run({"query": "季报"}, _ok_search_result(files=[("doxcnABC", "季报", "docx")]))
        payload = json.loads(result.content)
        self.assertEqual(payload["files"][0]["next_tool"], "feishu_read_doc")

    def test_sheet_type_next_tool_is_feishu_get_file_meta(self) -> None:
        result = self._run({"query": "季报表"}, _ok_search_result(files=[("shtcnXYZ", "季报表", "sheet")]))
        payload = json.loads(result.content)
        self.assertEqual(payload["files"][0]["next_tool"], "feishu_get_file_meta")

    def test_count_capped_at_max(self) -> None:
        """count 超过上限时工具应静默截断，不返回错误。"""
        result = self._run({"query": "季报", "count": 999}, _ok_search_result())
        self.assertTrue(result.ok)

    def test_invalid_count_falls_back_to_default(self) -> None:
        result = self._run({"query": "季报", "count": "not_a_number"}, _ok_search_result())
        self.assertTrue(result.ok)

    def test_empty_result_list_returns_ok(self) -> None:
        result = self._run({"query": "不存在的文档"}, _ok_search_result(files=[]))
        self.assertTrue(result.ok)
        payload = json.loads(result.content)
        self.assertEqual(payload["files"], [])
        self.assertEqual(payload["count"], 0)


def _self_test() -> None:
    loader = unittest.defaultTestLoader
    suite = unittest.TestSuite()
    for cls in (TestToolRegistration, TestFeishuReadDocTool, TestFeishuGetFileMetaTool, TestFeishuSearchDriveTool):
        suite.addTests(loader.loadTestsFromTestCase(cls))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    _self_test()
