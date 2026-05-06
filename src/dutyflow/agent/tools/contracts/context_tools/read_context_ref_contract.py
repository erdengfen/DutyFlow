# 本文件定义 read_context_ref 工具的模型可见 contract。

READ_CONTEXT_REF_TOOL_CONTRACT = {
    "type": "function",
    "function": {
        "name": "read_context_ref",
        "description": (
            "按稳定引用读取 DutyFlow 本地已落盘上下文摘要。"
            "支持 perception、ambient_context、evidence、task、approval、report。"
            "该工具只读，不访问飞书 API，不读取项目外文件，长正文只返回预览和 detail_file。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "ref_type": {
                    "type": "string",
                    "description": "引用类型：perception、ambient_context、evidence、task、approval、report。",
                },
                "ref_id": {
                    "type": "string",
                    "description": "稳定引用 ID，例如 per_xxx、gm_xxx、evid_xxx、task_xxx、approval_xxx。",
                },
            },
            "required": ["ref_type", "ref_id"],
        },
    },
}


def _self_test() -> None:
    """验证工具名稳定。"""
    assert READ_CONTEXT_REF_TOOL_CONTRACT["function"]["name"] == "read_context_ref"


if __name__ == "__main__":
    _self_test()
    print("dutyflow read_context_ref contract self-test passed")
