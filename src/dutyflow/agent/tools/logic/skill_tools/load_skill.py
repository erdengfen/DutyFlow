# 本文件实现 load_skill 工具的实际执行逻辑。

from __future__ import annotations

from dutyflow.agent.tools.contracts.skill_tools.load_skill_contract import LOAD_SKILL_TOOL_CONTRACT
from dutyflow.agent.tools.types import ToolCall, ToolResultEnvelope, error_envelope


class LoadSkillTool:
    """按名称加载完整技能正文的内部安全工具。"""

    name = "load_skill"
    contract = LOAD_SKILL_TOOL_CONTRACT
    is_concurrency_safe = True
    requires_approval = False
    timeout_seconds = 30.0
    max_retries = 0
    retry_policy = "none"
    idempotency = "read_only"
    degradation_mode = "none"
    fallback_tool_names = ()

    def handle(
        self,
        tool_call: ToolCall,
        tool_use_context,
    ) -> ToolResultEnvelope:
        """从共享 SkillRegistry 读取完整技能正文。"""
        registry = tool_use_context.skill_registry
        if registry is None:
            return error_envelope(
                tool_call,
                "skill_registry_unavailable",
                "skill registry is not configured",
            )
        name = str(tool_call.tool_input["name"])
        try:
            return ToolResultEnvelope(
                tool_call.tool_use_id,
                tool_call.tool_name,
                True,
                registry.load_full_text(name),
            )
        except KeyError:
            return error_envelope(
                tool_call,
                "skill_not_found",
                f"skill not found: {name}",
            )


def _self_test() -> None:
    """验证 LoadSkillTool 名称与 contract 一致。"""
    assert LoadSkillTool.name == LoadSkillTool.contract["function"]["name"]


if __name__ == "__main__":
    _self_test()
    print("dutyflow agent load_skill logic self-test passed")
