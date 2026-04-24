# 本文件实现 close_cli_session 工具的实际执行逻辑。

from __future__ import annotations

import json

from dutyflow.agent.cli_session import CliSessionError, get_cli_session_manager
from dutyflow.agent.tools.contracts.close_cli_session_contract import CLOSE_CLI_SESSION_TOOL_CONTRACT
from dutyflow.agent.tools.types import ToolCall, ToolResultEnvelope, error_envelope


class CloseCliSessionTool:
    """关闭持久 bash 会话的危险内部工具。"""

    name = "close_cli_session"
    contract = CLOSE_CLI_SESSION_TOOL_CONTRACT
    is_concurrency_safe = False
    requires_approval = True
    timeout_seconds = 30.0
    max_retries = 0
    retry_policy = "none"
    idempotency = "unsafe"
    degradation_mode = "escalate"
    fallback_tool_names = ()

    def handle(self, tool_call: ToolCall, tool_use_context) -> ToolResultEnvelope:
        """关闭指定 session，并返回结构化清理结果。"""
        try:
            payload = get_cli_session_manager().close_session(str(tool_call.tool_input["session_id"]))
        except CliSessionError as exc:
            return error_envelope(tool_call, exc.kind, str(exc))
        return ToolResultEnvelope(
            tool_call.tool_use_id,
            tool_call.tool_name,
            True,
            json.dumps(payload, ensure_ascii=False, indent=2),
        )


def _self_test() -> None:
    """验证 CloseCliSessionTool 名称与 contract 一致。"""
    assert CloseCliSessionTool.name == CloseCliSessionTool.contract["function"]["name"]


if __name__ == "__main__":
    _self_test()
    print("dutyflow agent close_cli_session logic self-test passed")
