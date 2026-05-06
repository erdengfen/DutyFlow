# 本文件验证 RuntimeAgentLoop 对 ambient_context_batch trigger_kind 的处理行为。

from __future__ import annotations

import sys
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dutyflow.agent.runtime_service import RuntimeLoopInput, RuntimeWorkItem  # noqa: E402


def _ambient_work_item(
    *,
    source_type: str = "direct_message",
    record_count: int = 2,
    chat_id: str = "oc_owner",
    perception_id: str = "amb_batch_testpkt001",
) -> RuntimeWorkItem:
    """构造一个 ambient_context_batch 类型的测试 work item。"""
    packet = {
        "packet_id": "ambpkt_test001",
        "source_type": source_type,
        "record_ids": [f"dm_rec{i}" for i in range(record_count)],
        "record_count": record_count,
        "time_window": {"start": "2026-05-07T10:00:00+00:00", "end": "2026-05-07T11:00:00+00:00"},
        "records": [],
    }
    loop_input = RuntimeLoopInput(
        perception_id=perception_id,
        perception_file="",
        trigger_kind="ambient_context_batch",
        payload={
            "perception_id": perception_id,
            "trigger_kind": "ambient_context_batch",
            "source_type": source_type,
            "chat_id": chat_id,
            "packet": packet,
        },
    )
    return RuntimeWorkItem(
        work_id="run_test_ambient_001",
        perception_id=perception_id,
        enqueued_at="2026-05-07T10:00:00+00:00",
        loop_input=loop_input,
    )


@dataclass
class _FakeAgentLoopResult:
    """测试替身：模拟 AgentLoopResult。"""

    final_text: str = ""
    stop_reason: str = "end_turn"
    turn_count: int = 1
    tool_result_count: int = 0
    pending_restarts: tuple = ()
    state: Any = None


@dataclass
class _FakeAgentLoop:
    """测试替身：记录 AgentLoop.run_until_stop 调用参数。"""

    calls: list[dict[str, Any]] = field(default_factory=list)

    def run_until_stop(
        self,
        user_text: str,
        *,
        query_id: str = "",
        tool_content: Mapping[str, Any] | None = None,
        state: Any = None,
    ) -> _FakeAgentLoopResult:
        self.calls.append({
            "user_text": user_text,
            "query_id": query_id,
            "tool_content": dict(tool_content or {}),
        })
        return _FakeAgentLoopResult()

    @property
    def runtime_context_manager(self):
        return None


class _FakeFeedbackGateway:
    """测试替身：记录 feedback 调用。"""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def send_text(self, chat_id: str, text: str) -> Any:
        self.calls.append(chat_id)
        return _FakeFeedbackResult()

    def send_status_update(self, chat_id: str, *args: Any) -> Any:
        self.calls.append(chat_id)
        return _FakeFeedbackResult()


@dataclass
class _FakeFeedbackResult:
    ok: bool = True
    status: str = "ok"


class TestRuntimeLoopAmbientBatch(unittest.TestCase):
    """验证 RuntimeAgentLoop 处理 ambient_context_batch 的行为。"""

    def _make_loop(self):
        """构造注入了 fake 依赖的 RuntimeAgentLoop 实例。"""
        from dutyflow.agent.runtime_loop import RuntimeAgentLoop

        loop = object.__new__(RuntimeAgentLoop)
        loop.project_root = Path(".")
        loop.config = None
        loop.agent_loop = _FakeAgentLoop()
        loop.feedback_gateway = _FakeFeedbackGateway()
        loop.perception_service = None
        loop.latest_result = None
        loop.latest_agent_loop_result = None
        loop._chat_sessions = {}
        return loop

    def test_ambient_batch_skips_perception_store(self) -> None:
        """ambient_context_batch 不应调用 perception_service。"""
        loop = self._make_loop()
        work_item = _ambient_work_item()

        loop.handle_work_item(work_item)

        self.assertIsNotNone(loop.latest_result)
        self.assertEqual(loop.latest_result.perception_id, "amb_batch_testpkt001")

    def test_ambient_batch_user_text_mentions_batch(self) -> None:
        """ambient batch 提示文本应包含批次分析相关关键词。"""
        loop = self._make_loop()
        work_item = _ambient_work_item(record_count=3)

        loop.handle_work_item(work_item)

        user_text = loop.agent_loop.calls[0]["user_text"]
        self.assertIn("批次", user_text)
        self.assertIn("3", user_text)

    def test_ambient_batch_tool_content_has_packet(self) -> None:
        """ambient batch 的 tool_content 应包含 ambient_context_batch 字段。"""
        loop = self._make_loop()
        work_item = _ambient_work_item(source_type="group_message")

        loop.handle_work_item(work_item)

        tool_content = loop.agent_loop.calls[0]["tool_content"]
        self.assertIn("ambient_context_batch", tool_content)
        self.assertEqual(
            tool_content["ambient_context_batch"]["source_type"], "group_message"
        )

    def test_ambient_batch_tool_content_has_runtime_fields(self) -> None:
        """ambient batch 的 tool_content 应包含 runtime 元信息。"""
        loop = self._make_loop()
        work_item = _ambient_work_item()

        loop.handle_work_item(work_item)

        tool_content = loop.agent_loop.calls[0]["tool_content"]
        runtime_info = tool_content["runtime"]
        self.assertEqual(runtime_info["trigger_kind"], "ambient_context_batch")
        self.assertEqual(runtime_info["work_id"], "run_test_ambient_001")

    def test_normal_trigger_kind_uses_perception_store(self) -> None:
        """非 ambient_context_batch 的 trigger_kind 应尝试读取 perception store。"""
        loop = self._make_loop()
        loop_input_payload = {
            "perception_id": "per_test_001",
            "trigger_kind": "p2p_text",
            "chat_id": "oc_owner",
        }
        normal_input = RuntimeLoopInput(
            perception_id="per_test_001",
            perception_file="",
            trigger_kind="p2p_text",
            payload=loop_input_payload,
        )
        work_item = RuntimeWorkItem(
            work_id="run_normal_001",
            perception_id="per_test_001",
            enqueued_at="2026-05-07T10:00:00+00:00",
            loop_input=normal_input,
        )

        class _FakePerceptionService:
            def build_loop_input(self, *, record_id: str = "", message_id: str = "") -> dict | None:
                return {
                    "perception_id": record_id,
                    "trigger_kind": "p2p_text",
                    "chat_id": "oc_owner",
                    "raw_text": "hello",
                }

        loop.perception_service = _FakePerceptionService()

        loop.handle_work_item(work_item)

        user_text = loop.agent_loop.calls[0]["user_text"]
        self.assertIn("hello", user_text)

    def test_ambient_batch_result_stored(self) -> None:
        """处理完成后 latest_result 应记录正确的 work_id 和 perception_id。"""
        loop = self._make_loop()
        work_item = _ambient_work_item()

        loop.handle_work_item(work_item)

        result = loop.latest_result
        self.assertEqual(result.work_id, "run_test_ambient_001")
        self.assertEqual(result.stop_reason, "end_turn")


if __name__ == "__main__":
    unittest.main()
