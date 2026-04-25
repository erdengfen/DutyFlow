# 本文件验证联系人知识查询链和写入工具的运行时行为。

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
from dutyflow.agent.tools.executor import ToolExecutor  # noqa: E402
from dutyflow.agent.tools.logic.add_contact_knowledge import AddContactKnowledgeTool  # noqa: E402
from dutyflow.agent.tools.logic.get_contact_knowledge_detail import GetContactKnowledgeDetailTool  # noqa: E402
from dutyflow.agent.tools.logic.search_contact_knowledge_headers import SearchContactKnowledgeHeadersTool  # noqa: E402
from dutyflow.agent.tools.logic.update_contact_knowledge import UpdateContactKnowledgeTool  # noqa: E402
from dutyflow.agent.tools.registry import create_runtime_tool_registry  # noqa: E402
from dutyflow.agent.tools.router import ToolRouter  # noqa: E402
from dutyflow.agent.tools.types import ToolCall  # noqa: E402


class TestContactKnowledgeTools(unittest.TestCase):
    """验证联系人知识工具的查询与审批行为。"""

    def test_search_headers_supports_contact_id_and_name(self) -> None:
        """搜索工具应支持 contact_id 与 name 两种入口。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_contact_index(root)
            _write_contact_note(root, "contact_001", "ckn_001", "async review")
            tool = SearchContactKnowledgeHeadersTool()
            result = tool.handle(_search_call(contact_id="contact_001"), _context(root))
            by_name = tool.handle(_search_call(name="张三"), _context(root))
            payload = _json_content(result)
            payload_by_name = _json_content(by_name)
        self.assertEqual(payload["match_status"], "unique")
        self.assertEqual(payload["headers"][0]["note_id"], "ckn_001")
        self.assertEqual(payload_by_name["headers"][0]["contact_id"], "contact_001")

    def test_get_detail_returns_trimmed_sections(self) -> None:
        """detail 工具应返回指定 section，而不是整份原文。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_contact_note(root, "contact_001", "ckn_001", "async review")
            result = GetContactKnowledgeDetailTool().handle(_detail_call("ckn_001"), _context(root))
            payload = _json_content(result)
        self.assertTrue(result.ok)
        self.assertEqual(payload["note_id"], "ckn_001")
        self.assertEqual(payload["summary"], "async review")
        self.assertIn("review_style", payload["structured_facts"])

    def test_add_and_update_require_approval_in_executor(self) -> None:
        """写入类工具应通过执行层进入审批。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            registry = create_runtime_tool_registry()
            add_context = _context(root, approved=False, registry=registry)
            add_result = _execute(registry, _add_call(), add_context)
            self.assertFalse(add_result.ok)
            self.assertEqual(add_result.error_kind, "approval_rejected")
            allowed_context = _context(root, approved=True, registry=registry)
            created = _execute(registry, _add_call(), allowed_context)
            note_id = _json_content(created)["note_id"]
            updated = _execute(registry, _update_call(note_id), allowed_context)
        self.assertTrue(created.ok)
        self.assertTrue(updated.ok)
        self.assertIn("updated", _json_content(updated)["status"])

    def test_direct_add_and_update_handlers_write_files(self) -> None:
        """直接 handler 调用应能创建并更新联系人知识文件。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            add_tool = AddContactKnowledgeTool()
            update_tool = UpdateContactKnowledgeTool()
            created = add_tool.handle(_add_call(), _context(root))
            note_id = _json_content(created)["note_id"]
            updated = update_tool.handle(_update_call(note_id), _context(root))
            detail = GetContactKnowledgeDetailTool().handle(_detail_call(note_id), _context(root))
            payload = _json_content(detail)
        self.assertTrue(created.ok)
        self.assertTrue(updated.ok)
        self.assertEqual(payload["decision_value"], "escalate only urgent pings")
        self.assertIn("manually reviewed", payload["change_log_preview"])


def _search_call(contact_id: str = "", name: str = "") -> ToolCall:
    """构造 search headers 工具调用。"""
    tool_input = {"topic": "working_preference"}
    if contact_id:
        tool_input["contact_id"] = contact_id
    if name:
        tool_input["name"] = name
    return ToolCall("tool_ck_search_001", "search_contact_knowledge_headers", tool_input, 0, 0)


def _detail_call(note_id: str) -> ToolCall:
    """构造 detail 工具调用。"""
    return ToolCall("tool_ck_detail_001", "get_contact_knowledge_detail", {"note_id": note_id}, 0, 0)


def _add_call() -> ToolCall:
    """构造 add 工具调用。"""
    return ToolCall(
        "tool_ck_add_001",
        "add_contact_knowledge",
        {
            "contact_id": "contact_001",
            "topic": "working_preference",
            "keywords": "async, review",
            "summary": "prefers async review",
            "structured_facts_markdown": "| fact_key | fact_value | confidence | source_ref |\n|---|---|---|---|\n| review_style | async first | medium | manual_input |",
            "decision_value": "ask asynchronously before scheduling meetings",
            "source_refs": "manual_input",
        },
        0,
        0,
    )


def _update_call(note_id: str) -> ToolCall:
    """构造 update 工具调用。"""
    return ToolCall(
        "tool_ck_update_001",
        "update_contact_knowledge",
        {
            "note_id": note_id,
            "decision_value": "escalate only urgent pings",
            "change_note": "manually reviewed",
        },
        0,
        0,
    )


def _context(root: Path, approved: bool | None = None, registry=None) -> ToolUseContext:
    """构造联系人知识工具测试上下文。"""
    tool_registry = registry or create_runtime_tool_registry()
    requester = None if approved is None else (lambda tool_name, reason, tool_input: approved)
    return ToolUseContext(
        "query_ck_001",
        root,
        create_initial_agent_state("query_ck_001", "hello"),
        tool_registry,
        approval_requester=requester,
    )


def _execute(registry, call: ToolCall, context: ToolUseContext):
    """通过真实执行层运行一次工具调用。"""
    routes = ToolRouter(registry).route_many((call,))
    return ToolExecutor(registry).execute_routes(routes, context)[0]


def _write_contact_index(root: Path) -> None:
    """写入联系人索引，用于 name -> contact_id 解析。"""
    path = root / "data" / "identity" / "contacts" / "index.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        (
            "---\n"
            "schema: dutyflow.contact_index.v1\n"
            "id: contact_index\n"
            "updated_at: 2026-04-25T00:00:00+00:00\n"
            "---\n\n"
            "# Contact Index\n\n"
            "| contact_id | display_name | aliases | feishu_user_id | feishu_open_id | department | org_level | detail_file |\n"
            "|---|---|---|---|---|---|---|---|\n"
            "| contact_001 | 张三 | 三哥, zhangsan | ou_001 | open_001 | 产品部 | manager | people/contact_001.md |\n"
        ),
        encoding="utf-8",
    )


def _write_contact_note(root: Path, contact_id: str, note_id: str, summary: str) -> None:
    """写入联系人知识记录 fixture。"""
    path = root / "data" / "knowledge" / "contacts" / contact_id / f"{note_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        (
            "---\n"
            "schema: dutyflow.contact_knowledge_note.v1\n"
            f"id: {note_id}\n"
            f"contact_id: {contact_id}\n"
            "topic: working_preference\n"
            "keywords: async, review\n"
            "confidence: medium\n"
            "status: active\n"
            "source_refs: evt_001\n"
            "created_at: 2026-04-25T00:00:00+00:00\n"
            "updated_at: 2026-04-25T00:00:00+00:00\n"
            "---\n\n"
            f"# Contact Knowledge {note_id}\n\n"
            "## Summary\n\n"
            f"{summary}\n\n"
            "## Structured Facts\n\n"
            "| fact_key | fact_value | confidence | source_ref |\n"
            "|---|---|---|---|\n"
            "| review_style | async first | medium | evt_001 |\n\n"
            "## Decision Value\n\n"
            "prefer async before meetings\n\n"
            "## Change Log\n\n"
            "| at | action | note |\n"
            "|---|---|---|\n"
            "| 2026-04-25T00:00:00+00:00 | created | initial |\n"
        ),
        encoding="utf-8",
    )


def _json_content(result) -> dict[str, object]:
    """把工具 JSON 结果转换成字典。"""
    return json.loads(result.content)


def _self_test() -> None:
    """运行本文件单元测试。"""
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestContactKnowledgeTools)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    _self_test()
