# 本文件实现 open_cli_session 工具的实际执行逻辑。

from __future__ import annotations

import json

from dutyflow.agent.cli_session import CliSessionError, get_cli_session_manager
from dutyflow.agent.tools.contracts.cli_tools.open_cli_session_contract import OPEN_CLI_SESSION_TOOL_CONTRACT
from dutyflow.agent.tools.types import ToolCall, ToolResultEnvelope, error_envelope


class OpenCliSessionTool:
    """创建持久 bash 会话的内部工具。"""

    name = "open_cli_session"
    contract = OPEN_CLI_SESSION_TOOL_CONTRACT
    is_concurrency_safe = False
    requires_approval = False
    timeout_seconds = 30.0
    max_retries = 0
    retry_policy = "none"
    idempotency = "read_only"
    degradation_mode = "none"
    fallback_tool_names = ()

    def handle(self, tool_call: ToolCall, tool_use_context) -> ToolResultEnvelope:
        """创建新的 CLI session，并返回结构化会话信息。"""
        try:
            payload = get_cli_session_manager().open_session(
                base_cwd=tool_use_context.cwd,
                cwd_text=str(tool_call.tool_input["cwd"]),
                timeout_seconds=float(tool_call.tool_input["timeout"]),
                shell_type=str(tool_call.tool_input.get("shell_type", "bash") or "bash"),
            )
        except CliSessionError as exc:
            return error_envelope(tool_call, exc.kind, str(exc))
        return ToolResultEnvelope(
            tool_call.tool_use_id,
            tool_call.tool_name,
            True,
            json.dumps(payload, ensure_ascii=False, indent=2),
        )


def _self_test() -> None:
    """验证 OpenCliSessionTool 名称与 contract 一致。"""
    assert OpenCliSessionTool.name == OpenCliSessionTool.contract["function"]["name"]


if __name__ == "__main__":
    _self_test()
    print("dutyflow agent open_cli_session logic self-test passed")
