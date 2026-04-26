# 本文件定义 close_cli_session 工具的模型可见 contract 结构。

CLOSE_CLI_SESSION_TOOL_CONTRACT = {
    "type": "function",
    "function": {
        "name": "close_cli_session",
        "description": "关闭持久 bash shell 会话并清理资源。",
        "parameters": {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "要关闭的 CLI shell 会话 ID。",
                }
            },
            "required": ["session_id"],
        },
    },
}


def _self_test() -> None:
    """验证 close_cli_session contract 名称稳定。"""
    assert CLOSE_CLI_SESSION_TOOL_CONTRACT["function"]["name"] == "close_cli_session"


if __name__ == "__main__":
    _self_test()
    print("dutyflow agent close_cli_session contract self-test passed")
