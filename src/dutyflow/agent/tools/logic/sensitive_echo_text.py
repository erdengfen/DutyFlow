# 本文件实现 sensitive_echo_text 工具的实际执行逻辑。

from __future__ import annotations

from dutyflow.agent.tools.contracts.sensitive_echo_text_contract import SENSITIVE_ECHO_TEXT_TOOL_CONTRACT
from dutyflow.agent.tools.types import ToolCall, ToolResultEnvelope


class SensitiveEchoTextTool:
    """敏感测试工具，仅在人工审批通过后返回输入文本。"""

    name = "sensitive_echo_text"
    contract = SENSITIVE_ECHO_TEXT_TOOL_CONTRACT
    is_concurrency_safe = False
    requires_approval = True
    timeout_seconds = 30.0
    max_retries = 0
    retry_policy = "none"
    idempotency = "idempotent"
    degradation_mode = "escalate"
    fallback_tool_names = ()

    def handle(
        self,
        tool_call: ToolCall,
        tool_use_context,
    ) -> ToolResultEnvelope:
        """返回审批通过后的敏感测试结果文本。"""
        text = str(tool_call.tool_input["text"])
        content = "sensitive_echo_text approved: " + text
        return ToolResultEnvelope(tool_call.tool_use_id, tool_call.tool_name, True, content)


def _self_test() -> None:
    """验证 SensitiveEchoTextTool 名称与 contract 一致。"""
    assert SensitiveEchoTextTool.name == SensitiveEchoTextTool.contract["function"]["name"]


if __name__ == "__main__":
    _self_test()
    print("dutyflow agent sensitive_echo_text logic self-test passed")
