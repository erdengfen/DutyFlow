# 本文件实现 update_contact_knowledge 工具的受控写入逻辑。

from __future__ import annotations

import json

from dutyflow.agent.tools.contracts.contact_tools.update_contact_knowledge_contract import (
    UPDATE_CONTACT_KNOWLEDGE_TOOL_CONTRACT,
)
from dutyflow.agent.tools.types import ToolCall, ToolResultEnvelope, error_envelope
from dutyflow.knowledge.contact_knowledge import ContactKnowledgeService


class UpdateContactKnowledgeTool:
    """更新联系人补充知识记录的敏感内部工具。"""

    name = "update_contact_knowledge"
    contract = UPDATE_CONTACT_KNOWLEDGE_TOOL_CONTRACT
    is_concurrency_safe = False
    requires_approval = True
    timeout_seconds = 30.0
    max_retries = 0
    retry_policy = "none"
    idempotency = "unsafe"
    degradation_mode = "escalate"
    fallback_tool_names = ()

    def handle(self, tool_call: ToolCall, tool_use_context) -> ToolResultEnvelope:
        """更新已有联系人知识记录。"""
        service = ContactKnowledgeService(tool_use_context.cwd)
        try:
            payload = service.update_record(dict(tool_call.tool_input))
        except ValueError as exc:
            return error_envelope(tool_call, "invalid_contact_knowledge_input", str(exc))
        except KeyError:
            note_id = str(tool_call.tool_input["note_id"]).strip()
            return error_envelope(tool_call, "contact_knowledge_not_found", f"contact knowledge not found: {note_id}")
        return ToolResultEnvelope(
            tool_call.tool_use_id,
            tool_call.tool_name,
            True,
            json.dumps(payload, ensure_ascii=False),
            attachments=(payload["file_path"],),
        )


def _self_test() -> None:
    """验证工具名与 contract 一致。"""
    assert UpdateContactKnowledgeTool.name == UpdateContactKnowledgeTool.contract["function"]["name"]


if __name__ == "__main__":
    _self_test()
    print("dutyflow update_contact_knowledge logic self-test passed")
