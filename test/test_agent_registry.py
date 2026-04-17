# 本文件验证工具注册表的注册、查找和最小输入校验。

from pathlib import Path
import sys
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dutyflow.agent.tools import ToolCall, ToolResultEnvelope, ToolSpec  # noqa: E402
from dutyflow.agent.tools.registry import ToolRegistry  # noqa: E402


class TestAgentRegistry(unittest.TestCase):
    """验证 ToolRegistry 的基础行为。"""

    def test_register_and_lookup_tool(self) -> None:
        """注册后应能查找工具定义和 handler。"""
        registry = ToolRegistry()
        spec = _echo_spec()
        registry.register(spec, _echo_handler)
        self.assertEqual(registry.get("echo_text").name, "echo_text")
        self.assertIsNotNone(registry.get_handler("echo_text"))

    def test_duplicate_tool_name_is_rejected(self) -> None:
        """重复注册同名工具必须失败。"""
        registry = ToolRegistry()
        registry.register(_echo_spec(), _echo_handler)
        with self.assertRaises(ValueError):
            registry.register(_echo_spec(), _echo_handler)

    def test_unregistered_tool_lookup_fails(self) -> None:
        """查找未注册工具必须失败。"""
        with self.assertRaises(KeyError):
            ToolRegistry().get("missing")

    def test_missing_required_input_fails(self) -> None:
        """缺失必填 input 字段必须失败。"""
        registry = ToolRegistry()
        registry.register(_echo_spec(), _echo_handler)
        call = ToolCall("tool_1", "echo_text", {}, 0, 0)
        with self.assertRaises(ValueError):
            registry.validate_tool_input(call)


def _echo_spec() -> ToolSpec:
    """构造 echo_text 测试工具定义。"""
    return ToolSpec(
        name="echo_text",
        description="Echo text.",
        input_schema={"required": ["text"]},
        is_concurrency_safe=True,
    )


def _echo_handler(tool_call, tool_use_context) -> ToolResultEnvelope:
    """测试用 echo handler。"""
    return ToolResultEnvelope(
        tool_call.tool_use_id,
        tool_call.tool_name,
        True,
        str(tool_call.tool_input["text"]),
    )


def _self_test() -> None:
    """运行本文件单元测试。"""
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestAgentRegistry)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    _self_test()
