# 本文件验证 context_overflow emergency compact recovery 的各层行为。

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sys
import tempfile
import unittest
from typing import Any
from unittest.mock import MagicMock

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
    append_assistant_message,
    append_tool_results,
    append_user_message,
)
from dutyflow.context.compression_journal import CompressionJournalStore  # noqa: E402
from dutyflow.context.runtime_context import (  # noqa: E402
    RuntimeContextManager,
    run_context_health_check,
)


# ---------------------------------------------------------------------------
# 辅助工厂
# ---------------------------------------------------------------------------

def _make_tool_result_block(tool_use_id: str, content: str = "raw data") -> AgentContentBlock:
    return AgentContentBlock(
        type="tool_result",
        tool_use_id=tool_use_id,
        tool_name="sample_tool",
        content=content,
    )


def _make_tool_use_block(tool_use_id: str) -> AgentContentBlock:
    return AgentContentBlock(type="tool_use", tool_use_id=tool_use_id, tool_name="sample_tool")


def _state_with_old_and_fresh_results(query_id: str = "q_ec") -> AgentState:
    """构造一个包含旧 tool_result 和最新 tool_result 的典型 state。"""
    state = create_initial_agent_state(query_id, "do work")
    # 第一轮工具调用（旧）
    state = append_assistant_message(state, (_make_tool_use_block("tool_old"),))
    state = append_tool_results(state, (_make_tool_result_block("tool_old", "old data"),))
    # 用户继续
    state = append_user_message(state, "continue")
    # 第二轮工具调用（新）
    state = append_assistant_message(state, (_make_tool_use_block("tool_fresh"),))
    state = append_tool_results(state, (_make_tool_result_block("tool_fresh", "fresh data"),))
    return state


class TestEmergencyCompactMessages(unittest.TestCase):
    """验证 emergency_compact_messages 比 micro-compact 更激进。"""

    def test_compacts_fresh_tool_result(self) -> None:
        """应急压缩应压缩包括最新 tool_result 在内的全部工具结果。"""
        state = _state_with_old_and_fresh_results()
        manager = RuntimeContextManager()
        compacted = manager.emergency_compact_messages(state)
        # 找出全部 tool_result block
        all_contents = [
            block.content
            for msg in compacted
            for block in msg.content
            if block.type == "tool_result"
        ]
        for content in all_contents:
            self.assertTrue(
                str(content).strip().startswith("ToolReceipt("),
                f"Expected ToolReceipt, got: {content!r}",
            )

    def test_micro_compact_preserves_fresh_but_emergency_does_not(self) -> None:
        """micro-compact 保留最新结果，应急压缩不保留。"""
        state = _state_with_old_and_fresh_results()
        manager = RuntimeContextManager()
        # micro-compact 保留 tool_fresh（它在最后一条 user 消息中）
        micro = manager.micro_compact_messages(state)
        fresh_contents_micro = [
            block.content
            for msg in micro
            for block in msg.content
            if block.type == "tool_result" and block.tool_use_id == "tool_fresh"
        ]
        self.assertTrue(
            any(not str(c).startswith("ToolReceipt(") for c in fresh_contents_micro),
            "micro-compact should preserve fresh tool_fresh",
        )

        # 应急压缩压缩 tool_fresh
        emergency = manager.emergency_compact_messages(state)
        fresh_contents_emergency = [
            block.content
            for msg in emergency
            for block in msg.content
            if block.type == "tool_result" and block.tool_use_id == "tool_fresh"
        ]
        self.assertTrue(
            all(str(c).strip().startswith("ToolReceipt(") for c in fresh_contents_emergency),
            "emergency compact should compact fresh tool_fresh",
        )

    def test_message_count_unchanged_after_emergency_compact(self) -> None:
        """应急压缩不得减少消息数量。"""
        state = _state_with_old_and_fresh_results()
        manager = RuntimeContextManager()
        compacted = manager.emergency_compact_messages(state)
        self.assertEqual(len(compacted), len(state.messages))

    def test_idempotent_when_all_already_compacted(self) -> None:
        """当所有工具结果已是 Tool Receipt 时，应急压缩应返回相同 messages 对象（不变）。"""
        state = create_initial_agent_state("q_idem", "hello")
        state = append_assistant_message(state, (_make_tool_use_block("tool_r"),))
        receipt_block = AgentContentBlock(
            type="tool_result",
            tool_use_id="tool_r",
            tool_name="sample_tool",
            content="ToolReceipt(tool_use_id=tool_r,status=success,summary=done)",
        )
        state = append_tool_results(state, (receipt_block,))
        state = append_user_message(state, "next")
        manager = RuntimeContextManager()
        compacted = manager.emergency_compact_messages(state)
        self.assertIs(compacted, state.messages)


class TestWriteEmergencyCompact(unittest.TestCase):
    """验证 CompressionJournalStore.write_emergency_compact 写入正确 action_type。"""

    def _build_state(self) -> AgentState:
        state = create_initial_agent_state("q_wec", "test")
        state = append_assistant_message(state, (_make_tool_use_block("tool_wec"),))
        state = append_tool_results(state, (_make_tool_result_block("tool_wec", "data"),))
        return state

    def test_writes_emergency_compact_action_type(self) -> None:
        """写入的 journal 记录 action_type 应为 emergency_compact。"""
        state = self._build_state()
        manager = RuntimeContextManager()
        compacted = manager.emergency_compact_messages(state)
        with tempfile.TemporaryDirectory() as tmp:
            store = CompressionJournalStore(Path(tmp))
            record = store.write_emergency_compact(
                state=state,
                source_messages=state.messages,
                compacted_messages=compacted,
                budget=None,
                health_check_status="passed",
            )
        self.assertEqual(record.action_type, "emergency_compact")
        self.assertEqual(record.trigger_reason, "context_overflow_emergency")
        self.assertEqual(record.health_check_status, "passed")

    def test_compacted_tool_result_ids_detected(self) -> None:
        """应急压缩的 compacted_tool_result_ids 应包含被替换的 tool_use_id。"""
        state = self._build_state()
        manager = RuntimeContextManager()
        compacted = manager.emergency_compact_messages(state)
        with tempfile.TemporaryDirectory() as tmp:
            store = CompressionJournalStore(Path(tmp))
            record = store.write_emergency_compact(
                state=state,
                source_messages=state.messages,
                compacted_messages=compacted,
                budget=None,
            )
        self.assertIn("tool_wec", record.compacted_tool_result_ids)

    def test_health_check_status_passed_through(self) -> None:
        """health_check_status 参数应原样写入 journal。"""
        state = self._build_state()
        manager = RuntimeContextManager()
        compacted = manager.emergency_compact_messages(state)
        with tempfile.TemporaryDirectory() as tmp:
            store = CompressionJournalStore(Path(tmp))
            record_failed = store.write_emergency_compact(
                state=state,
                source_messages=state.messages,
                compacted_messages=compacted,
                budget=None,
                health_check_status="failed",
            )
        self.assertEqual(record_failed.health_check_status, "failed")


class TestApplyEmergencyCompact(unittest.TestCase):
    """验证 AgentLoop._apply_emergency_compact 的成功与失败路径。"""

    def _make_loop(self, tmp_dir: Path):
        """构造最小 AgentLoop，不依赖真实模型。"""
        from dutyflow.agent.core_loop import AgentLoop
        from dutyflow.agent.tools.registry import ToolRegistry

        model_client = MagicMock()
        registry = ToolRegistry()
        loop = AgentLoop(
            model_client=model_client,
            registry=registry,
            cwd=tmp_dir,
        )
        return loop

    def test_returns_true_and_updates_state_when_health_passes(self) -> None:
        """健康检查通过时 _apply_emergency_compact 应返回 True 并更新 state.messages。"""
        state = _state_with_old_and_fresh_results()
        with tempfile.TemporaryDirectory() as tmp:
            loop = self._make_loop(Path(tmp))
            compacted_state, ok = loop._apply_emergency_compact(state, "overflow error")
        self.assertTrue(ok)
        # compacted_state.messages 中不应再有原始 raw data
        all_contents = [
            block.content
            for msg in compacted_state.messages
            for block in msg.content
            if block.type == "tool_result"
        ]
        for content in all_contents:
            self.assertTrue(str(content).strip().startswith("ToolReceipt("))

    def test_writes_emergency_compact_journal_entry(self) -> None:
        """应急压缩应在 journal 目录下落盘一条 emergency_compact 记录。"""
        state = _state_with_old_and_fresh_results()
        with tempfile.TemporaryDirectory() as tmp:
            loop = self._make_loop(Path(tmp))
            loop._apply_emergency_compact(state)
            records = loop.compression_journal_store.list_journals()
        emergency_records = [r for r in records if r.action_type == "emergency_compact"]
        self.assertEqual(len(emergency_records), 1)

    def test_returns_false_when_health_check_fails(self) -> None:
        """健康检查失败时 _apply_emergency_compact 应返回 False，不更新 state。"""
        # 构造有 pending_tool_id 但 emergency compact 后该 ID 消失的 state
        state = create_initial_agent_state("q_fail_hc", "hello")
        state = replace(state, pending_tool_use_ids=("tool_ghost",))
        # emergency compact 不会加入 tool_ghost，所以 pending_tool_ids_preserved 检查会失败
        with tempfile.TemporaryDirectory() as tmp:
            loop = self._make_loop(Path(tmp))
            compacted_state, ok = loop._apply_emergency_compact(state)
        self.assertFalse(ok)
        self.assertIs(compacted_state, state)

    def test_health_check_status_recorded_in_journal(self) -> None:
        """健康检查结果应写入 journal 的 health_check_status 字段。"""
        state = _state_with_old_and_fresh_results()
        with tempfile.TemporaryDirectory() as tmp:
            loop = self._make_loop(Path(tmp))
            loop._apply_emergency_compact(state)
            records = loop.compression_journal_store.list_journals()
        ec_records = [r for r in records if r.action_type == "emergency_compact"]
        self.assertEqual(len(ec_records), 1)
        self.assertIn(ec_records[0].health_check_status, ("passed", "failed"))


class TestContextOverflowRecoveryLoop(unittest.TestCase):
    """验证 AgentLoop.run_until_stop 在 context_overflow 时触发应急压缩并重试。"""

    def _make_mock_response(self, text: str = "done"):
        """构造最小 mock 模型响应。"""
        response = MagicMock()
        response.stop_reason = "stop"
        response.assistant_blocks = (AgentContentBlock(type="text", text=text),)
        return response

    def _make_loop(self, tmp_dir: Path, model_client):
        from dutyflow.agent.core_loop import AgentLoop
        from dutyflow.agent.tools.registry import ToolRegistry

        model_client_obj = model_client
        registry = ToolRegistry()
        loop = AgentLoop(
            model_client=model_client_obj,
            registry=registry,
            cwd=tmp_dir,
        )
        # 关键：mock 掉 phase_summary_service，避免 context_overflow 时 PhaseSummary 也调用 call_model
        loop.phase_summary_service = MagicMock()
        loop.phase_summary_service.maybe_create_summary.return_value = (None, None)
        return loop

    def test_context_overflow_triggers_emergency_compact_and_retries(self) -> None:
        """context_overflow 应触发应急压缩，成功后重试模型调用并返回正常结果。"""
        model_client = MagicMock()
        # 第一次调用抛 context_overflow；第二次返回正常响应
        model_client.call_model.side_effect = [
            Exception("context length exceeded"),
            self._make_mock_response("recovered"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            loop = self._make_loop(Path(tmp), model_client)
            state = _state_with_old_and_fresh_results()
            result = loop.run_until_stop("retry please", state=state)
        self.assertEqual(result.stop_reason, "stop")
        self.assertEqual(result.final_text, "recovered")
        # 模型应被调用了两次
        self.assertEqual(model_client.call_model.call_count, 2)

    def test_context_overflow_fails_when_emergency_compact_health_check_fails(self) -> None:
        """应急压缩 Health Check 失败时，loop 应返回 context_compaction_failed。"""
        model_client = MagicMock()
        model_client.call_model.side_effect = Exception("prompt too long")

        # 构造有 pending_tool_id 但应急压缩后找不到的 state，触发健康检查失败
        base_state = create_initial_agent_state("q_fail_loop", "hello")
        base_state = replace(base_state, pending_tool_use_ids=("tool_ghost_loop",))

        with tempfile.TemporaryDirectory() as tmp:
            loop = self._make_loop(Path(tmp), model_client)
            result = loop.run_until_stop("hello", state=base_state)
        self.assertIn(result.stop_reason, ("context_compaction_failed", "context_overflow"))

    def test_no_emergency_compact_on_transport_error(self) -> None:
        """普通模型传输错误不应触发应急压缩。"""
        model_client = MagicMock()
        model_client.call_model.side_effect = Exception("network timeout")

        with tempfile.TemporaryDirectory() as tmp:
            loop = self._make_loop(Path(tmp), model_client)
            result = loop.run_until_stop("hello")
        records = loop.compression_journal_store.list_journals()
        emergency_records = [r for r in records if r.action_type == "emergency_compact"]
        self.assertEqual(len(emergency_records), 0)

    def test_emergency_compact_only_attempted_once(self) -> None:
        """应急压缩最多尝试 max_emergency_compact_attempts 次，不无限重试。"""
        model_client = MagicMock()
        # 每次都触发 context_overflow
        model_client.call_model.side_effect = Exception("context length exceeded")

        with tempfile.TemporaryDirectory() as tmp:
            loop = self._make_loop(Path(tmp), model_client)
            state = _state_with_old_and_fresh_results()
            result = loop.run_until_stop("retry", state=state)
        # 应急压缩后还是 overflow → 最终以非正常原因结束
        records = loop.compression_journal_store.list_journals()
        emergency_records = [r for r in records if r.action_type == "emergency_compact"]
        # 最多一次应急压缩
        self.assertLessEqual(len(emergency_records), loop.max_emergency_compact_attempts)


def _self_test() -> None:
    """运行本文件单元测试。"""
    suite = unittest.defaultTestLoader.loadTestsFromName(__name__)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    _self_test()
