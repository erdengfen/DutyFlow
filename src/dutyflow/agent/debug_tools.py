# 本文件提供 Step 2.4 调试用假工具注册表，不接入真实外部能力。

from __future__ import annotations

from dutyflow.agent.tools.registry import ToolRegistry
from dutyflow.agent.tools.types import ToolCall, ToolResultEnvelope, ToolSpec


def create_debug_tool_registry() -> ToolRegistry:
    """创建 CLI /chat 调试用工具注册表。"""
    registry = ToolRegistry()
    registry.register(_echo_spec(), _echo_text)
    registry.register(ToolSpec("fail_tool", "调试用失败工具。"), _fail_tool)
    return registry


def _echo_spec() -> ToolSpec:
    """返回 echo_text 调试工具定义。"""
    return ToolSpec(
        name="echo_text",
        description="返回输入 text，用于验证工具调用链路。",
        input_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
        is_concurrency_safe=True,
    )


def _echo_text(
    tool_call: ToolCall,
    tool_use_context,
) -> ToolResultEnvelope:
    """返回工具输入文本，并允许读取调试共享前缀。"""
    prefix = str(tool_use_context.tool_content.get("prefix", ""))
    text = str(tool_call.tool_input["text"])
    return ToolResultEnvelope(tool_call.tool_use_id, tool_call.tool_name, True, prefix + text)


def _fail_tool(
    tool_call: ToolCall,
    tool_use_context,
) -> ToolResultEnvelope:
    """主动抛出异常，用于验证 ToolExecutor 错误封装。"""
    raise RuntimeError("debug fail_tool triggered")


def _self_test() -> None:
    """验证调试工具注册表包含 echo_text。"""
    registry = create_debug_tool_registry()
    assert registry.has("echo_text")


if __name__ == "__main__":
    _self_test()
    print("dutyflow debug tools self-test passed")
