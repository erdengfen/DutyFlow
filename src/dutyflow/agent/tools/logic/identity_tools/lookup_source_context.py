# 本文件实现 lookup_source_context 工具的只读查询逻辑。

from __future__ import annotations

from pathlib import Path
import sys

SRC_ROOT = Path(__file__).resolve().parents[5]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dutyflow.agent.tools.contracts.identity_tools.lookup_source_context_contract import LOOKUP_SOURCE_CONTEXT_TOOL_CONTRACT
from dutyflow.agent.tools.types import ToolCall, ToolResultEnvelope, error_envelope
from dutyflow.identity.source_context import SourceContextResolver


class LookupSourceContextTool:
    """查询来源上下文的只读内部工具。"""

    name = "lookup_source_context"
    contract = LOOKUP_SOURCE_CONTEXT_TOOL_CONTRACT
    is_concurrency_safe = True
    requires_approval = False
    timeout_seconds = 30.0
    max_retries = 0
    retry_policy = "none"
    idempotency = "read_only"
    degradation_mode = "none"
    fallback_tool_names = ()

    def handle(self, tool_call: ToolCall, tool_use_context) -> ToolResultEnvelope:
        """调用来源解析器返回来源查询结果。"""
        if not _has_source_selector(dict(tool_call.tool_input)):
            return error_envelope(tool_call, "invalid_source_lookup_input", "at least one source selector is required")
        resolver = SourceContextResolver(tool_use_context.cwd)
        return ToolResultEnvelope(
            tool_call.tool_use_id,
            tool_call.tool_name,
            True,
            resolver.resolve_source_json(dict(tool_call.tool_input)),
        )


def _has_source_selector(tool_input: dict[str, object]) -> bool:
    """判断是否提供了至少一个来源查询条件。"""
    return any(isinstance(tool_input.get(key), str) and tool_input.get(key, "").strip() for key in ("source_id", "source_type", "feishu_id", "display_name"))


def _self_test() -> None:
    """验证工具名与 contract 一致。"""
    assert LookupSourceContextTool.name == LookupSourceContextTool.contract["function"]["name"]


if __name__ == "__main__":
    _self_test()
    print("dutyflow lookup_source_context logic self-test passed")
