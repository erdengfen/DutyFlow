# 本文件验证 Compression Journal 的写入、读取、锚点收集和去重机制。

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sys
import tempfile
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dutyflow.agent.state import (  # noqa: E402
    AgentContentBlock,
    AgentMessage,
    AgentTaskControl,
    AgentState,
    create_initial_agent_state,
)
from dutyflow.context.compression_journal import (  # noqa: E402
    COMPRESSION_JOURNAL_SCHEMA,
    CompressionJournalRecord,
    CompressionJournalStore,
)
from dutyflow.context.runtime_context import RuntimeContextManager  # noqa: E402
from dutyflow.storage.file_store import FileStore  # noqa: E402
from dutyflow.storage.markdown_store import MarkdownStore  # noqa: E402


def _make_tool_result_message(tool_use_id: str, content: str) -> AgentMessage:
    return AgentMessage(
        role="user",
        content=(
            AgentContentBlock(
                type="tool_result",
                tool_use_id=tool_use_id,
                tool_name="sample_tool",
                content=content,
            ),
        ),
    )


def _make_receipt_message(tool_use_id: str) -> AgentMessage:
    return _make_tool_result_message(
        tool_use_id,
        f"ToolReceipt(tool_use_id={tool_use_id},status=success,summary=done)",
    )


def _make_state(
    query_id: str = "query_001",
    task_id: str = "",
    event_id: str = "",
    messages: tuple[AgentMessage, ...] = (),
) -> AgentState:
    base = create_initial_agent_state(query_id, "test input", current_event_id=event_id)
    state = replace(
        base,
        current_task_id=task_id,
        task_control=AgentTaskControl(task_id=task_id),
        messages=base.messages + messages,
    )
    return state


class TestCompressionJournalStoreWrite(unittest.TestCase):
    """验证 journal store 基本写入与读取路径。"""

    def test_write_micro_compact_creates_file_with_correct_schema(self) -> None:
        """micro-compact journal 应写入标准 schema 的 Markdown 文件。"""
        source = (_make_tool_result_message("tool_001", '{"result":"long content"}'),)
        projected = (_make_receipt_message("tool_001"),)
        state = _make_state(task_id="task_001", event_id="evt_001", messages=source)
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = CompressionJournalStore(root)
            record = store.write_projection_change(
                state=state,
                source_messages=source,
                projected_messages=projected,
                budget=None,
            )
            document = MarkdownStore(FileStore(root)).read_document(record.path)

        self.assertEqual(document.frontmatter["schema"], COMPRESSION_JOURNAL_SCHEMA)
        self.assertEqual(document.frontmatter["action_type"], "micro_compact")
        self.assertEqual(document.frontmatter["query_id"], "query_001")
        self.assertEqual(document.frontmatter["task_id"], "task_001")
        self.assertEqual(document.frontmatter["event_id"], "evt_001")
        self.assertTrue(record.journal_id.startswith("ctxj_"))
        self.assertEqual(record.path.name, f"{record.journal_id}.md")

    def test_write_model_context_projection_when_no_compact(self) -> None:
        """投影前后 messages 相同时应记录为 model_context_projection 而非 micro_compact。"""
        source = (_make_tool_result_message("tool_002", "short"),)
        state = _make_state(messages=source)
        with tempfile.TemporaryDirectory() as temp_dir:
            store = CompressionJournalStore(Path(temp_dir))
            record = store.write_projection_change(
                state=state,
                source_messages=source,
                projected_messages=source,
                budget=None,
            )
        self.assertEqual(record.action_type, "model_context_projection")
        self.assertEqual(record.compacted_tool_result_ids, ())

    def test_read_journal_round_trips_key_fields(self) -> None:
        """写入后读回应保留 journal_id、action_type、锚点字段。"""
        source = (_make_tool_result_message("tool_003", "original"),)
        projected = (_make_receipt_message("tool_003"),)
        state = _make_state(task_id="task_abc", event_id="evt_abc", messages=source)
        with tempfile.TemporaryDirectory() as temp_dir:
            store = CompressionJournalStore(Path(temp_dir))
            written = store.write_projection_change(
                state=state,
                source_messages=source,
                projected_messages=projected,
                budget=None,
                notes="测试回程记录。",
            )
            loaded = store.read_journal(written.journal_id)

        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.journal_id, written.journal_id)
        self.assertEqual(loaded.action_type, "micro_compact")
        self.assertEqual(loaded.compacted_tool_result_ids, ("tool_003",))
        self.assertEqual(loaded.preserved_task_ids, ("task_abc",))
        self.assertEqual(loaded.preserved_event_ids, ("evt_abc",))

    def test_read_journal_returns_none_for_missing_id(self) -> None:
        """读取不存在的 journal ID 应返回 None。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            store = CompressionJournalStore(Path(temp_dir))
            result = store.read_journal("ctxj_nonexistent")
        self.assertIsNone(result)

    def test_list_journals_returns_all_written_records(self) -> None:
        """list_journals 应按文件名顺序返回目录中的全部记录。"""
        state = _make_state()
        source = (_make_tool_result_message("tool_l1", "data"),)
        projected = (_make_receipt_message("tool_l1"),)
        with tempfile.TemporaryDirectory() as temp_dir:
            store = CompressionJournalStore(Path(temp_dir))
            r1 = store.write_projection_change(
                state=state,
                source_messages=source,
                projected_messages=projected,
                budget=None,
            )
            r2 = store.write_projection_change(
                state=state,
                source_messages=source,
                projected_messages=projected,
                budget=None,
            )
            records = store.list_journals()

        ids = {r.journal_id for r in records}
        self.assertIn(r1.journal_id, ids)
        self.assertIn(r2.journal_id, ids)
        self.assertEqual(len(records), 2)


class TestCompressionJournalAnchorCollection(unittest.TestCase):
    """验证锚点收集从 state 和 messages 中同时抓取 ID。"""

    def test_task_id_from_state_is_preserved(self) -> None:
        """state.current_task_id 应出现在 preserved_task_ids。"""
        state = _make_state(task_id="task_from_state")
        with tempfile.TemporaryDirectory() as temp_dir:
            store = CompressionJournalStore(Path(temp_dir))
            record = store.write_projection_change(
                state=state,
                source_messages=(),
                projected_messages=(),
                budget=None,
            )
        self.assertIn("task_from_state", record.preserved_task_ids)

    def test_task_id_from_message_content_is_preserved(self) -> None:
        """messages 内容中出现的 task_id 应被收集到 preserved_task_ids。"""
        msg = AgentMessage(
            role="assistant",
            content=(
                AgentContentBlock(
                    type="text",
                    text='后台任务 task_from_msg 已创建。',
                ),
            ),
        )
        state = _make_state(messages=(msg,))
        with tempfile.TemporaryDirectory() as temp_dir:
            store = CompressionJournalStore(Path(temp_dir))
            record = store.write_projection_change(
                state=state,
                source_messages=(msg,),
                projected_messages=(msg,),
                budget=None,
            )
        self.assertIn("task_from_msg", record.preserved_task_ids)

    def test_event_id_from_state_is_preserved(self) -> None:
        """state.current_event_id 应出现在 preserved_event_ids。"""
        state = _make_state(event_id="evt_from_state")
        with tempfile.TemporaryDirectory() as temp_dir:
            store = CompressionJournalStore(Path(temp_dir))
            record = store.write_projection_change(
                state=state,
                source_messages=(),
                projected_messages=(),
                budget=None,
            )
        self.assertIn("evt_from_state", record.preserved_event_ids)

    def test_tool_use_id_from_message_block_is_preserved(self) -> None:
        """messages 中 block.tool_use_id 应被收集到 preserved_tool_use_ids。"""
        source = (_make_tool_result_message("tool_anchor_01", "data"),)
        state = _make_state(messages=source)
        with tempfile.TemporaryDirectory() as temp_dir:
            store = CompressionJournalStore(Path(temp_dir))
            record = store.write_projection_change(
                state=state,
                source_messages=source,
                projected_messages=source,
                budget=None,
            )
        self.assertIn("tool_anchor_01", record.preserved_tool_use_ids)

    def test_approval_id_in_message_text_is_preserved(self) -> None:
        """消息文本中的 approval_xxx ID 应被收集到 preserved_approval_ids。"""
        msg = AgentMessage(
            role="assistant",
            content=(
                AgentContentBlock(type="text", text="审批 approval_xyz123 正在等待。"),
            ),
        )
        state = _make_state(messages=(msg,))
        with tempfile.TemporaryDirectory() as temp_dir:
            store = CompressionJournalStore(Path(temp_dir))
            record = store.write_projection_change(
                state=state,
                source_messages=(msg,),
                projected_messages=(msg,),
                budget=None,
            )
        self.assertIn("approval_xyz123", record.preserved_approval_ids)


class TestCompressionJournalPhaseSummaryEvent(unittest.TestCase):
    """验证 write_phase_summary_event 对阶段边界和 LLM 摘要的区分。"""

    def _make_trigger(self, reason: str, *, requires_llm: bool = False, dedupe_key: str = "") -> object:
        class Trigger:
            pass
        t = Trigger()
        t.reason = reason
        t.requires_llm = requires_llm
        t.should_record_boundary = True
        t.dedupe_key = dedupe_key or f"key_{reason}"
        return t

    def test_phase_boundary_trigger_writes_phase_boundary_action(self) -> None:
        """非 LLM 触发应写入 action_type=phase_boundary。"""
        trigger = self._make_trigger("phase_boundary_only", requires_llm=False)
        state = _make_state()
        with tempfile.TemporaryDirectory() as temp_dir:
            store = CompressionJournalStore(Path(temp_dir))
            record = store.write_phase_summary_event(
                state=state,
                projected_messages=(),
                budget=None,
                trigger=trigger,
                phase_summary_record=None,
            )
        self.assertEqual(record.action_type, "phase_boundary")
        self.assertEqual(record.trigger_reason, "phase_boundary_only")
        self.assertEqual(record.phase_summary_id, "")

    def test_llm_phase_summary_trigger_writes_phase_summary_action(self) -> None:
        """LLM 阶段摘要触发应写入 action_type=phase_summary，并保留 phase_summary_id。"""
        trigger = self._make_trigger("budget_hard_limit", requires_llm=True)

        class FakeSummaryRecord:
            summary_id = "ctx_abc"
            relative_path = "data/contexts/ctx_abc.md"

        state = _make_state()
        with tempfile.TemporaryDirectory() as temp_dir:
            store = CompressionJournalStore(Path(temp_dir))
            record = store.write_phase_summary_event(
                state=state,
                projected_messages=(),
                budget=None,
                trigger=trigger,
                phase_summary_record=FakeSummaryRecord(),
            )
        self.assertEqual(record.action_type, "phase_summary")
        self.assertEqual(record.phase_summary_id, "ctx_abc")
        self.assertEqual(record.phase_summary_file, "data/contexts/ctx_abc.md")

    def test_context_overflow_trigger_writes_phase_summary_action(self) -> None:
        """context_overflow 触发即使 requires_llm=False 也应标记为 phase_summary。"""
        trigger = self._make_trigger("context_overflow", requires_llm=False)
        state = _make_state()
        with tempfile.TemporaryDirectory() as temp_dir:
            store = CompressionJournalStore(Path(temp_dir))
            record = store.write_phase_summary_event(
                state=state,
                projected_messages=(),
                budget=None,
                trigger=trigger,
                phase_summary_record=None,
            )
        self.assertEqual(record.action_type, "phase_summary")


class TestCompressionJournalRuntimeContextIntegration(unittest.TestCase):
    """验证 RuntimeContextManager 在 micro-compact 发生时自动写入 journal。"""

    def test_micro_compact_projection_writes_journal(self) -> None:
        """当投影将 tool result 替换为 Tool Receipt 时，journal 应自动落盘。"""
        source_msg = _make_tool_result_message("tool_rcm_01", "full original content")
        receipt_msg = _make_receipt_message("tool_rcm_01")
        state = _make_state(messages=(source_msg,))
        # state.messages = (initial_user_msg, source_msg)
        # projected_messages 需与 state.messages 等长，只替换最后一条
        projected_messages = state.messages[:-1] + (receipt_msg,)
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            journal_store = CompressionJournalStore(root)
            manager = RuntimeContextManager(compression_journal_store=journal_store)
            manager._record_projection_change_journal(state, projected_messages)
            records = journal_store.list_journals()

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].action_type, "micro_compact")
        self.assertIn("tool_rcm_01", records[0].compacted_tool_result_ids)

    def test_no_journal_when_messages_unchanged(self) -> None:
        """投影前后 messages 相同时，不应写入 journal。"""
        source_msg = _make_tool_result_message("tool_unchanged", "data")
        state = _make_state(messages=(source_msg,))
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            journal_store = CompressionJournalStore(root)
            manager = RuntimeContextManager(compression_journal_store=journal_store)
            # projected_messages == state.messages，无变化
            manager._record_projection_change_journal(state, state.messages)
            records = journal_store.list_journals()

        self.assertEqual(len(records), 0)

    def test_deduplicate_repeated_micro_compact_for_same_ids(self) -> None:
        """相同 query_id + tool_use_id 组合重复调用时，journal 不重复写入。"""
        source_msg = _make_tool_result_message("tool_dedup", "data")
        receipt_msg = _make_receipt_message("tool_dedup")
        state = _make_state(messages=(source_msg,))
        projected_messages = state.messages[:-1] + (receipt_msg,)
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            journal_store = CompressionJournalStore(root)
            manager = RuntimeContextManager(compression_journal_store=journal_store)
            manager._record_projection_change_journal(state, projected_messages)
            manager._record_projection_change_journal(state, projected_messages)
            records = journal_store.list_journals()

        self.assertEqual(len(records), 1)

    def test_journal_not_written_when_store_is_none(self) -> None:
        """compression_journal_store=None 时，_record_projection_change_journal 应静默跳过。"""
        source_msg = _make_tool_result_message("tool_no_store", "data")
        receipt_msg = _make_receipt_message("tool_no_store")
        state = _make_state(messages=(source_msg,))
        manager = RuntimeContextManager(compression_journal_store=None)
        # 不应抛出，也不应写任何文件
        manager._record_projection_change_journal(state, (receipt_msg,))
        self.assertIsNone(manager.latest_compression_journal_record)


class TestCompressionJournalFileLayout(unittest.TestCase):
    """验证 journal 文件落在正确目录，frontmatter 无损。"""

    def test_journal_file_stored_in_contexts_journal_dir(self) -> None:
        """journal 文件应落在 data/contexts/journal/ 下。"""
        state = _make_state()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = CompressionJournalStore(root)
            record = store.write_projection_change(
                state=state,
                source_messages=(),
                projected_messages=(),
                budget=None,
            )
            expected_parent = root / "data" / "contexts" / "journal"
            self.assertEqual(record.path.parent, expected_parent)
            self.assertTrue(record.path.exists())

    def test_health_check_status_defaults_to_not_run(self) -> None:
        """Context Health Check 未实现前，health_check_status 应为 not_run。"""
        state = _make_state()
        with tempfile.TemporaryDirectory() as temp_dir:
            store = CompressionJournalStore(Path(temp_dir))
            record = store.write_projection_change(
                state=state,
                source_messages=(),
                projected_messages=(),
                budget=None,
            )
        self.assertEqual(record.health_check_status, "not_run")

    def test_notes_are_truncated_when_too_long(self) -> None:
        """超长 notes 应被截断，不污染 journal 文件。"""
        long_notes = "x" * 2000
        state = _make_state()
        with tempfile.TemporaryDirectory() as temp_dir:
            store = CompressionJournalStore(Path(temp_dir))
            record = store.write_projection_change(
                state=state,
                source_messages=(),
                projected_messages=(),
                budget=None,
                notes=long_notes,
            )
        self.assertLessEqual(len(record.notes), 1000)
        self.assertTrue(record.notes.endswith("[truncated]"))

    def test_invalid_action_type_raises_value_error(self) -> None:
        """不在允许集合内的 action_type 应抛出 ValueError。"""
        from dutyflow.context.compression_journal import _build_record, _generate_journal_id
        import tempfile
        state = _make_state()
        with tempfile.TemporaryDirectory() as temp_dir:
            journal_dir = Path(temp_dir) / "journal"
            journal_dir.mkdir(parents=True)
            with self.assertRaises(ValueError):
                _build_record(
                    Path(temp_dir),
                    journal_dir,
                    state=state,
                    action_type="invalid_type",
                    trigger_reason="test",
                    source_messages=(),
                    projected_messages=(),
                    budget=None,
                )


def _self_test() -> None:
    """运行本文件单元测试。"""
    suite = unittest.defaultTestLoader.loadTestsFromName(__name__)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    _self_test()
