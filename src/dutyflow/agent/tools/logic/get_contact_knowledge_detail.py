# 本文件实现 get_contact_knowledge_detail 工具的只读查询逻辑。

from __future__ import annotations

from dutyflow.agent.tools.contracts.get_contact_knowledge_detail_contract import (
    GET_CONTACT_KNOWLEDGE_DETAIL_TOOL_CONTRACT,
)
from dutyflow.agent.tools.types import ToolCall, ToolResultEnvelope, error_envelope
from dutyflow.knowledge.contact_knowledge import ContactKnowledgeService


class GetContactKnowledgeDetailTool:
    """按 note_id 读取联系人知识 detail 的只读内部工具。"""

    name = "get_contact_knowledge_detail"
    contract = GET_CONTACT_KNOWLEDGE_DETAIL_TOOL_CONTRACT
    is_concurrency_safe = True
    requires_approval = False
    timeout_seconds = 30.0
    max_retries = 0
    retry_policy = "none"
    idempotency = "read_only"
    degradation_mode = "none"
    fallback_tool_names = ()

    def handle(self, tool_call: ToolCall, tool_use_context) -> ToolResultEnvelope:
        """返回单条联系人知识记录的 detail JSON。"""
        service = ContactKnowledgeService(tool_use_context.cwd)
        note_id = str(tool_call.tool_input["note_id"]).strip()
        try:
            return ToolResultEnvelope(
                tool_call.tool_use_id,
                tool_call.tool_name,
                True,
                service.get_detail_json(note_id),
            )
        except KeyError:
            return error_envelope(tool_call, "contact_knowledge_not_found", f"contact knowledge not found: {note_id}")


def _self_test() -> None:
    """验证工具名与 contract 一致。"""
    assert GetContactKnowledgeDetailTool.name == GetContactKnowledgeDetailTool.contract["function"]["name"]


if __name__ == "__main__":
    _self_test()
    print("dutyflow get_contact_knowledge_detail logic self-test passed")
