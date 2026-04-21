# 本文件验证工具协议对象的基础校验和结果回写转换。

from pathlib import Path
import sys
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dutyflow.agent.state import AgentContentBlock  # noqa: E402
from dutyflow.agent.tools import ToolCall, ToolResultEnvelope, ToolSpec  # noqa: E402


class TestAgentTools(unittest.TestCase):
    """验证 ToolSpec、ToolCall 和 ToolResultEnvelope。"""

    def test_tool_spec_requires_name_and_description(self) -> None:
        """工具定义必须有稳定名称和描述。"""
        with self.assertRaises(ValueError):
            ToolSpec("", "demo")
        with self.assertRaises(ValueError):
            ToolSpec("demo", "")

    def test_tool_call_requires_id_and_name(self) -> None:
        """工具调用必须带 tool_use_id 和 tool_name。"""
        with self.assertRaises(ValueError):
            ToolCall("", "echo_text", {}, 0, 0)
        with self.assertRaises(ValueError):
            ToolCall("tool_1", "", {}, 0, 0)

    def test_tool_call_from_agent_block(self) -> None:
        """tool_use 内容块应能转换为 ToolCall。"""
        block = AgentContentBlock(
            type="tool_use",
            tool_use_id="tool_1",
            tool_name="echo_text",
            tool_input={"text": "hello"},
        )
        call = ToolCall.from_agent_block(block, 2, 3)
        self.assertEqual(call.tool_name, "echo_text")
        self.assertEqual(call.call_index, 3)

    def test_result_envelope_converts_to_agent_block(self) -> None:
        """ToolResultEnvelope 应能转换为 tool_result 内容块。"""
        result = ToolResultEnvelope("tool_1", "echo_text", True, "hello")
        block = result.to_agent_block()
        self.assertEqual(block.type, "tool_result")
        self.assertEqual(block.tool_use_id, "tool_1")
        self.assertEqual(block.content, "hello")

    def test_tool_spec_loads_from_contract(self) -> None:
        """ToolSpec 应支持从 contract 结构加载。"""
        contract = {
            "type": "function",
            "function": {
                "name": "echo_text",
                "description": "Echo text.",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        }
        spec = ToolSpec.from_contract(contract, is_concurrency_safe=True)
        self.assertEqual(spec.name, "echo_text")
        self.assertTrue(spec.is_concurrency_safe)
        self.assertEqual(spec.to_contract()["function"]["description"], "Echo text.")


def _self_test() -> None:
    """运行本文件单元测试。"""
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestAgentTools)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    _self_test()
