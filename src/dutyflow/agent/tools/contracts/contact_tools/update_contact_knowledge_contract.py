# 本文件定义 update_contact_knowledge 工具的模型可见 contract。

UPDATE_CONTACT_KNOWLEDGE_TOOL_CONTRACT = {
    "type": "function",
    "function": {
        "name": "update_contact_knowledge",
        "description": "按 note_id 更新联系人补充知识记录，并追加 Change Log。",
        "parameters": {
            "type": "object",
            "properties": {
                "note_id": {"type": "string", "description": "联系人知识记录 ID。"},
                "summary": {"type": "string", "description": "新的 Summary section。"},
                "structured_facts_markdown": {"type": "string", "description": "新的 Structured Facts section。"},
                "decision_value": {"type": "string", "description": "新的 Decision Value section。"},
                "status": {"type": "string", "description": "新的记录状态。"},
                "confidence": {"type": "string", "description": "新的置信度。"},
                "change_note": {"type": "string", "description": "写入 Change Log 的备注。"},
            },
            "required": ["note_id"],
        },
    },
}


def _self_test() -> None:
    """验证工具名稳定。"""
    assert UPDATE_CONTACT_KNOWLEDGE_TOOL_CONTRACT["function"]["name"] == "update_contact_knowledge"


if __name__ == "__main__":
    _self_test()
    print("dutyflow update_contact_knowledge contract self-test passed")
