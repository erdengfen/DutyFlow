# 本文件定义 create_skill 工具的模型可见 contract 结构。

CREATE_SKILL_TOOL_CONTRACT = {
    "type": "function",
    "function": {
        "name": "create_skill",
        "description": "创建新的 skills/<skill_name>/SKILL.md，用于受控扩展 agent 的本地 skills。",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "新 skill 名称，只允许小写字母、数字、下划线和连字符。",
                },
                "description": {
                    "type": "string",
                    "description": "写入 frontmatter 的简短 skill 描述。",
                },
                "body": {
                    "type": "string",
                    "description": "写入 SKILL.md 的正文，不包含 frontmatter。",
                },
            },
            "required": ["name", "description", "body"],
        },
    },
}


def _self_test() -> None:
    """验证 create_skill contract 名称稳定。"""
    assert CREATE_SKILL_TOOL_CONTRACT["function"]["name"] == "create_skill"


if __name__ == "__main__":
    _self_test()
    print("dutyflow agent create_skill contract self-test passed")
