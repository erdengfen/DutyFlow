# 本文件负责把 AgentState 投影为下一次模型调用可见的 messages。

from __future__ import annotations

from dataclasses import replace

from dutyflow.agent.state import AgentMessage, AgentState


class RuntimeContextManager:
    """管理模型调用前的运行时上下文投影，不拥有 AgentState 源状态。"""

    def project(self, state: AgentState) -> tuple[AgentMessage, ...]:
        """返回 ModelContextView 概念层对应的现有 messages 表示。"""
        return self.project_messages(state)

    def project_messages(self, state: AgentState) -> tuple[AgentMessage, ...]:
        """返回模型下一次调用应看到的 AgentMessage 序列。"""
        return state.messages

    def project_state_for_model(self, state: AgentState) -> AgentState:
        """把投影后的 messages 渲染回现有 AgentState 结构供模型客户端消费。"""
        projected_messages = self.project(state)
        if projected_messages is state.messages:
            return state
        return replace(state, messages=projected_messages)


def _self_test() -> None:
    """验证第一版投影层保持 messages 结构不变。"""
    from dutyflow.agent.state import create_initial_agent_state

    state = create_initial_agent_state("ctx_self_test", "hello")
    manager = RuntimeContextManager()
    projected = manager.project_state_for_model(state)
    assert manager.project(state) == state.messages
    assert projected.messages == state.messages


if __name__ == "__main__":
    _self_test()
    print("dutyflow runtime context self-test passed")
