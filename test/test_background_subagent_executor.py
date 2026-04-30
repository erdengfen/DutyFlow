# 本文件验证后台 subagent executor 复用 AgentLoop 核心执行单个任务。

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dutyflow.agent.background_subagent_executor import BackgroundSubagentExecutor  # noqa: E402
from dutyflow.agent.model_client import ModelResponse  # noqa: E402
from dutyflow.agent.state import AgentContentBlock  # noqa: E402
from dutyflow.agent.tools import ToolResultEnvelope, ToolSpec  # noqa: E402
from dutyflow.agent.tools.registry import ToolRegistry  # noqa: E402
from dutyflow.tasks.task_state import TaskStore  # noqa: E402


class TestBackgroundSubagentExecutor(unittest.TestCase):
    """验证后台 subagent executor 的任务执行和结果映射。"""

    def test_executor_completes_plain_task(self) -> None:
        """模型直接返回文本时，executor 应把任务结果映射为 completed。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            task = _create_task(root)
            model = _CapturingModelClient((_text_response("任务结果：已整理完成。"),))
            result = BackgroundSubagentExecutor(root, model).execute_task(task)
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.retry_status, "done")
        self.assertEqual(result.user_visible_final_text, "任务结果：已整理完成。")
        self.assertEqual(result.query_id, "bg_task_task_bg_001")
        self.assertIn("resume_payload: goal=整理项目风险", model.last_user_text)
        self.assertIn("decision_trace:", model.last_user_text)

    def test_executor_reuses_agent_loop_tool_continuation(self) -> None:
        """executor 应复用 AgentLoop 的多轮工具调用能力。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            task = _create_task(root)
            model = _CapturingModelClient((_tool_response(), _text_response("工具结果已处理。")))
            result = BackgroundSubagentExecutor(
                root,
                model,
                registry=_sample_tool_registry(),
            ).execute_task(task)
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.tool_result_count, 1)
        self.assertEqual(result.user_visible_final_text, "工具结果已处理。")

    def test_executor_blocks_when_model_returns_no_visible_text(self) -> None:
        """模型没有生成最终文本时，executor 不应伪装任务完成。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            task = _create_task(root)
            result = BackgroundSubagentExecutor(
                root,
                _CapturingModelClient((_text_response(""),)),
            ).execute_task(task)
        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.retry_status, "blocked")
        self.assertIn("未生成可见结果", result.last_result_summary)


class _CapturingModelClient:
    """按顺序返回预设响应，并记录最近用户输入。"""

    def __init__(self, responses: tuple[object, ...]) -> None:
        """保存预设响应列表。"""
        self.responses = list(responses)
        self.last_user_text = ""

    def call_model(self, state, tools) -> ModelResponse:
        """记录用户输入并返回下一条响应。"""
        del tools
        self.last_user_text = _latest_user_text(state)
        if not self.responses:
            raise RuntimeError("fake responses exhausted")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _create_task(root: Path):
    """创建一条带恢复载荷和决策记录的后台任务。"""
    return TaskStore(root).create_task(
        title="整理项目风险",
        task_id="task_bg_001",
        status="running",
        run_mode="async_now",
        execution_profile="background_async_selected",
        requested_capabilities="content_summarization",
        resolved_tools="sample_tool",
        summary="整理本周项目风险并给出简要结论。",
        resume_payload="goal=整理项目风险; success_criteria=输出简要结论; context_refs=per_001",
        decision_trace='{"source":"unit-test"}',
    )


def _sample_tool_registry() -> ToolRegistry:
    """构造仅供 executor 测试使用的工具注册表。"""
    registry = ToolRegistry()
    registry.register(
        ToolSpec("sample_tool", "Return text.", {"required": ["text"]}, is_concurrency_safe=True),
        _sample_handler,
    )
    return registry


def _sample_handler(tool_call, tool_use_context) -> ToolResultEnvelope:
    """返回工具输入文本，证明 executor 复用了工具执行链。"""
    del tool_use_context
    return ToolResultEnvelope(tool_call.tool_use_id, tool_call.tool_name, True, str(tool_call.tool_input["text"]))


def _tool_response() -> ModelResponse:
    """构造包含工具调用的模型响应。"""
    return ModelResponse(
        (
            AgentContentBlock(
                type="tool_use",
                tool_use_id="tool_bg_1",
                tool_name="sample_tool",
                tool_input={"text": "hello"},
            ),
        ),
        "tool_use",
    )


def _text_response(text: str) -> ModelResponse:
    """构造文本模型响应。"""
    return ModelResponse((AgentContentBlock(type="text", text=text),), "stop")


def _latest_user_text(state) -> str:
    """提取 AgentState 中最近一条用户文本。"""
    for message in reversed(state.messages):
        if message.role == "user":
            return "\n".join(block.text for block in message.content if block.type == "text" and block.text)
    return ""


def _self_test() -> None:
    """运行本文件单元测试。"""
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestBackgroundSubagentExecutor)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    _self_test()
