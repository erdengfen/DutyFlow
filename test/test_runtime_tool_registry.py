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

    def test_runtime_registry_loads_current_internal_tools(self) -> None:
        """运行时注册表应包含当前项目内置内部工具。"""
        registry = create_runtime_tool_registry()
        self.assertTrue(registry.has("add_contact_knowledge"))
        self.assertTrue(registry.has("create_approval_request"))
        self.assertTrue(registry.has("close_cli_session"))
        self.assertTrue(registry.has("create_background_task"))
        self.assertTrue(registry.has("create_skill"))
        self.assertTrue(registry.has("exec_cli_command"))
        self.assertTrue(registry.has("get_contact_knowledge_detail"))
        self.assertTrue(registry.has("load_skill"))
        self.assertTrue(registry.has("lookup_contact_identity"))
        self.assertTrue(registry.has("lookup_responsibility_context"))
        self.assertTrue(registry.has("lookup_source_context"))
        self.assertTrue(registry.has("open_cli_session"))
        self.assertTrue(registry.has("resume_after_approval"))
        self.assertTrue(registry.has("schedule_background_task"))
        self.assertTrue(registry.has("search_contact_knowledge_headers"))
        self.assertTrue(registry.has("update_contact_knowledge"))

    def test_tool_registry_objects_bind_contract_and_logic(self) -> None:
        """统一注册表中的工具对象应同时具备 contract 和 handle。"""
        create_tool = TOOL_REGISTRY["create_skill"]
        self.assertEqual(create_tool.contract["function"]["name"], create_tool.name)
        self.assertTrue(callable(create_tool.handle))

    def test_runtime_registry_loads_timeout_from_tool_logic(self) -> None:
        """运行时注册表应把工具超时配置加载到 ToolSpec。"""
        registry = create_runtime_tool_registry()
        self.assertEqual(registry.get("load_skill").timeout_seconds, 30.0)

    def test_runtime_registry_loads_retry_policy_fields(self) -> None:
        """运行时注册表应把重试与幂等声明加载到 ToolSpec。"""
        registry = create_runtime_tool_registry()
        add_spec = registry.get("add_contact_knowledge")
        approval_spec = registry.get("create_approval_request")
        close_spec = registry.get("close_cli_session")
        create_background_spec = registry.get("create_background_task")
        create_spec = registry.get("create_skill")
        exec_spec = registry.get("exec_cli_command")
        detail_spec = registry.get("get_contact_knowledge_detail")
        load_spec = registry.get("load_skill")
        contact_spec = registry.get("lookup_contact_identity")
        responsibility_spec = registry.get("lookup_responsibility_context")
        source_spec = registry.get("lookup_source_context")
        open_spec = registry.get("open_cli_session")
        resume_spec = registry.get("resume_after_approval")
        schedule_background_spec = registry.get("schedule_background_task")
        search_spec = registry.get("search_contact_knowledge_headers")
        update_spec = registry.get("update_contact_knowledge")
        self.assertTrue(add_spec.requires_approval)
        self.assertEqual(add_spec.idempotency, "unsafe")
        self.assertFalse(approval_spec.requires_approval)
        self.assertEqual(approval_spec.idempotency, "read_only")
        self.assertFalse(create_background_spec.requires_approval)
        self.assertEqual(create_background_spec.idempotency, "read_only")
        self.assertTrue(create_spec.requires_approval)
        self.assertEqual(create_spec.max_retries, 0)
        self.assertEqual(create_spec.retry_policy, "none")
        self.assertEqual(create_spec.idempotency, "idempotent")
        self.assertEqual(create_spec.degradation_mode, "escalate")
        self.assertFalse(detail_spec.requires_approval)
        self.assertEqual(detail_spec.idempotency, "read_only")
        self.assertEqual(load_spec.max_retries, 0)
        self.assertEqual(load_spec.retry_policy, "none")
        self.assertEqual(load_spec.idempotency, "read_only")
        self.assertEqual(load_spec.degradation_mode, "none")
        self.assertFalse(contact_spec.requires_approval)
        self.assertEqual(contact_spec.idempotency, "read_only")
        self.assertFalse(source_spec.requires_approval)
        self.assertEqual(source_spec.idempotency, "read_only")
        self.assertFalse(responsibility_spec.requires_approval)
        self.assertEqual(responsibility_spec.idempotency, "read_only")
        self.assertFalse(open_spec.requires_approval)
        self.assertEqual(open_spec.idempotency, "read_only")
        self.assertFalse(resume_spec.requires_approval)
        self.assertEqual(resume_spec.idempotency, "read_only")
        self.assertFalse(schedule_background_spec.requires_approval)
        self.assertEqual(schedule_background_spec.idempotency, "read_only")
        self.assertFalse(exec_spec.requires_approval)
        self.assertEqual(exec_spec.idempotency, "read_only")
        self.assertFalse(close_spec.requires_approval)
        self.assertEqual(close_spec.idempotency, "read_only")
        self.assertFalse(search_spec.requires_approval)
        self.assertEqual(search_spec.idempotency, "read_only")
        self.assertTrue(update_spec.requires_approval)
        self.assertEqual(update_spec.idempotency, "unsafe")


def _self_test() -> None:
    """运行本文件单元测试。"""
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestRuntimeToolRegistry)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    _self_test()
