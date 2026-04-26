# 本文件验证 Step 4 三个 lookup 工具的运行时行为。

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dutyflow.agent.state import create_initial_agent_state  # noqa: E402
from dutyflow.agent.tools.context import ToolUseContext  # noqa: E402
from dutyflow.agent.tools.executor import ToolExecutor  # noqa: E402
from dutyflow.agent.tools.logic.identity_tools.lookup_contact_identity import LookupContactIdentityTool  # noqa: E402
from dutyflow.agent.tools.logic.identity_tools.lookup_responsibility_context import LookupResponsibilityContextTool  # noqa: E402
from dutyflow.agent.tools.logic.identity_tools.lookup_source_context import LookupSourceContextTool  # noqa: E402
from dutyflow.agent.tools.registry import create_runtime_tool_registry  # noqa: E402
from dutyflow.agent.tools.router import ToolRouter  # noqa: E402
from dutyflow.agent.tools.types import ToolCall  # noqa: E402
from identity_fixture_data import write_identity_fixtures  # noqa: E402


class TestIdentityLookupTools(unittest.TestCase):
    """验证身份、来源与责任 lookup 工具。"""

    def test_lookup_contact_identity_returns_unique_payload(self) -> None:
        """联系人工具应返回裁剪后的唯一身份结果。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_identity_fixtures(root)
            result = LookupContactIdentityTool().handle(_contact_call({"feishu_user_id": "ou_001"}), _context(root))
            payload = _json_content(result)
        self.assertTrue(result.ok)
        self.assertEqual(payload["match_status"], "unique")
        self.assertEqual(payload["contact_id"], "contact_001")
        self.assertIn("需求判断", payload["context_snippet"])

    def test_lookup_contact_identity_returns_ambiguous_by_name_only(self) -> None:
        """联系人工具对重名查询应返回 ambiguous。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_identity_fixtures(root)
            payload = _json_content(LookupContactIdentityTool().handle(_contact_call({"name": "张三"}), _context(root)))
        self.assertEqual(payload["match_status"], "ambiguous")
        self.assertEqual(len(payload["ambiguous_candidates"]), 2)

    def test_lookup_source_context_returns_trimmed_source_payload(self) -> None:
        """来源工具应返回来源上下文和索引文件位置。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_identity_fixtures(root)
            payload = _json_content(LookupSourceContextTool().handle(_source_call({"source_id": "source_chat_001"}), _context(root)))
        self.assertEqual(payload["match_status"], "unique")
        self.assertEqual(payload["default_weight"], "high")
        self.assertEqual(payload["source_file"], "data/identity/sources/index.md")

    def test_lookup_responsibility_context_combines_contact_and_source(self) -> None:
        """责任工具应结合联系人详情和来源索引返回责任片段。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_identity_fixtures(root)
            payload = _json_content(
                LookupResponsibilityContextTool().handle(
                    _responsibility_call({"contact_id": "contact_001", "source_id": "source_chat_001", "matter_type": "项目排期"}),
                    _context(root),
                )
            )
        self.assertTrue(payload["responsibility_found"])
        self.assertIn("项目排期", payload["responsibility_scope"])
        self.assertTrue(payload["requires_user_attention"])
        self.assertEqual(len(payload["source_files"]), 2)

    def test_lookup_tools_run_through_executor(self) -> None:
        """三个 lookup 工具应可通过真实执行层直接放行。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_identity_fixtures(root)
            registry = create_runtime_tool_registry()
            context = _context(root, registry=registry)
            calls = (
                _contact_call({"contact_id": "contact_003"}),
                _source_call({"display_name": "核心项目群", "source_type": "chat"}),
                _responsibility_call({"contact_id": "contact_003", "source_id": "source_dm_001", "matter_type": "缺陷修复"}),
            )
            routes = ToolRouter(registry).route_many(calls)
            results = ToolExecutor(registry).execute_routes(routes, context)
        self.assertTrue(all(result.ok for result in results))
        self.assertEqual(_json_content(results[2])["relationship_to_user"], "direct_report")


def _contact_call(tool_input: dict[str, object]) -> ToolCall:
    """构造联系人 lookup 调用。"""
    return ToolCall("tool_contact_lookup_001", "lookup_contact_identity", tool_input, 0, 0)


def _source_call(tool_input: dict[str, object]) -> ToolCall:
    """构造来源 lookup 调用。"""
    return ToolCall("tool_source_lookup_001", "lookup_source_context", tool_input, 0, 0)


def _responsibility_call(tool_input: dict[str, object]) -> ToolCall:
    """构造责任 lookup 调用。"""
    return ToolCall("tool_resp_lookup_001", "lookup_responsibility_context", tool_input, 0, 0)


def _context(root: Path, registry=None) -> ToolUseContext:
    """构造 lookup 工具测试上下文。"""
    tool_registry = registry or create_runtime_tool_registry()
    return ToolUseContext("query_identity_001", root, create_initial_agent_state("query_identity_001", "hello"), tool_registry)


def _json_content(result) -> dict[str, object]:
    """把工具 JSON 输出转换成字典。"""
    return json.loads(result.content)


def _self_test() -> None:
    """运行本文件单元测试。"""
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestIdentityLookupTools)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    _self_test()
