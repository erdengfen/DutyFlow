# 本文件定义 echo_text 工具的模型可见 contract 结构。

ECHO_TEXT_TOOL_CONTRACT = {
    "type": "function",
    "function": {
        "name": "echo_text",
        "description": "返回输入 text，用于验证工具调用链路。",
        "parameters": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "要原样返回的文本内容。",
                }
            },
            "required": ["text"],
        },
    },
}


def _self_test() -> None:
    """验证 echo_text contract 名称稳定。"""
    assert ECHO_TEXT_TOOL_CONTRACT["function"]["name"] == "echo_text"


if __name__ == "__main__":
    _self_test()
    print("dutyflow agent echo_text contract self-test passed")
