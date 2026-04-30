# 本文件验证后台 subagent 执行链的任务状态、结果文件和系统层飞书回推。

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import time
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dutyflow.agent.background_task_worker import BackgroundTaskWorker  # noqa: E402
from dutyflow.agent.model_client import ModelResponse  # noqa: E402
from dutyflow.agent.state import AgentContentBlock, create_initial_agent_state  # noqa: E402
from dutyflow.agent.tools.context import ToolUseContext  # noqa: E402
from dutyflow.agent.tools.logic.task_tools.create_background_task import CreateBackgroundTaskTool  # noqa: E402
from dutyflow.agent.tools.registry import create_runtime_tool_registry  # noqa: E402
from dutyflow.agent.tools.types import ToolCall  # noqa: E402
from dutyflow.feedback.gateway import FeedbackResult  # noqa: E402
from dutyflow.tasks.task_result import TaskResultStore  # noqa: E402
from dutyflow.tasks.task_state import TaskStore  # noqa: E402


class TestBackgroundSubagentChain(unittest.TestCase):
    """验证后台任务从创建、执行、落盘到回推的完整链路。"""

    def test_background_task_chain_updates_task_result_and_feedback(self) -> None:
        """后台任务完成后应同时更新任务、结果文件，并通过系统反馈出口回推。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            create_result = CreateBackgroundTaskTool().handle(_create_task_call(), _context(root))
            payload = json.loads(create_result.content)
            task_id = str(payload["task_id"])
            model = _CapturingModelClient("后台链路结果：已经整理完成。")
            feedback = _FakeFeedbackGateway()
            worker = BackgroundTaskWorker(
                TaskStore(root),
                model_client=model,
                feedback_gateway=feedback,
                queue_poll_seconds=0.01,
            )
            worker.start()
            worker.enqueue_task(task_id, source="chain_test")
            worker_state = _wait_until_processed(worker)
            task = TaskStore(root).read_task(task_id)
            result = TaskResultStore(root).read_result(task_id)
            worker.stop()
        self.assertTrue(create_result.ok)
        self.assertEqual(worker_state.latest_action, "processed")
        self.assertEqual(model.seen_tool_names, [()])
        self.assertEqual(feedback.sent_texts, [("oc_chain_chat", "后台链路结果：已经整理完成。")])
        self.assertIsNotNone(task)
        assert task is not None
        self.assertEqual(task.status, "completed")
        self.assertEqual(task.retry_status, "done")
        self.assertEqual(task.attempt_count, "1")
        self.assertEqual(task.source_event_id, "evt_chain_001")
        self.assertEqual(task.source_id, "oc_chain_chat")
        self.assertIn("后台链路结果", task.last_result_summary)
        self.assertIn("已通过飞书回推", task.next_action)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.task_id, task_id)
        self.assertEqual(result.user_visible_final_text, "后台链路结果：已经整理完成。")
        self.assertEqual(result.stop_reason, "stop")
        self.assertEqual(result.tool_result_count, "0")
        self.assertEqual(result.query_id, f"bg_task_{task_id}")
        self.assertEqual(result.source_task_file, f"data/tasks/{task_id}.md")


def _create_task_call() -> ToolCall:
    """构造后台任务创建工具调用。"""
    return ToolCall(
        "tool_chain_create_001",
        "create_background_task",
        {
            "title": "整理后台链路资料",
            "goal": "整理来自飞书消息的资料并返回结论",
            "success_criteria": "输出一条可直接回推给用户的中文结论",
            "user_visible_summary": "已开始整理后台链路资料。",
            "context_refs": "per_chain_001,evt_chain_001",
            "capability_requirements": "",
            "preferred_skills": "",
            "preferred_tools": "",
        },
        0,
        0,
    )


def _context(root: Path) -> ToolUseContext:
    """构造带飞书感知上下文的工具执行上下文。"""
    registry = create_runtime_tool_registry()
    return ToolUseContext(
        "query_chain_001",
        root,
        create_initial_agent_state("query_chain_001", "create task"),
        registry,
        tool_content={
            "perception": {
                "source_event_id": "evt_chain_001",
                "chat_id": "oc_chain_chat",
            }
        },
    )


class _CapturingModelClient:
    """返回固定后台任务结果，并记录 subagent 可见工具面。"""

    def __init__(self, final_text: str) -> None:
        """保存固定最终文本。"""
        self.final_text = final_text
        self.seen_tool_names: list[tuple[str, ...]] = []

    def call_model(self, state, tools) -> ModelResponse:
        """记录工具面并返回完成文本。"""
        del state
        self.seen_tool_names.append(tuple(tool.name for tool in tools))
        return ModelResponse((AgentContentBlock(type="text", text=self.final_text),), "stop")


class _FakeFeedbackGateway:
    """记录后台任务完成后的系统层回推。"""

    def __init__(self) -> None:
        """初始化发送记录。"""
        self.sent_texts: list[tuple[str, str]] = []

    def send_text(self, chat_id: str, text: str) -> FeedbackResult:
        """记录指定会话回推。"""
        self.sent_texts.append((chat_id, text))
        return FeedbackResult(ok=True, status="sent", detail="fake", payload={"chat_id": chat_id})

    def send_owner_text(self, text: str) -> FeedbackResult:
        """记录 owner 回推。"""
        return self.send_text("owner", text)


def _wait_until_processed(worker: BackgroundTaskWorker) -> object:
    """等待后台 worker 完成一条任务。"""
    deadline = time.time() + 1.0
    latest = worker.get_state()
    while time.time() < deadline:
        latest = worker.get_state()
        if latest.processed_count == 1:
            return latest
        time.sleep(0.02)
    raise AssertionError(f"background subagent chain did not finish: {latest}")


def _self_test() -> None:
    """运行本文件单元测试。"""
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestBackgroundSubagentChain)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    _self_test()
