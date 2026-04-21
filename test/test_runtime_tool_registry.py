# 本文件验证 agent tools 包内的运行时工具注册入口能从 contract 层装载工具定义。

from pathlib import Path
import sys
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dutyflow.agent.tools.registry import TOOL_REGISTRY, create_runtime_tool_registry  # noqa: E402


class TestRuntimeToolRegistry(unittest.TestCase):
    """验证 agent tools 包内的运行时工具注册入口。"""

    def test_runtime_registry_loads_echo_and_fail_tools(self) -> None:
        """运行时注册表应包含迁移后的占位工具。"""
        registry = create_runtime_tool_registry()
        self.assertTrue(registry.has("echo_text"))
        self.assertTrue(registry.has("fail_tool"))

    def test_tool_registry_objects_bind_contract_and_logic(self) -> None:
        """统一注册表中的工具对象应同时具备 contract 和 handle。"""
        echo_tool = TOOL_REGISTRY["echo_text"]
        self.assertEqual(echo_tool.contract["function"]["name"], echo_tool.name)
        self.assertTrue(callable(echo_tool.handle))


def _self_test() -> None:
    """运行本文件单元测试。"""
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestRuntimeToolRegistry)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    _self_test()
