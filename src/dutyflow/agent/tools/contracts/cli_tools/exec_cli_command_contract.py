# 本文件定义 exec_cli_command 工具的模型可见 contract 结构。

EXEC_CLI_COMMAND_TOOL_CONTRACT = {
    "type": "function",
    "function": {
        "name": "exec_cli_command",
        "description": "在已打开的 bash session 中执行一条命令；权限层会按命令内容判断是否需要审批。",
        "parameters": {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "目标 CLI shell 会话 ID。",
                },
                "command": {
                    "type": "string",
                    "description": "要执行的一条单行 bash 命令。",
                },
                "timeout": {
                    "type": "number",
                    "description": "本次命令执行的超时秒数。",
                },
            },
            "required": ["session_id", "command", "timeout"],
        },
    },
}


def _self_test() -> None:
    """验证 exec_cli_command contract 名称稳定。"""
    assert EXEC_CLI_COMMAND_TOOL_CONTRACT["function"]["name"] == "exec_cli_command"


if __name__ == "__main__":
    _self_test()
    print("dutyflow agent exec_cli_command contract self-test passed")
