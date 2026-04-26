# 本文件定义 get_contact_knowledge_detail 工具的模型可见 contract。

GET_CONTACT_KNOWLEDGE_DETAIL_TOOL_CONTRACT = {
    "type": "function",
    "function": {
        "name": "get_contact_knowledge_detail",
        "description": "按 note_id 读取单条联系人补充知识的指定 detail section，不返回整份 Markdown 原文。",
        "parameters": {
            "type": "object",
            "properties": {
                "note_id": {"type": "string", "description": "联系人知识记录 ID，例如 ckn_001。"},
            },
            "required": ["note_id"],
        },
    },
}


def _self_test() -> None:
    """验证工具名稳定。"""
    assert GET_CONTACT_KNOWLEDGE_DETAIL_TOOL_CONTRACT["function"]["name"] == "get_contact_knowledge_detail"


if __name__ == "__main__":
    _self_test()
    print("dutyflow get_contact_knowledge_detail contract self-test passed")
