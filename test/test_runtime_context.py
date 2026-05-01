# 本文件验证 Runtime Context 第一版只做模型调用前 messages 投影。

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sys
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dutyflow.agent.core_loop import AgentLoop  # noqa: E402
from dutyflow.agent.model_client import ModelResponse  # noqa: E402
from dutyflow.agent.state import (  # noqa: E402
    AgentContentBlock,
    AgentState,
    create_initial_agent_state,
)
from dutyflow.agent.tools.registry import ToolRegistry  # noqa: E402
from dutyflow.context.runtime_context import RuntimeContextManager  # noqa: E402


class TestRuntimeContextManager(unittest.TestCase):
    """验证 ModelContextView 概念层不新增独立数据结构。"""

    def test_first_version_projects_existing_messages(self) -> None:
        """第一版投影应直接返回现有 AgentMessage 序列。"""
        state = create_initial_agent_state("ctx_001", "hello")
        manager = RuntimeContextManager()
        self.assertIs(manager.project(state), state.messages)
        self.assertIs(manager.project_messages(state), state.messages)
        self.assertIs(manager.project_state_for_model(state), state)

    def test_agent_loop_calls_model_with_projected_state(self) -> None:
        """AgentLoop 调模型前应使用 RuntimeContextManager 输出的投影 state。"""
        manager = _MarkerRuntimeContextManager()
        client = _CapturingModelClient()
        loop = AgentLoop(
            client,
            ToolRegistry(),
            PROJECT_ROOT,
            runtime_context_manager=manager,
        )
        result = loop.run_until_stop("hello", query_id="ctx_projection")
        self.assertEqual(manager.project_count, 1)
        self.assertIn(_PROJECTION_MARKER, client.first_system_text)
        self.assertNotIn(_PROJECTION_MARKER, _first_system_text(result.state))
        self.assertEqual(result.final_text, "ok")


class _MarkerRuntimeContextManager(RuntimeContextManager):
    """测试用投影器：只改模型可见 system message，不改源 state。"""

    def __init__(self) -> None:
        """记录投影调用次数。"""
        self.project_count = 0

    def project_state_for_model(self, state: AgentState) -> AgentState:
        """返回带测试标记的投影 state。"""
        self.project_count += 1
        first = state.messages[0]
        block = first.content[0]
        marked_block = replace(block, text=block.text + "\n" + _PROJECTION_MARKER)
        marked_message = replace(first, content=(marked_block,))
        return replace(state, messages=(marked_message,) + state.messages[1:])


class _CapturingModelClient:
    """保存模型调用时实际收到的 state。"""

    def __init__(self) -> None:
        """初始化捕获字段。"""
        self.first_system_text = ""

    def call_model(self, state, tools) -> ModelResponse:
        """捕获 system prompt 并返回固定文本。"""
        del tools
        self.first_system_text = _first_system_text(state)
        return ModelResponse((AgentContentBlock(type="text", text="ok"),), "stop")


def _first_system_text(state: AgentState) -> str:
    """提取 state 第一条 system message 的文本。"""
    if not state.messages or state.messages[0].role != "system":
        return ""
    return "\n".join(block.text for block in state.messages[0].content if block.type == "text")


_PROJECTION_MARKER = "runtime-context-projection-marker"


if __name__ == "__main__":
    unittest.main()
