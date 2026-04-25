# 本文件实现 add_contact_knowledge 工具的受控写入逻辑。

from __future__ import annotations

import json

from dutyflow.agent.tools.contracts.add_contact_knowledge_contract import ADD_CONTACT_KNOWLEDGE_TOOL_CONTRACT
from dutyflow.agent.tools.types import ToolCall, ToolResultEnvelope, error_envelope
from dutyflow.knowledge.contact_knowledge import ContactKnowledgeService


class AddContactKnowledgeTool:
    """新增联系人补充知识记录的敏感内部工具。"""

    name = "add_contact_knowledge"
    contract = ADD_CONTACT_KNOWLEDGE_TOOL_CONTRACT
    is_concurrency_safe = False
    requires_approval = True
    timeout_seconds = 30.0
    max_retries = 0
    retry_policy = "none"
    idempotency = "unsafe"
    degradation_mode = "escalate"
    fallback_tool_names = ()

    def handle(self, tool_call: ToolCall, tool_use_context) -> ToolResultEnvelope:
        """创建新的联系人知识记录。"""
        service = ContactKnowledgeService(tool_use_context.cwd)
        try:
            payload = service.add_record(dict(tool_call.tool_input))
        except ValueError as exc:
            return error_envelope(tool_call, "invalid_contact_knowledge_input", str(exc))
        return ToolResultEnvelope(
            tool_call.tool_use_id,
            tool_call.tool_name,
            True,
            json.dumps(payload, ensure_ascii=False),
            attachments=(payload["file_path"],),
        )


def _self_test() -> None:
    """验证工具名与 contract 一致。"""
    assert AddContactKnowledgeTool.name == AddContactKnowledgeTool.contract["function"]["name"]


if __name__ == "__main__":
    _self_test()
    print("dutyflow add_contact_knowledge logic self-test passed")
