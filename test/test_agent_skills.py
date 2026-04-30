# 本文件验证 Step 3 skill 解析层、load_skill 工具和 system message 注入。

from pathlib import Path
import sys
import tempfile
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dutyflow.agent.core_loop import AgentLoop  # noqa: E402
from dutyflow.agent.model_client import ModelResponse  # noqa: E402
from dutyflow.agent.skills import SkillRegistry  # noqa: E402
from dutyflow.agent.state import AgentContentBlock, create_initial_agent_state  # noqa: E402
from dutyflow.agent.tools.context import ToolUseContext  # noqa: E402
from dutyflow.agent.tools.executor import ToolExecutor  # noqa: E402
from dutyflow.agent.tools.logic.skill_tools.create_skill import CreateSkillTool  # noqa: E402
from dutyflow.agent.tools.logic.skill_tools.load_skill import LoadSkillTool  # noqa: E402
from dutyflow.agent.tools.registry import create_runtime_tool_registry  # noqa: E402
from dutyflow.agent.tools.router import ToolRouter  # noqa: E402
from dutyflow.agent.tools.types import ToolCall  # noqa: E402


class TestSkillRegistry(unittest.TestCase):
    """验证技能注册表的初始化加载与文档读取。"""

    def test_project_skills_directory_loads_current_project_skill(self) -> None:
        """项目自带业务技能应能被实际技能目录加载。"""
        registry = SkillRegistry(PROJECT_ROOT / "skills")
        self.assertTrue(registry.has("cli_session_operator"))
        self.assertTrue(registry.has("skill_creator"))
        self.assertIn("cli_session_operator", registry.system_prompt_text())
        self.assertIn("skill_creator", registry.system_prompt_text())
        self.assertIn("exec_cli_command", registry.load_full_text("cli_session_operator"))
        self.assertIn("create_skill", registry.load_full_text("skill_creator"))

    def test_registry_loads_manifest_and_body_from_skill_markdown(self) -> None:
        """注册表应解析 `skills/<name>/SKILL.md` 中的 manifest 和正文。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            skills_dir = Path(temp_dir) / "skills"
            _write_skill(skills_dir, "code-review", "Code review checklist", "# Review\n\nchecklist")
            registry = SkillRegistry(skills_dir)
            manifests = registry.list_manifests()
            self.assertEqual(manifests[0].name, "code-review")
            self.assertEqual(manifests[0].description, "Code review checklist")
            self.assertEqual(registry.load_full_text("code-review"), "# Review\n\nchecklist")

    def test_registry_only_loads_once_without_hot_reload(self) -> None:
        """SkillRegistry 只在初始化时加载，不会自动热重载后续文件修改。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            skills_dir = Path(temp_dir) / "skills"
            skill_path = _write_skill(skills_dir, "git-workflow", "Git workflow", "# Git\n\nfirst")
            registry = SkillRegistry(skills_dir)
            skill_path.write_text(
                "---\nname: git-workflow\ndescription: Git workflow updated\n---\n\n# Git\n\nsecond",
                encoding="utf-8",
            )
            self.assertEqual(registry.get("git-workflow").manifest.description, "Git workflow")
            self.assertEqual(registry.load_full_text("git-workflow"), "# Git\n\nfirst")

    def test_registry_describe_available_only_exposes_manifest_summary(self) -> None:
        """system message 摘要只暴露 manifest，不直接带正文。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            skills_dir = Path(temp_dir) / "skills"
            _write_skill(skills_dir, "doc-cleanup", "Clean up document notes", "# Body\n\nsecret detail")
            registry = SkillRegistry(skills_dir)
            summary = registry.system_prompt_text()
            self.assertIn("Skills available:", summary)
            self.assertIn("- doc-cleanup: Clean up document notes", summary)
            self.assertNotIn("secret detail", summary)

    def test_registry_select_returns_filtered_skill_set(self) -> None:
        """后台 subagent 可基于任务字段获得过滤后的 skill 注册表。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            skills_dir = Path(temp_dir) / "skills"
            _write_skill(skills_dir, "alpha_skill", "Alpha skill", "# Alpha\n\nbody")
            _write_skill(skills_dir, "beta_skill", "Beta skill", "# Beta\n\nbody")
            selected = SkillRegistry(skills_dir).select(("alpha_skill",))
            self.assertTrue(selected.has("alpha_skill"))
            self.assertFalse(selected.has("beta_skill"))
            self.assertIn("alpha_skill", selected.system_prompt_text())
            self.assertNotIn("beta_skill", selected.system_prompt_text())


class TestLoadSkillTool(unittest.TestCase):
    """验证 load_skill 工具从 SkillRegistry 读取完整正文。"""

    def test_load_skill_returns_full_body(self) -> None:
        """load_skill 应能返回指定技能正文。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            skills_dir = Path(temp_dir) / "skills"
            _write_skill(skills_dir, "meeting-summary", "Meeting summary style", "# Meeting\n\nfull body")
            registry = SkillRegistry(skills_dir)
            tool = LoadSkillTool()
            result = tool.handle(_load_skill_call("meeting-summary"), _tool_context(registry))
            self.assertTrue(result.ok)
            self.assertEqual(result.content, "# Meeting\n\nfull body")

    def test_load_skill_returns_explicit_error_for_missing_name(self) -> None:
        """缺失技能时 load_skill 应返回明确错误 envelope。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            registry = SkillRegistry(Path(temp_dir) / "skills")
            tool = LoadSkillTool()
            result = tool.handle(_load_skill_call("missing-skill"), _tool_context(registry))
            self.assertFalse(result.ok)
            self.assertEqual(result.error_kind, "skill_not_found")


class TestCreateSkillTool(unittest.TestCase):
    """验证 create_skill 工具的受控写入和审批约束。"""

    def test_create_skill_direct_handler_writes_skill_markdown(self) -> None:
        """handler 在输入合法且文件不存在时应创建标准 SKILL.md。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            tool = CreateSkillTool()
            call = _create_skill_call("demo_skill")
            result = tool.handle(call, _tool_context(SkillRegistry.empty(Path(temp_dir) / "skills"), Path(temp_dir)))
            self.assertTrue(result.ok)
            created = Path(temp_dir) / "skills" / "demo_skill" / "SKILL.md"
            self.assertTrue(created.exists())
            registry = SkillRegistry(Path(temp_dir) / "skills")
            self.assertEqual(registry.get("demo_skill").manifest.description, "Demo skill")

    def test_create_skill_rejects_invalid_name(self) -> None:
        """非法名称不能写入 skills 目录。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            result = CreateSkillTool().handle(
                _create_skill_call("../bad"),
                _tool_context(SkillRegistry.empty(Path(temp_dir) / "skills"), Path(temp_dir)),
            )
            self.assertFalse(result.ok)
            self.assertEqual(result.error_kind, "invalid_skill_input")

    def test_create_skill_does_not_overwrite_existing_skill(self) -> None:
        """已存在的 skill 默认不能被覆盖。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            skills_dir = Path(temp_dir) / "skills"
            _write_skill(skills_dir, "demo_skill", "Demo skill", "# Old")
            result = CreateSkillTool().handle(
                _create_skill_call("demo_skill"),
                _tool_context(SkillRegistry(skills_dir), Path(temp_dir)),
            )
            self.assertFalse(result.ok)
            self.assertEqual(result.error_kind, "skill_already_exists")
            self.assertEqual(SkillRegistry(skills_dir).load_full_text("demo_skill"), "# Old")

    def test_create_skill_requires_approval_in_executor(self) -> None:
        """通过执行层触发 create_skill 时必须先经过审批。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            registry = create_runtime_tool_registry()
            call = _create_skill_call("approved_skill")
            context = _tool_context(SkillRegistry.empty(Path(temp_dir) / "skills"), Path(temp_dir), True, registry)
            routes = ToolRouter(registry).route_many((call,))
            result = ToolExecutor(registry).execute_routes(routes, context)[0]
            self.assertTrue(result.ok)
            self.assertTrue((Path(temp_dir) / "skills" / "approved_skill" / "SKILL.md").exists())

    def test_create_skill_rejected_approval_does_not_write(self) -> None:
        """审批拒绝时 create_skill 不应写入任何 skill 文件。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            registry = create_runtime_tool_registry()
            call = _create_skill_call("blocked_skill")
            context = _tool_context(SkillRegistry.empty(Path(temp_dir) / "skills"), Path(temp_dir), False, registry)
            routes = ToolRouter(registry).route_many((call,))
            result = ToolExecutor(registry).execute_routes(routes, context)[0]
            self.assertFalse(result.ok)
            self.assertEqual(result.error_kind, "approval_rejected")
            self.assertFalse((Path(temp_dir) / "skills" / "blocked_skill" / "SKILL.md").exists())


class TestAgentLoopSkillInjection(unittest.TestCase):
    """验证 AgentLoop 会把 skills manifest 注入 system message。"""

    def test_loop_injects_skill_manifest_system_message(self) -> None:
        """模型调用前应收到包含 skills 摘要的 system message。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            skills_dir = Path(temp_dir) / "skills"
            _write_skill(skills_dir, "task-triage", "Triage incoming tasks", "# Triage\n\nbody")
            registry = SkillRegistry(skills_dir)
            client = _InspectingModelClient()
            loop = AgentLoop(
                client,
                create_runtime_tool_registry(),
                PROJECT_ROOT,
                skill_registry=registry,
            )
            result = loop.run_until_stop("show skills", query_id="query_skill_001")
            self.assertEqual(result.final_text, "done")
            self.assertEqual(result.state.messages[0].role, "system")
            self.assertIsNotNone(client.last_state)
            first_message = client.last_state.messages[0]
            self.assertEqual(first_message.role, "system")
            system_text = first_message.content[0].text
            self.assertIn("Skills available:", system_text)
            self.assertIn("- task-triage: Triage incoming tasks", system_text)

    def test_loop_does_not_duplicate_system_message_on_second_turn(self) -> None:
        """持续会话进入下一轮时不应重复插入相同的 system message。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            skills_dir = Path(temp_dir) / "skills"
            _write_skill(skills_dir, "task-triage", "Triage incoming tasks", "# Triage\n\nbody")
            registry = SkillRegistry(skills_dir)
            client = _InspectingModelClient()
            loop = AgentLoop(
                client,
                create_runtime_tool_registry(),
                PROJECT_ROOT,
                skill_registry=registry,
            )
            first = loop.run_until_stop("first", query_id="query_skill_002")
            second = loop.run_until_stop("second", state=first.state)
            system_roles = [message.role for message in second.state.messages if message.role == "system"]
            self.assertEqual(len(system_roles), 1)
            self.assertEqual(second.final_text, "done")


class _InspectingModelClient:
    """保存最近一次模型调用输入，供测试检查 system message。"""

    def __init__(self) -> None:
        """初始化空的捕获状态。"""
        self.last_state = None

    def call_model(self, state, tools) -> ModelResponse:
        """返回最小文本响应，并保存收到的 state。"""
        self.last_state = state
        return ModelResponse((AgentContentBlock(type="text", text="done"),), "stop")


def _write_skill(skills_dir: Path, name: str, description: str, body: str) -> Path:
    """在临时目录中写入一个技能文档。"""
    skill_dir = skills_dir / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    path = skill_dir / "SKILL.md"
    path.write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}",
        encoding="utf-8",
    )
    return path


def _load_skill_call(name: str) -> ToolCall:
    """构造测试用 load_skill 工具调用。"""
    return ToolCall(
        tool_use_id="tool_skill_001",
        tool_name="load_skill",
        tool_input={"name": name},
        source_message_index=0,
        call_index=0,
    )


def _create_skill_call(name: str) -> ToolCall:
    """构造测试用 create_skill 工具调用。"""
    return ToolCall(
        tool_use_id="tool_create_skill_001",
        tool_name="create_skill",
        tool_input={
            "name": name,
            "description": "Demo skill",
            "body": "# Demo Skill\n\nDemo body.",
        },
        source_message_index=0,
        call_index=0,
    )


def _tool_context(
    skill_registry: SkillRegistry,
    cwd: Path = PROJECT_ROOT,
    approved: bool | None = None,
    registry=None,
) -> ToolUseContext:
    """构造可供 skill 相关工具读取 SkillRegistry 的工具上下文。"""
    tool_registry = registry or create_runtime_tool_registry()
    requester = None if approved is None else (lambda tool_name, reason, tool_input: approved)
    return ToolUseContext(
        "query_skill_tool",
        cwd,
        create_initial_agent_state("query_skill_tool", "hello"),
        tool_registry,
        approval_requester=requester,
        skill_registry=skill_registry,
    )


def _self_test() -> None:
    """运行本文件单元测试。"""
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    _self_test()
