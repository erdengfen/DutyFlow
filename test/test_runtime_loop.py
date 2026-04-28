# 本文件验证正式 runtime loop 已接到现有 Agent 基架，并可通过统一反馈接口回消息。

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dutyflow.agent.model_client import ModelResponse  # noqa: E402
from dutyflow.agent.runtime_loop import RuntimeAgentLoop  # noqa: E402
from dutyflow.agent.runtime_service import RuntimeLoopInput, RuntimeWorkItem  # noqa: E402
from dutyflow.agent.state import AgentContentBlock  # noqa: E402
from dutyflow.agent.tools import ToolResultEnvelope, ToolSpec  # noqa: E402
from dutyflow.agent.tools.registry import ToolRegistry  # noqa: E402
from dutyflow.config.env import load_env_config  # noqa: E402
from dutyflow.feishu.events import FeishuEventAdapter  # noqa: E402
from dutyflow.perception.store import PerceptionRecordService  # noqa: E402


class TestRuntimeLoop(unittest.TestCase):
    """验证正式 runtime loop 与现有 AgentLoop 的最小整合闭环。"""

    def test_runtime_loop_sends_plain_text_reply(self) -> None:
        """纯文本响应应通过统一反馈接口直接发回当前会话。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = _write_env(root)
            perception = PerceptionRecordService(root)
            record = _create_perception_record(perception, "hello", message_id="msg_plain")
            feedback = _FakeFeedbackGateway()
            loop = RuntimeAgentLoop(
                root,
                config,
                model_client=_FakeModelClient((_text_response("pong"),)),
                registry=ToolRegistry(),
                feedback_gateway=feedback,
                perception_service=perception,
            )
            loop.handle_work_item(_work_item(record))
            self.assertIsNotNone(loop.latest_result)
            self.assertEqual(loop.latest_result.final_text, "pong")
            self.assertEqual(loop.latest_result.tool_result_count, 0)
            self.assertEqual(feedback.sent_texts, [("oc_fixture_chat", "pong")])

    def test_runtime_loop_supports_multi_turn_tool_calls(self) -> None:
        """正式 runtime loop 应能复用现有 AgentLoop 的多轮工具调用能力。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = _write_env(root)
            perception = PerceptionRecordService(root)
            record = _create_perception_record(perception, "run", message_id="msg_tool")
            feedback = _FakeFeedbackGateway()
            loop = RuntimeAgentLoop(
                root,
                config,
                model_client=_FakeModelClient((_tool_response(), _text_response("done"))),
                registry=_tool_test_registry(),
                feedback_gateway=feedback,
                perception_service=perception,
            )
            loop.handle_work_item(_work_item(record))
            self.assertIsNotNone(loop.latest_result)
            self.assertEqual(loop.latest_result.final_text, "done")
            self.assertEqual(loop.latest_result.tool_result_count, 1)
            self.assertEqual(feedback.sent_texts, [("oc_fixture_chat", "done")])


class _FakeModelClient:
    """按顺序返回预设响应的测试模型。"""

    def __init__(self, responses: tuple[object, ...]) -> None:
        """保存预设模型响应。"""
        self.responses = list(responses)

    def call_model(self, state, tools) -> ModelResponse:
        """返回下一条模型响应，验证正式 runtime 复用了 AgentLoop。"""
        del state, tools
        if not self.responses:
            raise RuntimeError("fake responses exhausted")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class _FakeFeedbackGateway:
    """模拟统一反馈接口，记录正式 runtime 的发送结果。"""

    def __init__(self) -> None:
        """保存已经发送的文本和状态消息。"""
        self.sent_texts: list[tuple[str, str]] = []
        self.sent_status_updates: list[tuple[str, str, str]] = []

    def send_text(self, chat_id: str, text: str):
        """记录文本发送请求。"""
        from dutyflow.feedback.gateway import FeedbackResult  # noqa: WPS433

        self.sent_texts.append((chat_id, text))
        return FeedbackResult(ok=True, status="sent", detail="fake", payload={"chat_id": chat_id})

    def send_status_update(self, chat_id: str, title: str, summary: str):
        """记录状态更新发送请求。"""
        from dutyflow.feedback.gateway import FeedbackResult  # noqa: WPS433

        self.sent_status_updates.append((chat_id, title, summary))
        return FeedbackResult(ok=True, status="sent", detail="fake", payload={"chat_id": chat_id})


def _write_env(root: Path) -> object:
    """写入正式 runtime loop 测试所需的最小配置。"""
    content = (
        "DUTYFLOW_MODEL_API_KEY=demo-key\n"
        "DUTYFLOW_MODEL_BASE_URL=https://example.invalid/model\n"
        "DUTYFLOW_MODEL_NAME=demo-model\n"
        "DUTYFLOW_FEISHU_APP_ID=app_demo\n"
        "DUTYFLOW_FEISHU_APP_SECRET=secret_demo\n"
        "DUTYFLOW_FEISHU_EVENT_VERIFY_TOKEN=verify_demo\n"
        "DUTYFLOW_FEISHU_EVENT_ENCRYPT_KEY=encrypt_demo\n"
        "DUTYFLOW_FEISHU_EVENT_MODE=fixture\n"
        "DUTYFLOW_FEISHU_TENANT_KEY=tenant_demo\n"
        "DUTYFLOW_FEISHU_OWNER_OPEN_ID=ou_owner\n"
        "DUTYFLOW_FEISHU_OWNER_REPORT_CHAT_ID=oc_owner\n"
        "DUTYFLOW_DATA_DIR=data\n"
        "DUTYFLOW_LOG_DIR=data/logs\n"
    )
    (root / ".env").write_text(content, encoding="utf-8")
    return load_env_config(root)


def _create_perception_record(
    perception: PerceptionRecordService,
    text: str,
    *,
    message_id: str,
):
    """根据 fixture 飞书消息生成一条可供正式 runtime 消费的感知记录。"""
    adapter = FeishuEventAdapter()
    envelope = adapter.build_event_envelope(
        adapter.create_local_fixture_event(text, message_id=message_id)
    )
    raw_event_path = perception.project_root / "data" / "events" / f"evt_{message_id}.md"
    return perception.create_record(envelope, raw_event_path)


def _work_item(record) -> RuntimeWorkItem:
    """把感知记录包装成 runtime work item。"""
    return RuntimeWorkItem(
        work_id=f"run_{record.record_id}",
        perception_id=record.record_id,
        enqueued_at="2026-04-28T00:00:00+00:00",
        loop_input=RuntimeLoopInput(
            perception_id=record.record_id,
            perception_file=str(record.path),
            trigger_kind=record.trigger_kind,
            payload=record.to_loop_input(),
        ),
    )


def _tool_test_registry() -> ToolRegistry:
    """构造仅供正式 runtime loop 测试使用的最小工具注册表。"""
    registry = ToolRegistry()
    registry.register(
        ToolSpec("sample_tool", "Return text.", {"required": ["text"]}, is_concurrency_safe=True),
        _sample_handler,
    )
    return registry


def _sample_handler(tool_call, tool_use_context) -> ToolResultEnvelope:
    """返回测试工具输入，验证多轮工具调用链可用。"""
    del tool_use_context
    return ToolResultEnvelope(tool_call.tool_use_id, tool_call.tool_name, True, str(tool_call.tool_input["text"]))


def _tool_response() -> ModelResponse:
    """构造包含 sample_tool 的模型工具调用响应。"""
    block = AgentContentBlock(
        type="tool_use",
        tool_use_id="tool_1",
        tool_name="sample_tool",
        tool_input={"text": "hello"},
    )
    return ModelResponse((block,), "tool_use")


def _text_response(text: str, stop_reason: str = "stop") -> ModelResponse:
    """构造简单文本响应。"""
    return ModelResponse((AgentContentBlock(type="text", text=text),), stop_reason)


def _self_test() -> None:
    """运行本文件单元测试。"""
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestRuntimeLoop)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    _self_test()
