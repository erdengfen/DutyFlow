# 本文件验证后台任务入口工具的最小校验、落盘与运行时行为。

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

from dutyflow.agent.state import create_initial_agent_state  # noqa: E402
from dutyflow.agent.tools.context import ToolUseContext  # noqa: E402
from dutyflow.agent.tools.logic.task_tools.create_background_task import CreateBackgroundTaskTool  # noqa: E402
from dutyflow.agent.tools.logic.task_tools.schedule_background_task import ScheduleBackgroundTaskTool  # noqa: E402
from dutyflow.agent.tools.registry import create_runtime_tool_registry  # noqa: E402
from dutyflow.agent.tools.types import ToolCall  # noqa: E402
from dutyflow.agent.skills import SkillRegistry  # noqa: E402
from dutyflow.tasks.task_state import TaskStore  # noqa: E402


class TestBackgroundTaskTools(unittest.TestCase):
    """验证后台任务入口工具的能力裁决和任务落盘。"""

    def test_create_background_task_writes_queued_task(self) -> None:
        """异步后台任务工具应创建 queued 任务并保存解析后的能力面。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_skill(root, "doc_reader")
            context = _context(root)
            result = CreateBackgroundTaskTool().handle(_create_call(), context)
            payload = _json_content(result)
            record = TaskStore(root).read_task(str(payload["task_id"]))
        self.assertTrue(result.ok)
        self.assertEqual(payload["status"], "queued")
        self.assertEqual(payload["run_mode"], "async_now")
        self.assertEqual(payload["resolved_skills"], ["doc_reader"])
        self.assertEqual(payload["resolved_tools"], ["lookup_contact_identity"])
        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record.status, "queued")
        self.assertEqual(record.execution_profile, "background_async_selected")
        self.assertEqual(record.resolved_skills, "doc_reader")
        self.assertEqual(record.resolved_tools, "lookup_contact_identity")

    def test_schedule_background_task_writes_scheduled_task(self) -> None:
        """定时后台任务工具应创建 scheduled 任务并写入计划时间。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            context = _context(root)
            result = ScheduleBackgroundTaskTool().handle(_schedule_call(), context)
            payload = _json_content(result)
            record = TaskStore(root).read_task(str(payload["task_id"]))
        self.assertTrue(result.ok)
        self.assertEqual(payload["status"], "scheduled")
        self.assertEqual(payload["run_mode"], "run_at")
        self.assertEqual(payload["scheduled_for"], "2026-04-30T09:00:00+08:00")
        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record.status, "scheduled")
        self.assertEqual(record.scheduled_for, "2026-04-30T09:00:00+08:00")

    def test_forbidden_cli_tool_is_rejected(self) -> None:
        """后台任务入口工具应拒绝把 CLI tools 带入后台能力面。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            result = CreateBackgroundTaskTool().handle(
                _create_call(preferred_skills="", preferred_tools="exec_cli_command"),
                _context(root),
            )
        self.assertFalse(result.ok)
        self.assertEqual(result.error_kind, "invalid_background_task_input")
        self.assertIn("forbidden background tools", result.content)

    def test_unknown_skill_is_rejected(self) -> None:
        """后台任务入口工具应拒绝未注册 skill。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            result = CreateBackgroundTaskTool().handle(
                _create_call(preferred_skills="missing_skill"),
                _context(root),
            )
        self.assertFalse(result.ok)
        self.assertEqual(result.error_kind, "invalid_background_task_input")
        self.assertIn("unknown background skills", result.content)


def _create_call(
    *,
    preferred_skills: str = "doc_reader",
    preferred_tools: str = "lookup_contact_identity",
) -> ToolCall:
    """构造 create_background_task 调用。"""
    return ToolCall(
        "tool_bg_create_001",
        "create_background_task",
        {
            "title": "整理新人资料",
            "goal": "汇总当前资料并补充联系人关系线索",
            "success_criteria": "输出一份可读结论并标出缺失信息",
            "user_visible_summary": "已开始整理新人资料。",
            "context_refs": "per_001,evt_001",
            "capability_requirements": "identity_lookup,contact_knowledge",
            "preferred_skills": preferred_skills,
            "preferred_tools": preferred_tools,
        },
        0,
        0,
    )


def _schedule_call() -> ToolCall:
    """构造 schedule_background_task 调用。"""
    return ToolCall(
        "tool_bg_schedule_001",
        "schedule_background_task",
        {
            "title": "明早跟进新人资料",
            "goal": "在明早检查资料补全情况",
            "success_criteria": "返回最新补全状态",
            "scheduled_for": "2026-04-30T09:00:00+08:00",
            "capability_requirements": "identity_lookup",
        },
        0,
        0,
    )


def _context(root: Path) -> ToolUseContext:
    """构造后台任务入口工具的测试上下文。"""
    registry = create_runtime_tool_registry()
    return ToolUseContext(
        "query_background_task_001",
        root,
        create_initial_agent_state("query_background_task_001", "hello"),
        registry,
        skill_registry=SkillRegistry(root / "skills"),
    )


def _write_skill(root: Path, name: str) -> None:
    """在临时项目目录中写入最小 skill 文档。"""
    path = root / "skills" / name / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nname: {name}\ndescription: {name} description\n---\n\n# {name}\n\nbody\n",
        encoding="utf-8",
    )


def _json_content(result) -> dict[str, object]:
    """把工具 JSON 内容转换成字典。"""
    return json.loads(result.content)


def _self_test() -> None:
    """运行本文件单元测试。"""
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestBackgroundTaskTools)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    _self_test()
