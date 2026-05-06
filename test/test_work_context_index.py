# 本文件验证 list_work_context 能枚举本地已落盘工作上下文 refs。

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dutyflow.agent.tools.logic.context_tools.list_work_context import ListWorkContextTool  # noqa: E402
from dutyflow.agent.tools.registry import create_runtime_tool_registry  # noqa: E402
from dutyflow.agent.tools.types import ToolCall  # noqa: E402
from dutyflow.approval.approval_flow import ApprovalStore  # noqa: E402
from dutyflow.context.evidence_store import EvidenceStore  # noqa: E402
from dutyflow.context.work_context_index import WorkContextIndexService, WorkContextQuery  # noqa: E402
from dutyflow.feishu.ambient_context import AmbientContextRecord, AmbientContextStore  # noqa: E402
from dutyflow.storage.file_store import FileStore  # noqa: E402
from dutyflow.storage.markdown_store import MarkdownDocument, MarkdownStore  # noqa: E402
from dutyflow.tasks.task_state import TaskStore  # noqa: E402


class TestWorkContextIndex(unittest.TestCase):
    """验证本地工作上下文枚举服务和工具输出。"""

    def test_runtime_registry_has_list_work_context(self) -> None:
        """运行时工具注册表应包含 list_work_context。"""
        registry = create_runtime_tool_registry()
        self.assertTrue(registry.has("list_work_context"))
        self.assertEqual(registry.get("list_work_context").idempotency, "read_only")

    def test_service_lists_all_supported_context_kinds(self) -> None:
        """服务应枚举 ambient、task、approval、evidence 和 report。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _seed_work_context(root)
            result = WorkContextIndexService(root).list_context()
        kinds = {item.kind for item in result.items}
        self.assertEqual(result.total_count, 5)
        self.assertIn("ambient_context", kinds)
        self.assertIn("task", kinds)
        self.assertIn("approval", kinds)
        self.assertIn("evidence", kinds)
        self.assertIn("report", kinds)
        reports = [item for item in result.items if item.kind == "report"]
        self.assertEqual(reports[0].ref_type, "report")

    def test_service_filters_by_query_source_and_status(self) -> None:
        """服务应支持关键词、来源和状态过滤。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _seed_work_context(root)
            service = WorkContextIndexService(root)
            ambient = service.list_context(WorkContextQuery(source_types=("group_message",)))
            tasks = service.list_context(WorkContextQuery(task_statuses=("queued",)))
            approvals = service.list_context(WorkContextQuery(approval_statuses=("waiting",)))
            queried = service.list_context(WorkContextQuery(query="支付"))
        self.assertEqual([item.ref_id for item in ambient.items], ["gm_work_001"])
        self.assertEqual([item.ref_id for item in tasks.items], ["task_work_001"])
        self.assertEqual([item.ref_id for item in approvals.items], ["approval_work_001"])
        self.assertTrue(any(item.kind == "ambient_context" for item in queried.items))

    def test_service_filters_by_date_and_limit(self) -> None:
        """服务应支持日期和 limit 过滤。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _seed_work_context(root)
            result = WorkContextIndexService(root).list_context(
                WorkContextQuery(date="2026-05-07", source_types=("group_message",), limit=1)
            )
        self.assertEqual(len(result.items), 1)
        self.assertEqual(result.total_count, 1)

    def test_tool_returns_json_payload(self) -> None:
        """工具应返回稳定 JSON，条目可继续交给 read_context_ref。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _seed_work_context(root)
            result = _call_tool(root, {"source_types": "group_message", "limit": 5})
        self.assertTrue(result.ok)
        payload = json.loads(result.content)
        self.assertEqual(payload["returned_count"], 1)
        item = payload["items"][0]
        self.assertEqual(item["ref_type"], "ambient_context")
        self.assertEqual(item["ref_id"], "gm_work_001")


def _call_tool(root: Path, tool_input: dict[str, object]):
    """调用 list_work_context 工具。"""
    ctx = MagicMock()
    ctx.cwd = root
    call = ToolCall("tool_list_work", "list_work_context", tool_input, 0, 0)
    return ListWorkContextTool().handle(call, ctx)


def _seed_work_context(root: Path) -> None:
    """写入测试用本地工作上下文数据。"""
    AmbientContextStore(root).write(
        AmbientContextRecord(
            record_id="gm_work_001",
            source_type="group_message",
            collector_name="group_message_collector",
            source_id="oc_group",
            sync_scope_id="oc_group",
            created_at="2026-05-07T09:00:00+08:00",
            fetched_at="2026-05-07T09:01:00+08:00",
            text="支付回调超时需要下午处理。",
            summary="群聊提示支付回调风险。",
        )
    )
    TaskStore(root).create_task(
        title="跟进支付回调",
        task_id="task_work_001",
        status="queued",
        summary="下午确认支付回调修复结果。",
    )
    ApprovalStore(root).create_approval(
        task_id="task_work_approval",
        requested_action="enable_feishu_scope",
        risk_level="high",
        request="DutyFlow向您请求*青桐上线群*阅读权限",
        reason="需要读取群聊消息判断上线风险。",
        risk="会把授权范围内消息保存到本地。",
        approval_id="approval_work_001",
    )
    EvidenceStore(root).save_content(
        source_type="manual",
        source_id="doc_work",
        evidence_id="evid_work_001",
        content="支付风险台账正文。",
        summary="支付风险台账摘录。",
    )
    MarkdownStore(FileStore(root)).write_document(
        "data/reports/report_work_001.md",
        MarkdownDocument(
            {"schema": "dutyflow.report.v1", "report_id": "report_work_001", "created_at": "2026-05-07T10:00:00+08:00"},
            "# 青桐今日总结\n\n支付回调仍是最高风险。",
        ),
    )


def _self_test() -> None:
    """运行本文件单元测试。"""
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestWorkContextIndex)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    _self_test()
