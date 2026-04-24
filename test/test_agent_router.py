# 本文件验证 ToolRouter 的能力来源判定和占位路由。

from pathlib import Path
import sys
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dutyflow.agent.tools import ToolCall, ToolResultEnvelope, ToolSpec  # noqa: E402
from dutyflow.agent.tools.registry import ToolRegistry  # noqa: E402
from dutyflow.agent.tools.router import ToolRouter  # noqa: E402


class TestAgentRouter(unittest.TestCase):
    """验证工具路由层不执行 handler，只生成路线。"""

    def test_native_tool_routes_as_executable(self) -> None:
        """native 工具应被路由为可执行路线。"""
        registry = ToolRegistry()
        registry.register(_echo_spec(), _echo_handler)
        route = ToolRouter(registry).route(ToolCall("tool_1", "sample_tool", {"text": "x"}, 0, 0))
        self.assertTrue(route.is_executable)
        self.assertEqual(route.source, "native")
        self.assertEqual(route.execution_mode, "concurrent")

    def test_reserved_source_routes_as_placeholder(self) -> None:
        """保留来源必须返回明确占位路线。"""
        registry = ToolRegistry()
        spec = ToolSpec("mcp_demo", "Reserved.", source="mcp_reserved")
        registry.register(spec)
        route = ToolRouter(registry).route(ToolCall("tool_1", "mcp_demo", {}, 0, 0))
        self.assertFalse(route.is_executable)
        self.assertIn("not implemented", route.error_message)

    def test_unregistered_tool_is_not_native_executable(self) -> None:
        """未注册工具不可被路由为可执行 native。"""
        route = ToolRouter(ToolRegistry()).route(ToolCall("tool_1", "missing", {}, 0, 0))
        self.assertFalse(route.is_executable)
        self.assertEqual(route.source, "placeholder")


def _echo_spec() -> ToolSpec:
    """构造 sample_tool 测试工具定义。"""
    return ToolSpec("sample_tool", "Sample tool.", {"required": ["text"]}, is_concurrency_safe=True)


def _echo_handler(tool_call, tool_use_context) -> ToolResultEnvelope:
    """测试用 echo handler。"""
    return ToolResultEnvelope(tool_call.tool_use_id, tool_call.tool_name, True, "ok")


def _self_test() -> None:
    """运行本文件单元测试。"""
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestAgentRouter)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    _self_test()
