# 本文件实现 lookup_contact_identity 工具的只读查询逻辑。

from __future__ import annotations

from pathlib import Path
import sys

SRC_ROOT = Path(__file__).resolve().parents[4]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dutyflow.agent.tools.contracts.lookup_contact_identity_contract import LOOKUP_CONTACT_IDENTITY_TOOL_CONTRACT
from dutyflow.agent.tools.types import ToolCall, ToolResultEnvelope, error_envelope
from dutyflow.identity.contact_resolver import ContactResolver


class LookupContactIdentityTool:
    """查询联系人身份的只读内部工具。"""

    name = "lookup_contact_identity"
    contract = LOOKUP_CONTACT_IDENTITY_TOOL_CONTRACT
    is_concurrency_safe = True
    requires_approval = False
    timeout_seconds = 30.0
    max_retries = 0
    retry_policy = "none"
    idempotency = "read_only"
    degradation_mode = "none"
    fallback_tool_names = ()

    def handle(self, tool_call: ToolCall, tool_use_context) -> ToolResultEnvelope:
        """调用联系人解析器返回身份查询结果。"""
        if not _has_identity_selector(dict(tool_call.tool_input)):
            return error_envelope(tool_call, "invalid_identity_lookup_input", "at least one identity selector is required")
        resolver = ContactResolver(tool_use_context.cwd)
        return ToolResultEnvelope(
            tool_call.tool_use_id,
            tool_call.tool_name,
            True,
            resolver.resolve_contact_json(dict(tool_call.tool_input)),
        )


def _has_identity_selector(tool_input: dict[str, object]) -> bool:
    """判断是否提供了至少一个身份查询条件。"""
    return any(isinstance(tool_input.get(key), str) and tool_input.get(key, "").strip() for key in ("contact_id", "feishu_user_id", "feishu_open_id", "name", "alias"))


def _self_test() -> None:
    """验证工具名与 contract 一致。"""
    assert LookupContactIdentityTool.name == LookupContactIdentityTool.contract["function"]["name"]


if __name__ == "__main__":
    _self_test()
    print("dutyflow lookup_contact_identity logic self-test passed")
