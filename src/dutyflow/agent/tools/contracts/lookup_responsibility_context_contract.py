# 本文件定义 lookup_responsibility_context 工具的模型可见 contract。

LOOKUP_RESPONSIBILITY_CONTEXT_TOOL_CONTRACT = {
    "type": "function",
    "function": {
        "name": "lookup_responsibility_context",
        "description": "结合联系人、来源和事项类型查询责任上下文，并返回裁剪后的责任片段。",
        "parameters": {
            "type": "object",
            "properties": {
                "contact_id": {"type": "string", "description": "联系人稳定 ID。"},
                "source_id": {"type": "string", "description": "来源稳定 ID。"},
                "matter_type": {"type": "string", "description": "事项类型，例如 项目排期 或 缺陷修复。"},
                "task_id": {"type": "string", "description": "任务稳定 ID，当前仅保留为上下文透传字段。"},
            },
            "required": [],
        },
    },
}


def _self_test() -> None:
    """验证工具名稳定。"""
    assert LOOKUP_RESPONSIBILITY_CONTEXT_TOOL_CONTRACT["function"]["name"] == "lookup_responsibility_context"


if __name__ == "__main__":
    _self_test()
    print("dutyflow lookup_responsibility_context contract self-test passed")
