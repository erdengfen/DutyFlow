# 本文件定义 search_contact_knowledge_headers 工具的模型可见 contract。

SEARCH_CONTACT_KNOWLEDGE_HEADERS_TOOL_CONTRACT = {
    "type": "function",
    "function": {
        "name": "search_contact_knowledge_headers",
        "description": "搜索联系人补充知识记录的轻量 header 信息，返回 note_id、topic、summary 等字段，不直接展开正文全文。",
        "parameters": {
            "type": "object",
            "properties": {
                "contact_id": {"type": "string", "description": "联系人稳定 ID。"},
                "name": {"type": "string", "description": "联系人显示名或别名。"},
                "topic": {"type": "string", "description": "知识主题，如 working_preference。"},
                "keywords": {"type": "string", "description": "英文逗号分隔的关键词。"},
                "query": {"type": "string", "description": "在轻量文本上执行的包含匹配词。"},
                "status": {"type": "string", "description": "记录状态，如 active。"},
            },
            "required": [],
        },
    },
}


def _self_test() -> None:
    """验证工具名稳定。"""
    assert SEARCH_CONTACT_KNOWLEDGE_HEADERS_TOOL_CONTRACT["function"]["name"] == "search_contact_knowledge_headers"


if __name__ == "__main__":
    _self_test()
    print("dutyflow search_contact_knowledge_headers contract self-test passed")
