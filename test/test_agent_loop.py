# 本文件验证 Step 2.4 多轮调试 AgentLoop 的状态回写。

from pathlib import Path
import sys
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dutyflow.agent.loop import AgentLoop, ChatDebugSession  # noqa: E402
from dutyflow.agent.model_client import ModelResponse  # noqa: E402
from dutyflow.agent.state import AgentContentBlock  # noqa: E402
from dutyflow.agent.tools import ToolResultEnvelope, ToolSpec  # noqa: E402
from dutyflow.agent.tools.registry import ToolRegistry, create_runtime_tool_registry  # noqa: E402


class TestAgentLoop(unittest.TestCase):
    """验证 /chat 调试 loop 的最小闭环。"""

    def test_tool_call_continues_to_second_model_turn(self) -> None:
        """第一轮 tool_use、第二轮 text 时应完整回写工具结果。"""
        client = _FakeModelClient((_tool_response(), _text_response("done")))
        result = _loop(client, registry=_tool_test_registry()).run_until_stop("run", query_id="query_001")
        self.assertEqual(result.final_text, "done")
        self.assertEqual(result.tool_result_count, 1)
        self.assertEqual(result.tool_results[0].content, "hello")
        self.assertEqual(result.state.transition_reason, "finished")

    def test_plain_text_finishes_in_one_turn(self) -> None:
        """模型直接返回文本时 loop 应一轮结束。"""
        result = _loop(_FakeModelClient((_text_response("pong"),))).run_until_stop("ping")
        self.assertEqual(result.final_text, "pong")
        self.assertEqual(result.tool_result_count, 0)
        self.assertEqual(result.turn_count, 1)

    def test_max_tokens_response_continues_with_recovery_state(self) -> None:
        """max_tokens 截断后应继续下一轮，并写入 recovery 聚合信息。"""
        client = _FakeModelClient((_text_response("part", "max_tokens"), _text_response("done")))
        result = _loop(client).run_until_stop("run")
        self.assertEqual(result.final_text, "done")
        self.assertEqual(result.state.recovery.continuation_attempts, 1)
        self.assertEqual(result.state.recovery.latest_resume_point, "before_model_call")
        self.assertEqual(result.state.task_control.attempt_count, 1)
        self.assertEqual(result.state.task_control.retry_status, "none")

    def test_model_transport_error_retries_and_records_recovery(self) -> None:
        """模型传输异常恢复后应记录 transport recovery。"""
        client = _FakeModelClient((RuntimeError("model request failed: timeout"), _text_response("done")))
        result = _loop(client).run_until_stop("run")
        self.assertEqual(result.final_text, "done")
        self.assertEqual(result.state.recovery.transport_attempts, 1)
        self.assertEqual(result.state.transition_reason, "finished")
        self.assertEqual(result.state.task_control.attempt_count, 1)
        self.assertEqual(result.state.task_control.retry_status, "none")

    def test_context_overflow_fails_with_recovery_scope(self) -> None:
        """上下文溢出时应返回失败，并留下恢复 scope。"""
        client = _FakeModelClient((ValueError("prompt too long"),))
        result = _loop(client).run_until_stop("run")
        self.assertEqual(result.stop_reason, "context_overflow")
        self.assertEqual(result.state.recovery.compact_attempts, 1)
        self.assertEqual(result.state.recovery.recovery_scopes[0].failure_kind, "context_overflow")
        self.assertEqual(result.state.recovery.recovery_scopes[0].status, "waiting")
        self.assertEqual(result.state.task_control.retry_status, "retrying")
        self.assertEqual(result.state.task_control.next_action, "compact_context_then_retry")
        self.assertEqual(len(result.pending_restarts), 1)
        self.assertEqual(result.pending_restarts[0].restart_action, "compact_then_retry")
        self.assertFalse(result.pending_restarts[0].can_restart_now)

    def test_max_turns_stops_continuous_tool_calls(self) -> None:
        """连续 tool_use 超出 max_turns 时应返回失败结果。"""
        client = _FakeModelClient((_tool_response(), _tool_response()))
        result = _loop(client, registry=_tool_test_registry(), max_turns=2).run_until_stop("run")
        self.assertEqual(result.stop_reason, "max_turns_reached")
        self.assertEqual(result.state.transition_reason, "failed")

    def test_debug_text_contains_state_and_tool_results(self) -> None:
        """调试输出必须包含完整 state 和 tool result。"""
        client = _FakeModelClient((_tool_response(), _text_response("done")))
        text = _loop(client, registry=_tool_test_registry()).run_until_stop("run").to_debug_text()
        self.assertIn('"agent_state"', text)
        self.assertIn('"tool_results"', text)
        self.assertIn('"pending_restarts"', text)
        self.assertIn('"final_text": "done"', text)
        self.assertIn('"attempt_count": 1', text)
        self.assertIn('"context_modifiers"', text)
        self.assertIn('"retry_exhausted": false', text)

    def test_chat_session_reuses_agent_state(self) -> None:
        """持续 chat 会话应复用同一个 Agent State。"""
        client = _FakeModelClient((_text_response("one"), _text_response("two")))
        session = ChatDebugSession(_loop(client))
        first = session.run_turn("first")
        second = session.run_turn("second")
        user_texts = _user_texts(second.state)
        self.assertEqual(first.state.query_id, second.state.query_id)
        self.assertEqual(second.turn_count, 2)
        self.assertIn("first", user_texts)
        self.assertIn("second", user_texts)

    def test_loop_writes_structured_audit_events(self) -> None:
        """loop 应记录 started、recovery 和 finished 审计事件。"""
        logger = _FakeAuditLogger()
        client = _FakeModelClient((ValueError("prompt too long"),))
        _loop(client, audit_logger=logger).run_until_stop("run")
        event_types = {item["event_type"] for item in logger.records}
        self.assertIn("loop_started", event_types)
        self.assertIn("model_recovery_registered", event_types)
        self.assertIn("pending_restart_described", event_types)
        self.assertIn("loop_finished", event_types)


class _FakeModelClient:
    """按顺序返回预设响应的测试模型。"""

    def __init__(self, responses: tuple[object, ...]) -> None:
        """保存预设响应。"""
        self.responses = list(responses)

    def call_model(self, state, tools) -> ModelResponse:
        """返回下一条预设模型响应。"""
        if not self.responses:
            raise RuntimeError("fake responses exhausted")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _loop(client: _FakeModelClient, registry=None, max_turns: int = 6, audit_logger=None) -> AgentLoop:
    """构造测试用 AgentLoop。"""
    return AgentLoop(
        client,
        registry or create_runtime_tool_registry(),
        PROJECT_ROOT,
        max_turns=max_turns,
        audit_logger=audit_logger,
    )


def _tool_response() -> ModelResponse:
    """构造包含 sample_tool 工具调用的模型响应。"""
    block = AgentContentBlock(
        type="tool_use",
        tool_use_id="tool_1",
        tool_name="sample_tool",
        tool_input={"text": "hello"},
    )
    return ModelResponse((block,), "tool_use")


def _tool_test_registry() -> ToolRegistry:
    """构造仅供 loop 测试使用的最小工具注册表。"""
    registry = ToolRegistry()
    registry.register(
        ToolSpec("sample_tool", "Return text.", {"required": ["text"]}, is_concurrency_safe=True),
        _sample_handler,
    )
    return registry


def _sample_handler(tool_call, tool_use_context) -> ToolResultEnvelope:
    """返回测试工具的输入文本。"""
    return ToolResultEnvelope(tool_call.tool_use_id, tool_call.tool_name, True, str(tool_call.tool_input["text"]))


def _text_response(text: str, stop_reason: str = "stop") -> ModelResponse:
    """构造文本模型响应。"""
    return ModelResponse((AgentContentBlock(type="text", text=text),), stop_reason)


def _user_texts(state) -> tuple[str, ...]:
    """提取 Agent State 中的用户文本。"""
    texts: list[str] = []
    for message in state.messages:
        if message.role == "user":
            texts.extend(block.text for block in message.content if block.type == "text")
    return tuple(texts)


class _FakeAuditLogger:
    """为 loop 审计测试提供最小结构化日志对象。"""

    def __init__(self) -> None:
        """保存审计记录。"""
        self.records: list[dict[str, object]] = []

    def preview(self, value) -> str:
        """返回测试预览。"""
        return str(value)

    def record_event(self, **payload) -> dict[str, object]:
        """记录一条结构化审计事件。"""
        self.records.append(dict(payload))
        return dict(payload)


def _self_test() -> None:
    """运行本文件单元测试。"""
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestAgentLoop)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    _self_test()
