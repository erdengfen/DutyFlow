# 本文件定义工具协议对象，负责 ToolSpec、ToolCall 和结果信封。

from __future__ import annotations

import sys

_THIS_DIR = __file__.rsplit("/", 1)[0]
if sys.path and sys.path[0] == _THIS_DIR:
    sys.path.pop(0)

from dataclasses import dataclass, field
from typing import Any, Mapping

from dutyflow.agent.state import AgentContentBlock

TOOL_SOURCES = frozenset({"native", "placeholder", "mcp_reserved", "agent_reserved"})


@dataclass(frozen=True)
class ToolSpec:
    """描述模型可见工具及其执行来源，不负责执行。"""

    name: str
    description: str
    input_schema: Mapping[str, Any] = field(default_factory=dict)
    source: str = "native"
    is_concurrency_safe: bool = False
    requires_approval: bool = False
    provider_contract: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """校验工具定义的稳定字段。"""
        if not self.name:
            raise ValueError("ToolSpec.name is required")
        if not self.description:
            raise ValueError("ToolSpec.description is required")
        if self.source not in TOOL_SOURCES:
            raise ValueError(f"Unknown tool source: {self.source}")
        if not isinstance(self.input_schema, Mapping):
            raise ValueError("ToolSpec.input_schema must be a mapping")
        if self.provider_contract:
            _validate_tool_contract(self.provider_contract)

    def required_inputs(self) -> tuple[str, ...]:
        """返回 input_schema 中声明的必填字段。"""
        required = self.input_schema.get("required", ())
        if not isinstance(required, (list, tuple)):
            raise ValueError("ToolSpec.input_schema.required must be a list")
        return tuple(str(item) for item in required)

    @classmethod
    def from_contract(
        cls,
        contract: Mapping[str, Any],
        source: str = "native",
        is_concurrency_safe: bool = False,
        requires_approval: bool = False,
    ) -> "ToolSpec":
        """从工具 contract 结构加载 ToolSpec。"""
        _validate_tool_contract(contract)
        function = contract["function"]
        return cls(
            name=str(function["name"]),
            description=str(function["description"]),
            input_schema=dict(function.get("parameters", {})),
            source=source,
            is_concurrency_safe=is_concurrency_safe,
            requires_approval=requires_approval,
            provider_contract=dict(contract),
        )

    def to_contract(self) -> dict[str, Any]:
        """返回模型可见的工具 contract 结构。"""
        if self.provider_contract:
            return dict(self.provider_contract)
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": dict(self.input_schema) or {"type": "object", "properties": {}},
            },
        }


@dataclass(frozen=True)
class ToolCall:
    """表示模型发出的单次工具调用意图。"""

    tool_use_id: str
    tool_name: str
    tool_input: Mapping[str, Any]
    source_message_index: int
    call_index: int

    def __post_init__(self) -> None:
        """校验工具调用必须带稳定 ID 和 dict 输入。"""
        if not self.tool_use_id:
            raise ValueError("ToolCall.tool_use_id is required")
        if not self.tool_name:
            raise ValueError("ToolCall.tool_name is required")
        if not isinstance(self.tool_input, Mapping):
            raise ValueError("ToolCall.tool_input must be a mapping")
        if self.source_message_index < 0 or self.call_index < 0:
            raise ValueError("ToolCall indexes must be >= 0")

    @classmethod
    def from_agent_block(
        cls,
        block: AgentContentBlock,
        source_message_index: int,
        call_index: int,
    ) -> "ToolCall":
        """从 AgentContentBlock 的 tool_use 块构造 ToolCall。"""
        if block.type != "tool_use":
            raise ValueError("ToolCall can only be created from tool_use block")
        return cls(
            tool_use_id=block.tool_use_id,
            tool_name=block.tool_name,
            tool_input=dict(block.tool_input),
            source_message_index=source_message_index,
            call_index=call_index,
        )


@dataclass(frozen=True)
class ToolResultEnvelope:
    """统一封装工具执行结果，供 Agent State 回写为 tool_result。"""

    tool_use_id: str
    tool_name: str
    ok: bool
    content: str
    is_error: bool = False
    error_kind: str = ""
    attachments: tuple[str, ...] = ()
    context_modifiers: tuple[Mapping[str, Any], ...] = ()
    call_index: int = 0

    def __post_init__(self) -> None:
        """校验结果信封必须可回写到 tool_result。"""
        if not self.tool_use_id:
            raise ValueError("ToolResultEnvelope.tool_use_id is required")
        if not self.tool_name:
            raise ValueError("ToolResultEnvelope.tool_name is required")
        if self.is_error and not self.error_kind:
            raise ValueError("ToolResultEnvelope.error_kind is required for errors")

    def to_agent_block(self) -> AgentContentBlock:
        """转换为 Agent State 可回写的 tool_result 内容块。"""
        return AgentContentBlock(
            type="tool_result",
            tool_use_id=self.tool_use_id,
            tool_name=self.tool_name,
            content=self.content,
            is_error=self.is_error,
        )


def error_envelope(
    tool_call: ToolCall,
    error_kind: str,
    content: str,
) -> ToolResultEnvelope:
    """创建统一错误结果信封，避免异常穿透 agent loop。"""
    return ToolResultEnvelope(
        tool_use_id=tool_call.tool_use_id,
        tool_name=tool_call.tool_name,
        ok=False,
        content=content,
        is_error=True,
        error_kind=error_kind,
        call_index=tool_call.call_index,
    )


def _validate_tool_contract(contract: Mapping[str, Any]) -> None:
    """校验工具 contract 的最小结构。"""
    if contract.get("type") != "function":
        raise ValueError("tool contract type must be function")
    function = contract.get("function")
    if not isinstance(function, Mapping):
        raise ValueError("tool contract.function must be a mapping")
    if not function.get("name"):
        raise ValueError("tool contract.function.name is required")
    if not function.get("description"):
        raise ValueError("tool contract.function.description is required")
    parameters = function.get("parameters", {})
    if not isinstance(parameters, Mapping):
        raise ValueError("tool contract.function.parameters must be a mapping")


def _self_test() -> None:
    """验证工具调用可转换为 tool_result 内容块。"""
    call = ToolCall("tool_1", "echo_text", {"text": "ok"}, 0, 0)
    result = ToolResultEnvelope(call.tool_use_id, call.tool_name, True, "ok")
    assert result.to_agent_block().tool_use_id == "tool_1"
    spec = ToolSpec.from_contract(
        {
            "type": "function",
            "function": {
                "name": "echo_text",
                "description": "Echo text.",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        }
    )
    assert spec.to_contract()["function"]["name"] == "echo_text"


if __name__ == "__main__":
    _self_test()
    print("dutyflow agent tool types self-test passed")
