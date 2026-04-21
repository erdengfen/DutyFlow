# 本文件实现 echo_text 工具的实际执行逻辑。

from __future__ import annotations

from dutyflow.agent.tools.contracts.echo_text_contract import ECHO_TEXT_TOOL_CONTRACT
from dutyflow.agent.tools.types import ToolCall, ToolResultEnvelope


class EchoTextTool:
    """测试用回显工具，验证工具执行和回写链路。"""

    name = "echo_text"
    contract = ECHO_TEXT_TOOL_CONTRACT
    is_concurrency_safe = True
    requires_approval = False
    timeout_seconds = 30.0
    max_retries = 3
    retry_policy = "transient_only"
    idempotency = "read_only"
    degradation_mode = "none"
    fallback_tool_names = ()

    def handle(
        self,
        tool_call: ToolCall,
        tool_use_context,
    ) -> ToolResultEnvelope:
        """返回输入文本，并读取共享前缀作为调试上下文。"""
        prefix = str(tool_use_context.tool_content.get("prefix", ""))
        text = str(tool_call.tool_input["text"])
        return ToolResultEnvelope(tool_call.tool_use_id, tool_call.tool_name, True, prefix + text)


def _self_test() -> None:
    """验证 EchoTextTool 名称与 contract 一致。"""
    assert EchoTextTool.name == EchoTextTool.contract["function"]["name"]


if __name__ == "__main__":
    _self_test()
    print("dutyflow agent echo_text logic self-test passed")
