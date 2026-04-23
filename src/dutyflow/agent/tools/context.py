# 本文件定义工具 handler 可显式共享的 ToolUseContext。

from __future__ import annotations

import sys

_THIS_DIR = __file__.rsplit("/", 1)[0]
if sys.path and sys.path[0] == _THIS_DIR:
    sys.path.pop(0)

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from dutyflow.agent.skills import SkillRegistry
from dutyflow.agent.state import AgentState, create_initial_agent_state
from dutyflow.agent.tools.registry import ToolRegistry


class ApprovalRequester(Protocol):
    """定义工具执行前人工审批回调的最小协议。"""

    def __call__(self, tool_name: str, reason: str, tool_input: Mapping[str, Any]) -> bool:
        """返回用户是否允许继续执行该工具。"""


class AuditLoggerLike(Protocol):
    """定义执行层记录审计日志所需的最小接口。"""

    def preview(self, value: Any) -> str:
        """返回与审计日志一致的统一预览字符串。"""

    def record_event(
        self,
        *,
        category: str,
        event_type: str,
        outcome: str,
        note: str,
        query_id: str = "",
        task_id: str = "",
        trace_id: str = "",
        recovery_id: str = "",
        tool_use_id: str = "",
        tool_name: str = "",
        permission_mode: str = "",
        turn_count: int = 0,
        payload: Mapping[str, Any] | None = None,
    ) -> object:
        """写入一条结构化审计记录。"""


@dataclass(frozen=True)
class ToolUseContext:
    """提供工具执行时可读取的共享环境，不保存任何密钥。

    说明：
    - `agent_state` 指向当前 Agent 主状态，只允许工具只读访问。
    - 工具不得直接修改主状态；状态变更只能通过 ToolResultEnvelope 回写链路完成。
    """

    query_id: str
    cwd: Path
    agent_state: AgentState
    registry: ToolRegistry
    permission_mode: str = "default"
    approval_requester: ApprovalRequester | None = None
    audit_logger: AuditLoggerLike | None = None
    skill_registry: SkillRegistry | None = None
    runtime_metadata: Mapping[str, Any] = field(default_factory=dict)
    notifications: tuple[str, ...] = ()
    tool_content: Mapping[str, Any] = field(default_factory=dict)


def _self_test() -> None:
    """验证 ToolUseContext 可承载共享 tool_content。"""
    state = create_initial_agent_state("query_ctx", "hello")
    context = ToolUseContext("query_ctx", Path.cwd(), state, ToolRegistry())
    assert context.tool_content == {}
    assert context.permission_mode == "default"
    assert context.skill_registry is None


if __name__ == "__main__":
    _self_test()
    print("dutyflow tool context self-test passed")
