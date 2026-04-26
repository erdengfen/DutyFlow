# 本文件定义 lookup_source_context 工具的模型可见 contract。

LOOKUP_SOURCE_CONTEXT_TOOL_CONTRACT = {
    "type": "function",
    "function": {
        "name": "lookup_source_context",
        "description": "按 source_id、飞书来源 ID、来源类型和显示名查询来源上下文。",
        "parameters": {
            "type": "object",
            "properties": {
                "source_id": {"type": "string", "description": "本地来源稳定 ID。"},
                "source_type": {"type": "string", "description": "来源类型，例如 chat 或 doc。"},
                "feishu_id": {"type": "string", "description": "飞书侧来源资源 ID。"},
                "display_name": {"type": "string", "description": "来源显示名。"},
            },
            "required": [],
        },
    },
}


def _self_test() -> None:
    """验证工具名稳定。"""
    assert LOOKUP_SOURCE_CONTEXT_TOOL_CONTRACT["function"]["name"] == "lookup_source_context"


if __name__ == "__main__":
    _self_test()
    print("dutyflow lookup_source_context contract self-test passed")
