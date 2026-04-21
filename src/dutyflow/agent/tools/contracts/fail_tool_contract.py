# 本文件定义 fail_tool 工具的模型可见 contract 结构。

FAIL_TOOL_CONTRACT = {
    "type": "function",
    "function": {
        "name": "fail_tool",
        "description": "返回稳定失败，用于验证工具异常封装链路。",
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "可选失败原因说明。",
                }
            },
            "required": [],
        },
    },
}


def _self_test() -> None:
    """验证 fail_tool contract 名称稳定。"""
    assert FAIL_TOOL_CONTRACT["function"]["name"] == "fail_tool"


if __name__ == "__main__":
    _self_test()
    print("dutyflow agent fail_tool contract self-test passed")
