# 本文件实现 exec_cli_command 工具的实际执行逻辑。

from __future__ import annotations

import json

from dutyflow.agent.cli_session import CliSessionError, get_cli_session_manager
from dutyflow.agent.tools.contracts.exec_cli_command_contract import EXEC_CLI_COMMAND_TOOL_CONTRACT
from dutyflow.agent.tools.types import ToolCall, ToolResultEnvelope, error_envelope


class ExecCliCommandTool:
    """在持久 bash 会话中执行一条危险命令的内部工具。"""

    name = "exec_cli_command"
    contract = EXEC_CLI_COMMAND_TOOL_CONTRACT
    is_concurrency_safe = False
    requires_approval = True
    timeout_seconds = 30.0
    max_retries = 0
    retry_policy = "none"
    idempotency = "unsafe"
    degradation_mode = "escalate"
    fallback_tool_names = ()

    def handle(self, tool_call: ToolCall, tool_use_context) -> ToolResultEnvelope:
        """在指定 session 中执行一条命令，并返回结构化命令结果。"""
        try:
            payload = get_cli_session_manager().exec_command(
                session_id=str(tool_call.tool_input["session_id"]),
                command=str(tool_call.tool_input["command"]),
                timeout_seconds=float(tool_call.tool_input["timeout"]),
            )
        except CliSessionError as exc:
            if exc.payload:
                return ToolResultEnvelope(
                    tool_call.tool_use_id,
                    tool_call.tool_name,
                    False,
                    json.dumps(exc.payload, ensure_ascii=False, indent=2),
                    is_error=True,
                    error_kind=exc.kind,
                )
            return error_envelope(tool_call, exc.kind, str(exc))
        return ToolResultEnvelope(
            tool_call.tool_use_id,
            tool_call.tool_name,
            True,
            json.dumps(payload, ensure_ascii=False, indent=2),
        )


def _self_test() -> None:
    """验证 ExecCliCommandTool 名称与 contract 一致。"""
    assert ExecCliCommandTool.name == ExecCliCommandTool.contract["function"]["name"]


if __name__ == "__main__":
    _self_test()
    print("dutyflow agent exec_cli_command logic self-test passed")
