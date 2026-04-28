# 本文件实现 schedule_background_task 工具的定时后台任务创建逻辑。

from __future__ import annotations

import json

from dutyflow.agent.skills import SkillRegistry
from dutyflow.agent.tools.contracts.task_tools.schedule_background_task_contract import (
    SCHEDULE_BACKGROUND_TASK_TOOL_CONTRACT,
)
from dutyflow.agent.tools.types import ToolCall, ToolResultEnvelope, error_envelope
from dutyflow.tasks.background_task_intake import BackgroundTaskIntakeService


class ScheduleBackgroundTaskTool:
    """创建未来定时执行后台任务的高层入口工具。"""

    name = "schedule_background_task"
    contract = SCHEDULE_BACKGROUND_TASK_TOOL_CONTRACT
    is_concurrency_safe = False
    requires_approval = False
    timeout_seconds = 30.0
    max_retries = 0
    retry_policy = "none"
    idempotency = "read_only"
    degradation_mode = "none"
    fallback_tool_names = ()

    def handle(self, tool_call: ToolCall, tool_use_context) -> ToolResultEnvelope:
        """校验任务意图和调度时间后创建定时后台任务记录。"""
        service = _build_service(tool_use_context)
        try:
            payload = service.create_scheduled_task(tool_call.tool_input).to_payload()
        except ValueError as exc:
            return error_envelope(tool_call, "invalid_background_task_input", str(exc))
        return ToolResultEnvelope(
            tool_call.tool_use_id,
            tool_call.tool_name,
            True,
            json.dumps(payload, ensure_ascii=False),
            attachments=(str(payload["task_file"]),),
        )


def _build_service(tool_use_context) -> BackgroundTaskIntakeService:
    """根据工具上下文构造后台任务入口服务。"""
    skill_registry = tool_use_context.skill_registry or SkillRegistry(tool_use_context.cwd / "skills")
    return BackgroundTaskIntakeService(tool_use_context.cwd, tool_use_context.registry, skill_registry)


def _self_test() -> None:
    """验证工具名与 contract 一致。"""
    assert ScheduleBackgroundTaskTool.name == ScheduleBackgroundTaskTool.contract["function"]["name"]


if __name__ == "__main__":
    _self_test()
    print("dutyflow schedule_background_task logic self-test passed")
