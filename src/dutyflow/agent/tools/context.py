# 本文件定义工具 handler 可显式共享的 ToolUseContext。

from __future__ import annotations

import sys

_THIS_DIR = __file__.rsplit("/", 1)[0]
if sys.path and sys.path[0] == _THIS_DIR:
    sys.path.pop(0)

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from dutyflow.agent.state import AgentState, create_initial_agent_state
from dutyflow.agent.tools.registry import ToolRegistry


@dataclass(frozen=True)
class ToolUseContext:
    """提供工具执行时可读取的共享环境，不保存任何密钥。"""

    query_id: str
    cwd: Path
    agent_state: AgentState
    registry: ToolRegistry
    runtime_metadata: Mapping[str, Any] = field(default_factory=dict)
    notifications: tuple[str, ...] = ()
    tool_content: Mapping[str, Any] = field(default_factory=dict)


def _self_test() -> None:
    """验证 ToolUseContext 可承载共享 tool_content。"""
    state = create_initial_agent_state("query_ctx", "hello")
    context = ToolUseContext("query_ctx", Path.cwd(), state, ToolRegistry())
    assert context.tool_content == {}


if __name__ == "__main__":
    _self_test()
    print("dutyflow tool context self-test passed")
