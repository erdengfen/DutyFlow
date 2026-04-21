# 本文件负责 Step 2.4 的最小多轮调试 loop，不代表最终生产 agent loop。

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping, Sequence
from uuid import uuid4

from dutyflow.agent.model_client import ModelClient
from dutyflow.agent.state import (
    AgentContentBlock,
    AgentState,
    append_user_message,
    append_assistant_message,
    append_tool_results,
    create_initial_agent_state,
    mark_transition,
    to_dict,
)
from dutyflow.agent.tools.context import ToolUseContext
from dutyflow.agent.tools.executor import ToolExecutor
from dutyflow.agent.tools.registry import ToolRegistry
from dutyflow.agent.tools.router import ToolRouter
from dutyflow.agent.tools.types import ToolCall, ToolResultEnvelope


@dataclass(frozen=True)
class AgentLoopResult:
    """保存 /chat 调试 loop 的完整可见结果。"""

    state: AgentState
    final_text: str
    stop_reason: str
    turn_count: int
    tool_results: tuple[ToolResultEnvelope, ...]

    @property
    def tool_result_count(self) -> int:
        """返回工具结果数量。"""
        return len(self.tool_results)

    def to_debug_text(self) -> str:
        """返回 CLI 可打印的完整调试文本。"""
        payload = {
            "final_text": self.final_text,
            "stop_reason": self.stop_reason,
            "turn_count": self.turn_count,
            "tool_result_count": self.tool_result_count,
            "tool_results": [_tool_result_to_dict(item) for item in self.tool_results],
            "agent_state": to_dict(self.state),
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)


@dataclass
class ChatDebugSession:
    """维护 CLI /chat 子会话中的持续 Agent State。"""

    loop: "AgentLoop"
    state: AgentState | None = None

    def run_turn(self, user_text: str) -> AgentLoopResult:
        """执行 chat 子会话的一轮用户输入并更新当前状态。"""
        result = self.loop.run_until_stop(user_text, state=self.state)
        self.state = result.state
        return result


class AgentLoop:
    """执行基于 Agent State 和工具控制层的最小多轮调试链路。"""

    def __init__(
        self,
        model_client: ModelClient,
        registry: ToolRegistry,
        cwd: Path,
        max_turns: int = 6,
    ) -> None:
        """绑定模型客户端、工具注册表和运行目录。"""
        self.model_client = model_client
        self.registry = registry
        self.router = ToolRouter(registry)
        self.executor = ToolExecutor(registry)
        self.cwd = cwd
        # 关键开关：CLI /chat 调试链路允许的最大工具续转轮数；超过后直接停止，防止无限循环。
        self.max_turns = max_turns

    def run_until_stop(
        self,
        user_text: str,
        query_id: str | None = None,
        tool_content: Mapping[str, Any] | None = None,
        state: AgentState | None = None,
    ) -> AgentLoopResult:
        """运行一轮 /chat 调试输入，直到模型停止或达到轮数限制。"""
        state = self._prepare_state(user_text, query_id, state)
        tool_results: list[ToolResultEnvelope] = []
        local_turns = 0
        while True:
            response = self.model_client.call_model(state, self.registry.list_specs())
            state = append_assistant_message(state, response.assistant_blocks)
            tool_calls = extract_tool_calls(state)
            if not tool_calls:
                return _finish_result(state, _final_text(response.assistant_blocks), response.stop_reason, tool_results)
            local_turns += 1
            if local_turns >= self.max_turns:
                return _failed_result(state, "max_turns_reached", tool_results)
            envelopes = self._execute_tool_calls(state, tool_calls, tool_content or {})
            tool_results.extend(envelopes)
            state = append_tool_results(state, tuple(item.to_agent_block() for item in envelopes))

    def _prepare_state(
        self,
        user_text: str,
        query_id: str | None,
        state: AgentState | None,
    ) -> AgentState:
        """创建或复用 Agent State，并追加本轮用户输入。"""
        if state is None:
            prepared = create_initial_agent_state(query_id or _new_query_id(), user_text)
        else:
            prepared = append_user_message(state, user_text)
            prepared = replace(prepared, turn_count=prepared.turn_count + 1)
            prepared = mark_transition(prepared, "user_continuation")
        # 关键开关：把本次 loop 允许追加的最大轮数写回 AgentState，供状态层统一兜底校验。
        return replace(prepared, max_turns=prepared.turn_count + self.max_turns)

    def _execute_tool_calls(
        self,
        state: AgentState,
        tool_calls: Sequence[ToolCall],
        tool_content: Mapping[str, Any],
    ) -> tuple[ToolResultEnvelope, ...]:
        """通过 Router 和 Executor 执行工具调用。"""
        routes = self.router.route_many(tuple(tool_calls))
        context = ToolUseContext(state.query_id, self.cwd, state, self.registry, tool_content=tool_content)
        return self.executor.execute_routes(routes, context)


def extract_tool_calls(state: AgentState) -> tuple[ToolCall, ...]:
    """从最后一条 assistant 消息中提取 ToolCall。"""
    message_index = len(state.messages) - 1
    message = state.messages[message_index]
    calls: list[ToolCall] = []
    for block_index, block in enumerate(message.content):
        if block.type == "tool_use":
            calls.append(ToolCall.from_agent_block(block, message_index, block_index))
    return tuple(calls)


def _finish_result(
    state: AgentState,
    final_text: str,
    stop_reason: str,
    tool_results: Sequence[ToolResultEnvelope],
) -> AgentLoopResult:
    """生成成功结束结果。"""
    finished = mark_transition(state, "finished")
    return AgentLoopResult(finished, final_text, stop_reason or "stop", finished.turn_count, tuple(tool_results))


def _failed_result(
    state: AgentState,
    reason: str,
    tool_results: Sequence[ToolResultEnvelope],
) -> AgentLoopResult:
    """生成失败结束结果。"""
    failed = mark_transition(state, "failed")
    return AgentLoopResult(failed, reason, reason, failed.turn_count, tuple(tool_results))


def _final_text(blocks: Sequence[AgentContentBlock]) -> str:
    """提取 assistant 文本结果。"""
    texts = [block.text for block in blocks if block.type == "text" and block.text]
    return "\n".join(texts)


def _tool_result_to_dict(result: ToolResultEnvelope) -> dict[str, Any]:
    """把工具结果信封转换为调试字典。"""
    return {
        "tool_use_id": result.tool_use_id,
        "tool_name": result.tool_name,
        "ok": result.ok,
        "content": result.content,
        "is_error": result.is_error,
        "error_kind": result.error_kind,
        "call_index": result.call_index,
    }


def _new_query_id() -> str:
    """生成本地调试 query id。"""
    return "chat_" + uuid4().hex


def _self_test() -> None:
    """验证 extract_tool_calls 能读取最后一条 assistant 消息。"""
    state = create_initial_agent_state("query_loop", "hello")
    block = AgentContentBlock(type="tool_use", tool_use_id="tool_1", tool_name="echo_text")
    state = append_assistant_message(state, (block,))
    assert extract_tool_calls(state)[0].tool_name == "echo_text"


if __name__ == "__main__":
    _self_test()
    print("dutyflow agent loop self-test passed")
