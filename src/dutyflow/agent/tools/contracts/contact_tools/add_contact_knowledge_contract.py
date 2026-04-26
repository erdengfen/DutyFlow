# 本文件定义 add_contact_knowledge 工具的模型可见 contract。

ADD_CONTACT_KNOWLEDGE_TOOL_CONTRACT = {
    "type": "function",
    "function": {
        "name": "add_contact_knowledge",
        "description": "新增一条联系人补充知识记录，写入 data/knowledge/contacts 下的结构化 Markdown 文件。",
        "parameters": {
            "type": "object",
            "properties": {
                "contact_id": {"type": "string", "description": "联系人稳定 ID。"},
                "topic": {"type": "string", "description": "知识主题。"},
                "keywords": {"type": "string", "description": "英文逗号分隔的关键词。"},
                "summary": {"type": "string", "description": "一句话知识摘要。"},
                "structured_facts_markdown": {"type": "string", "description": "Structured Facts section 的 Markdown 文本。"},
                "decision_value": {"type": "string", "description": "Decision Value section 的正文。"},
                "source_refs": {"type": "string", "description": "英文逗号分隔的来源引用。"},
            },
            "required": ["contact_id", "topic", "summary"],
        },
    },
}


def _self_test() -> None:
    """验证工具名稳定。"""
    assert ADD_CONTACT_KNOWLEDGE_TOOL_CONTRACT["function"]["name"] == "add_contact_knowledge"


if __name__ == "__main__":
    _self_test()
    print("dutyflow add_contact_knowledge contract self-test passed")
