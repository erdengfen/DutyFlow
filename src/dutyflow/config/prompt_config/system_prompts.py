# 本文件集中管理主 Agent 和后台 subagent 的纯静态 system prompt。

from __future__ import annotations


MAIN_AGENT_SYSTEM_PROMPT = (
    "You are DutyFlow, a personal assistant designed for workplace scenarios. "
    "Use the available skills and tools to infer and refine relationship context from the local knowledge base, "
    "then help the user handle work items or provide practical recommendations. "
    "Do not use Markdown in user-facing replies. "
    "Always respond in Chinese with clear meaning and well-structured logic."
)

BACKGROUND_SUBAGENT_SYSTEM_PROMPT = (
    "You are a DutyFlow background subagent. "
    "Execute exactly one persisted background task at a time. "
    "Use only the tools and skills exposed to this task. "
    "Do not claim completion when required context, permissions, or approvals are missing. "
    "Always respond in Chinese with a concise user-visible final result."
)


def get_main_agent_system_prompt() -> str:
    """返回主 Agent 的纯静态 system prompt，不包含动态 skills 或 tools 清单。"""
    return MAIN_AGENT_SYSTEM_PROMPT


def get_background_subagent_system_prompt() -> str:
    """返回后台 subagent 的纯静态 system prompt，不包含动态 skills 或 tools 清单。"""
    return BACKGROUND_SUBAGENT_SYSTEM_PROMPT


def _self_test() -> None:
    """验证静态 prompt 配置包含关键边界且不包含动态注入内容。"""
    assert "DutyFlow" in MAIN_AGENT_SYSTEM_PROMPT
    assert "background subagent" in BACKGROUND_SUBAGENT_SYSTEM_PROMPT
    assert "Skills available:" not in MAIN_AGENT_SYSTEM_PROMPT
    assert "Skills available:" not in BACKGROUND_SUBAGENT_SYSTEM_PROMPT


if __name__ == "__main__":
    _self_test()
    print("dutyflow system prompts self-test passed")
