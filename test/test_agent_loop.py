# 本文件验证 Step 2.4 多轮调试 AgentLoop 的状态回写。

from pathlib import Path
import sys
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dutyflow.agent.debug_tools import create_debug_tool_registry  # noqa: E402
from dutyflow.agent.loop import AgentLoop, ChatDebugSession  # noqa: E402
from dutyflow.agent.model_client import ModelResponse  # noqa: E402
from dutyflow.agent.state import AgentContentBlock  # noqa: E402


class TestAgentLoop(unittest.TestCase):
    """验证 /chat 调试 loop 的最小闭环。"""

    def test_tool_call_continues_to_second_model_turn(self) -> None:
        """第一轮 tool_use、第二轮 text 时应完整回写工具结果。"""
        client = _FakeModelClient((_tool_response(), _text_response("done")))
        result = _loop(client).run_until_stop("run", query_id="query_001")
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

    def test_max_turns_stops_continuous_tool_calls(self) -> None:
        """连续 tool_use 超出 max_turns 时应返回失败结果。"""
        client = _FakeModelClient((_tool_response(), _tool_response()))
        result = _loop(client, max_turns=2).run_until_stop("run")
        self.assertEqual(result.stop_reason, "max_turns_reached")
        self.assertEqual(result.state.transition_reason, "failed")

    def test_debug_text_contains_state_and_tool_results(self) -> None:
        """调试输出必须包含完整 state 和 tool result。"""
        client = _FakeModelClient((_tool_response(), _text_response("done")))
        text = _loop(client).run_until_stop("run").to_debug_text()
        self.assertIn('"agent_state"', text)
        self.assertIn('"tool_results"', text)
        self.assertIn('"final_text": "done"', text)

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


class _FakeModelClient:
    """按顺序返回预设响应的测试模型。"""

    def __init__(self, responses: tuple[ModelResponse, ...]) -> None:
        """保存预设响应。"""
        self.responses = list(responses)

    def call_model(self, state, tools) -> ModelResponse:
        """返回下一条预设模型响应。"""
        if not self.responses:
            raise RuntimeError("fake responses exhausted")
        return self.responses.pop(0)


def _loop(client: _FakeModelClient, max_turns: int = 6) -> AgentLoop:
    """构造测试用 AgentLoop。"""
    return AgentLoop(client, create_debug_tool_registry(), PROJECT_ROOT, max_turns=max_turns)


def _tool_response() -> ModelResponse:
    """构造包含 echo_text 工具调用的模型响应。"""
    block = AgentContentBlock(
        type="tool_use",
        tool_use_id="tool_1",
        tool_name="echo_text",
        tool_input={"text": "hello"},
    )
    return ModelResponse((block,), "tool_use")


def _text_response(text: str) -> ModelResponse:
    """构造文本模型响应。"""
    return ModelResponse((AgentContentBlock(type="text", text=text),), "stop")


def _user_texts(state) -> tuple[str, ...]:
    """提取 Agent State 中的用户文本。"""
    texts: list[str] = []
    for message in state.messages:
        if message.role == "user":
            texts.extend(block.text for block in message.content if block.type == "text")
    return tuple(texts)


def _self_test() -> None:
    """运行本文件单元测试。"""
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestAgentLoop)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    _self_test()
