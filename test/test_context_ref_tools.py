# 本文件验证 read_context_ref 工具可读取本地已落盘的关键上下文引用。

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

from dutyflow.agent.tools.logic.context_tools.read_context_ref import ReadContextRefTool  # noqa: E402
from dutyflow.agent.tools.registry import create_runtime_tool_registry  # noqa: E402
from dutyflow.agent.tools.types import ToolCall  # noqa: E402
from dutyflow.approval.approval_flow import ApprovalStore  # noqa: E402
from dutyflow.context.evidence_store import EvidenceStore  # noqa: E402
from dutyflow.feishu.ambient_context import (  # noqa: E402
    AmbientContextRecord,
    AmbientContextStore,
    AmbientDocLink,
)
from dutyflow.feishu.events import FeishuEventAdapter  # noqa: E402
from dutyflow.perception.store import PerceptionRecordService  # noqa: E402
from dutyflow.storage.file_store import FileStore  # noqa: E402
from dutyflow.storage.markdown_store import MarkdownDocument, MarkdownStore  # noqa: E402
from dutyflow.tasks.task_result import TaskResultStore  # noqa: E402
from dutyflow.tasks.task_state import TaskStore  # noqa: E402


class TestReadContextRefTool(unittest.TestCase):
    """验证上下文引用读取工具的注册、输入校验和各类记录读取。"""

    def test_runtime_registry_has_read_context_ref(self) -> None:
        """运行时工具注册表应包含 read_context_ref。"""
        registry = create_runtime_tool_registry()
        self.assertTrue(registry.has("read_context_ref"))
        self.assertEqual(registry.get("read_context_ref").idempotency, "read_only")

    def test_read_perception_ref(self) -> None:
        """工具应可读取 perception 记录，并返回事件锚点。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_perception(root)
            result = _read(root, "perception", "per_om_ctx_001")
        self.assertTrue(result.ok)
        payload = _payload(result)
        self.assertEqual(payload["ref_type"], "perception")
        self.assertEqual(payload["anchors"]["message_id"], "om_ctx_001")
        self.assertIn("需要总结项目风险", payload["text_preview"])

    def test_read_ambient_context_ref(self) -> None:
        """工具应可读取 ambient_context 记录和文档 token 线索。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_ambient(root)
            result = _read(root, "ambient_context", "gm_om_ctx_001")
        self.assertTrue(result.ok)
        payload = _payload(result)
        self.assertEqual(payload["anchors"]["sync_scope_id"], "oc_group")
        self.assertEqual(payload["payload"]["doc_links"][0]["token"], "doxcn_ctx")
        self.assertIn("会议纪要", payload["text_preview"])

    def test_read_evidence_ref_from_handle(self) -> None:
        """工具应接受 evidence:path 形式并只返回正文预览。"""
        long_content = "A" * 1400
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record = EvidenceStore(root).save_content(
                source_type="manual",
                source_id="manual_ctx",
                content=long_content,
                evidence_id="evid_ctx_001",
            )
            result = _read(root, "evidence", record.to_ref())
        self.assertTrue(result.ok)
        payload = _payload(result)
        self.assertEqual(payload["ref_id"], "evid_ctx_001")
        self.assertLess(len(payload["text_preview"]), len(long_content))
        self.assertEqual(payload["payload"]["content_size"], str(len(long_content)))

    def test_read_task_ref_with_result(self) -> None:
        """工具读取 task 时应附带任务结果摘要。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_task_with_result(root)
            result = _read(root, "task", "task_ctx_001")
        self.assertTrue(result.ok)
        payload = _payload(result)
        self.assertEqual(payload["anchors"]["task_result_id"], "result_task_ctx_001")
        self.assertIn("用户可见结果", payload["text_preview"])
        self.assertEqual(payload["payload"]["task_result"]["status"], "completed")

    def test_read_approval_ref(self) -> None:
        """工具应可读取审批记录的请求、风险和任务锚点。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ApprovalStore(root).create_approval(
                task_id="task_ctx_approval",
                requested_action="enable_feishu_scope",
                risk_level="high",
                request="DutyFlow向您请求*群聊 oc_group*阅读权限",
                reason="需要读取群聊消息用于事项判断。",
                risk="会在本地保存群消息上下文。",
                approval_id="approval_ctx_001",
            )
            result = _read(root, "approval", "approval_ctx_001")
        self.assertTrue(result.ok)
        payload = _payload(result)
        self.assertEqual(payload["anchors"]["task_id"], "task_ctx_approval")
        self.assertEqual(payload["payload"]["requested_action"], "enable_feishu_scope")

    def test_read_report_ref(self) -> None:
        """工具应可读取本地 report 记录。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_report(root)
            result = _read(root, "report", "report_ctx_001")
        self.assertTrue(result.ok)
        payload = _payload(result)
        self.assertEqual(payload["ref_type"], "report")
        self.assertEqual(payload["anchors"]["source_task_id"], "task_ctx_001")
        self.assertIn("今日重点", payload["text_preview"])

    def test_missing_ref_returns_error_envelope(self) -> None:
        """不存在的引用应返回稳定错误，不抛出异常。"""
        with tempfile.TemporaryDirectory() as tmp:
            result = _read(Path(tmp), "task", "task_missing")
        self.assertFalse(result.ok)
        self.assertEqual(result.error_kind, "not_found")


def _read(root: Path, ref_type: str, ref_id: str):
    """调用 read_context_ref 工具。"""
    ctx = MagicMock()
    ctx.cwd = root
    call = ToolCall("tool_read_ctx", "read_context_ref", {"ref_type": ref_type, "ref_id": ref_id}, 0, 0)
    return ReadContextRefTool().handle(call, ctx)


def _payload(result) -> dict[str, object]:
    """读取工具 JSON 结果。"""
    return json.loads(result.content)


def _write_perception(root: Path) -> None:
    """写入测试用 perception 记录。"""
    adapter = FeishuEventAdapter()
    raw = adapter.create_local_fixture_event(
        "需要总结项目风险",
        event_id="evt_ctx_001",
        message_id="om_ctx_001",
    )
    envelope = adapter.build_event_envelope(raw, received_at="2026-05-07T09:00:00+08:00")
    raw_path = root / "data/events/evt_ctx_001.md"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text("# raw", encoding="utf-8")
    PerceptionRecordService(root).create_record(envelope, raw_path)


def _write_ambient(root: Path) -> None:
    """写入测试用 ambient_context 记录。"""
    AmbientContextStore(root).write(
        AmbientContextRecord(
            record_id="gm_om_ctx_001",
            source_type="group_message",
            collector_name="group_message_collector",
            source_id="oc_group",
            sync_scope_id="oc_group",
            created_at="2026-05-07T09:10:00+08:00",
            fetched_at="2026-05-07T09:11:00+08:00",
            text="请看会议纪要 https://example.feishu.cn/docx/doxcn_ctx",
            summary="群聊中出现会议纪要。",
            doc_links=(AmbientDocLink("https://example.feishu.cn/docx/doxcn_ctx", "docx", "doxcn_ctx"),),
        )
    )


def _write_task_with_result(root: Path) -> None:
    """写入测试用任务和任务结果。"""
    store = TaskStore(root)
    task = store.create_task(
        title="总结项目风险",
        task_id="task_ctx_001",
        status="completed",
        summary="整理群聊中的项目风险。",
        last_result_summary="已完成。",
        next_action="结果已回推。",
    )
    TaskResultStore(root).update_result(
        task,
        status="completed",
        summary="任务结果摘要。",
        user_visible_final_text="用户可见结果：风险有三项。",
        stop_reason="stop",
        tool_result_count=1,
        query_id="bg_task_ctx_001",
        raw_result="用户可见结果：风险有三项。",
    )


def _write_report(root: Path) -> None:
    """写入测试用 report 记录。"""
    MarkdownStore(FileStore(root)).write_document(
        "data/reports/report_ctx_001.md",
        MarkdownDocument(
            {
                "schema": "dutyflow.report.v1",
                "report_id": "report_ctx_001",
                "source_task_id": "task_ctx_001",
                "created_at": "2026-05-07T10:00:00+08:00",
            },
            "# 今日重点\n\n## Summary\n\n项目风险有三项。",
        ),
    )


def _self_test() -> None:
    """运行本文件单元测试。"""
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestReadContextRefTool)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    _self_test()
