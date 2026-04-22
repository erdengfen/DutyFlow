# 本文件定义 sensitive_echo_text 工具的模型可见 contract 结构。

SENSITIVE_ECHO_TEXT_TOOL_CONTRACT = {
    "type": "function",
    "function": {
        "name": "sensitive_echo_text",
        "description": "模拟需要人工审批的敏感动作，审批通过后返回输入 text，用于验证权限链路。",
        "parameters": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "审批通过后要返回的文本内容。",
                }
            },
            "required": ["text"],
        },
    },
}


def _self_test() -> None:
    """验证 sensitive_echo_text contract 名称稳定。"""
    assert SENSITIVE_ECHO_TEXT_TOOL_CONTRACT["function"]["name"] == "sensitive_echo_text"


if __name__ == "__main__":
    _self_test()
    print("dutyflow agent sensitive_echo_text contract self-test passed")
