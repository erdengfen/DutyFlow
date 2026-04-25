# 本文件实现 search_contact_knowledge_headers 工具的只读查询逻辑。

from __future__ import annotations

from dutyflow.agent.tools.contracts.search_contact_knowledge_headers_contract import (
    SEARCH_CONTACT_KNOWLEDGE_HEADERS_TOOL_CONTRACT,
)
from dutyflow.agent.tools.types import ToolCall, ToolResultEnvelope
from dutyflow.knowledge.contact_knowledge import ContactKnowledgeService


class SearchContactKnowledgeHeadersTool:
    """查询联系人知识 header 的只读内部工具。"""

    name = "search_contact_knowledge_headers"
    contract = SEARCH_CONTACT_KNOWLEDGE_HEADERS_TOOL_CONTRACT
    is_concurrency_safe = True
    requires_approval = False
    timeout_seconds = 30.0
    max_retries = 0
    retry_policy = "none"
    idempotency = "read_only"
    degradation_mode = "none"
    fallback_tool_names = ()

    def handle(self, tool_call: ToolCall, tool_use_context) -> ToolResultEnvelope:
        """调用联系人知识服务返回轻量 header 结果。"""
        service = ContactKnowledgeService(tool_use_context.cwd)
        return ToolResultEnvelope(
            tool_call.tool_use_id,
            tool_call.tool_name,
            True,
            service.search_headers_json(dict(tool_call.tool_input)),
        )


def _self_test() -> None:
    """验证工具名与 contract 一致。"""
    assert SearchContactKnowledgeHeadersTool.name == SearchContactKnowledgeHeadersTool.contract["function"]["name"]


if __name__ == "__main__":
    _self_test()
    print("dutyflow search_contact_knowledge_headers logic self-test passed")
