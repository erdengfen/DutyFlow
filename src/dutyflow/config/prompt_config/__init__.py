# 本文件统一暴露 DutyFlow 静态 system prompt 配置。

from dutyflow.config.prompt_config.system_prompts import (
    BACKGROUND_SUBAGENT_SYSTEM_PROMPT,
    MAIN_AGENT_SYSTEM_PROMPT,
    get_background_subagent_system_prompt,
    get_main_agent_system_prompt,
)

__all__ = [
    "BACKGROUND_SUBAGENT_SYSTEM_PROMPT",
    "MAIN_AGENT_SYSTEM_PROMPT",
    "get_background_subagent_system_prompt",
    "get_main_agent_system_prompt",
]


def _self_test() -> None:
    """验证 prompt_config 包可稳定导出静态 prompt。"""
    assert get_main_agent_system_prompt() == MAIN_AGENT_SYSTEM_PROMPT
    assert get_background_subagent_system_prompt() == BACKGROUND_SUBAGENT_SYSTEM_PROMPT


if __name__ == "__main__":
    _self_test()
    print("dutyflow prompt config package self-test passed")
