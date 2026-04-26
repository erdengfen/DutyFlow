# 本文件定义 open_cli_session 工具的模型可见 contract 结构。

OPEN_CLI_SESSION_TOOL_CONTRACT = {
    "type": "function",
    "function": {
        "name": "open_cli_session",
        "description": "在 Linux / WSL 下创建持久 bash shell 会话。",
        "parameters": {
            "type": "object",
            "properties": {
                "cwd": {
                    "type": "string",
                    "description": "初始工作目录；为空时默认使用当前工作区根目录。",
                },
                "timeout": {
                    "type": "number",
                    "description": "创建 shell 会话的超时秒数。",
                },
                "shell_type": {
                    "type": "string",
                    "description": "当前批次只支持 bash；该字段仅作兼容预留。",
                },
            },
            "required": ["cwd", "timeout"],
        },
    },
}


def _self_test() -> None:
    """验证 open_cli_session contract 名称稳定。"""
    assert OPEN_CLI_SESSION_TOOL_CONTRACT["function"]["name"] == "open_cli_session"


if __name__ == "__main__":
    _self_test()
    print("dutyflow agent open_cli_session contract self-test passed")
