# 本文件负责模型调用适配，把外部响应转换为 AgentContentBlock。

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence

from dutyflow.agent.state import AgentContentBlock, AgentMessage, AgentState
from dutyflow.agent.tools.types import ToolSpec
from dutyflow.config.env import EnvConfig


@dataclass(frozen=True)
class ModelResponse:
    """保存一次模型调用的规范化结果。"""

    assistant_blocks: tuple[AgentContentBlock, ...]
    stop_reason: str
    raw_provider: Mapping[str, Any] = field(default_factory=dict)


class ModelClient(Protocol):
    """定义 Agent Loop 依赖的最小模型客户端接口。"""

    def call_model(
        self,
        state: AgentState,
        tools: Sequence[ToolSpec],
    ) -> ModelResponse:
        """根据当前 Agent State 调用模型并返回规范化响应。"""


class OpenAICompatibleModelClient:
    """调用 OpenAI-compatible chat completions 接口。"""

    def __init__(self, config: EnvConfig, timeout_seconds: int = 60) -> None:
        """绑定模型配置，密钥只保存在内存中。"""
        validation = config.validate()
        if not validation.ok:
            raise ValueError(validation.message())
        self.config = config
        self.timeout_seconds = timeout_seconds

    def call_model(
        self,
        state: AgentState,
        tools: Sequence[ToolSpec],
    ) -> ModelResponse:
        """调用模型接口并转换为内部消息块。"""
        payload = {
            "model": self.config.model_name,
            "messages": _messages_to_provider(state.messages),
        }
        provider_tools = _tools_to_provider(tools)
        if provider_tools:
            payload["tools"] = provider_tools
            payload["tool_choice"] = "auto"
        response = _post_json(self._endpoint(), self._headers(), payload, self.timeout_seconds)
        return parse_model_response(response)

    def _endpoint(self) -> str:
        """返回 chat completions 端点。"""
        base_url = self.config.model_base_url.rstrip("/")
        if base_url.endswith("/chat/completions"):
            return base_url
        return base_url + "/chat/completions"

    def _headers(self) -> dict[str, str]:
        """构造模型请求头，不对外暴露密钥。"""
        return {
            "Authorization": f"Bearer {self.config.model_api_key}",
            "Content-Type": "application/json",
        }


def call_model(
    client: ModelClient,
    state: AgentState,
    tools: Sequence[ToolSpec],
) -> ModelResponse:
    """提供函数式模型调用入口，便于测试替换。"""
    return client.call_model(state, tools)


def parse_model_response(payload: Mapping[str, Any]) -> ModelResponse:
    """解析 OpenAI-compatible 响应为内部响应结构。"""
    choices = payload.get("choices", [])
    if not choices:
        raise ValueError("model response missing choices")
    choice = choices[0]
    message = choice.get("message", {})
    blocks = _blocks_from_provider_message(message)
    return ModelResponse(
        assistant_blocks=blocks or (AgentContentBlock(type="text", text=""),),
        stop_reason=str(choice.get("finish_reason", "")),
        raw_provider=_sanitize_provider_payload(payload),
    )


def _messages_to_provider(messages: Sequence[AgentMessage]) -> list[dict[str, Any]]:
    """把 AgentMessage 序列转换为 provider messages。"""
    provider_messages: list[dict[str, Any]] = []
    for message in messages:
        provider_messages.extend(_message_to_provider(message))
    return provider_messages


def _message_to_provider(message: AgentMessage) -> list[dict[str, Any]]:
    """转换单条 AgentMessage。"""
    if _is_tool_result_message(message):
        return [_tool_result_to_provider(block) for block in message.content]
    item: dict[str, Any] = {"role": message.role, "content": _text_from_blocks(message.content)}
    tool_calls = [_tool_call_to_provider(block) for block in message.content if block.type == "tool_use"]
    if tool_calls:
        item["tool_calls"] = tool_calls
        if not item["content"]:
            item["content"] = None
    return [item]


def _is_tool_result_message(message: AgentMessage) -> bool:
    """判断消息是否只包含工具结果。"""
    return all(block.type == "tool_result" for block in message.content)


def _text_from_blocks(blocks: Sequence[AgentContentBlock]) -> str:
    """提取文本块内容。"""
    texts = [block.text for block in blocks if block.type == "text" and block.text]
    return "\n".join(texts)


def _tool_call_to_provider(block: AgentContentBlock) -> dict[str, Any]:
    """把 tool_use 块转换为 provider tool_call。"""
    return {
        "id": block.tool_use_id,
        "type": "function",
        "function": {
            "name": block.tool_name,
            "arguments": json.dumps(dict(block.tool_input), ensure_ascii=False),
        },
    }


def _tool_result_to_provider(block: AgentContentBlock) -> dict[str, Any]:
    """把 tool_result 块转换为 provider tool message。"""
    return {
        "role": "tool",
        "tool_call_id": block.tool_use_id,
        "name": block.tool_name,
        "content": block.content,
    }


def _tools_to_provider(tools: Sequence[ToolSpec]) -> list[dict[str, Any]]:
    """把工具定义转换为 provider 可见 schema。"""
    return [tool.to_contract() for tool in tools if tool.source == "native"]


def _blocks_from_provider_message(message: Mapping[str, Any]) -> tuple[AgentContentBlock, ...]:
    """把 provider assistant message 转为内部内容块。"""
    blocks: list[AgentContentBlock] = []
    content = message.get("content", "")
    if isinstance(content, str) and content:
        blocks.append(AgentContentBlock(type="text", text=content))
    for item in message.get("tool_calls", []) or []:
        blocks.append(_provider_tool_call_to_block(item))
    return tuple(blocks)


def _provider_tool_call_to_block(item: Mapping[str, Any]) -> AgentContentBlock:
    """把 provider tool_call 转换为 tool_use 块。"""
    function = item.get("function", {})
    arguments = function.get("arguments", "{}")
    return AgentContentBlock(
        type="tool_use",
        tool_use_id=str(item.get("id", "")),
        tool_name=str(function.get("name", "")),
        tool_input=_parse_arguments(arguments),
    )


def _parse_arguments(arguments: object) -> dict[str, Any]:
    """解析模型返回的工具参数 JSON。"""
    if isinstance(arguments, Mapping):
        return dict(arguments)
    if not isinstance(arguments, str) or not arguments:
        return {}
    parsed = json.loads(arguments)
    if not isinstance(parsed, Mapping):
        raise ValueError("tool call arguments must be a JSON object")
    return dict(parsed)


def _post_json(
    endpoint: str,
    headers: Mapping[str, str],
    payload: Mapping[str, Any],
    timeout_seconds: int,
) -> dict[str, Any]:
    """发送 JSON 请求并返回 JSON 响应。"""
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(endpoint, data=body, headers=dict(headers), method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            data = response.read().decode("utf-8")
    except urllib.error.URLError as exc:
        raise RuntimeError(f"model request failed: {exc}") from exc
    return json.loads(data)


def _sanitize_provider_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """保留 provider 原始响应结构，不写入任何本地密钥。"""
    return dict(payload)


def _self_test() -> None:
    """验证 provider 响应可转换为内部文本块。"""
    payload = {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}
    response = parse_model_response(payload)
    assert response.assistant_blocks[0].text == "ok"


if __name__ == "__main__":
    _self_test()
    print("dutyflow model client self-test passed")
