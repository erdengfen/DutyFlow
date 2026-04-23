# 本文件定义 load_skill 工具的模型可见 contract 结构。

LOAD_SKILL_TOOL_CONTRACT = {
    "type": "function",
    "function": {
        "name": "load_skill",
        "description": "按技能名称加载完整 SKILL.md 正文，用于按需把技能全文注入上下文。",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "要加载的技能名称，对应 skill frontmatter 中的 name。",
                }
            },
            "required": ["name"],
        },
    },
}


def _self_test() -> None:
    """验证 load_skill contract 名称稳定。"""
    assert LOAD_SKILL_TOOL_CONTRACT["function"]["name"] == "load_skill"


if __name__ == "__main__":
    _self_test()
    print("dutyflow agent load_skill contract self-test passed")
