# 本文件定义 lookup_contact_identity 工具的模型可见 contract。

LOOKUP_CONTACT_IDENTITY_TOOL_CONTRACT = {
    "type": "function",
    "function": {
        "name": "lookup_contact_identity",
        "description": "按 contact_id、飞书 ID、姓名、别名和部门查询联系人身份，并返回裁剪后的身份片段。",
        "parameters": {
            "type": "object",
            "properties": {
                "contact_id": {"type": "string", "description": "联系人稳定 ID。"},
                "feishu_user_id": {"type": "string", "description": "飞书 user_id。"},
                "feishu_open_id": {"type": "string", "description": "飞书 open_id。"},
                "name": {"type": "string", "description": "联系人姓名。"},
                "alias": {"type": "string", "description": "联系人别名。"},
                "department": {"type": "string", "description": "部门名称。"},
                "source_id": {"type": "string", "description": "来源 ID，预留用于后续补充判断。"},
            },
            "required": [],
        },
    },
}


def _self_test() -> None:
    """验证工具名稳定。"""
    assert LOOKUP_CONTACT_IDENTITY_TOOL_CONTRACT["function"]["name"] == "lookup_contact_identity"


if __name__ == "__main__":
    _self_test()
    print("dutyflow lookup_contact_identity contract self-test passed")
