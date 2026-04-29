# 本文件实现 resume_after_approval 工具的审批恢复状态流转逻辑。

from __future__ import annotations

import json

from dutyflow.agent.tools.contracts.approval_tools.resume_after_approval_contract import (
    RESUME_AFTER_APPROVAL_TOOL_CONTRACT,
)
from dutyflow.agent.tools.types import ToolCall, ToolResultEnvelope, error_envelope
from dutyflow.approval.approval_resume_intake import ApprovalResumeIntakeService


class ResumeAfterApprovalTool:
    """根据用户审批结果完成审批记录并更新后台任务状态的高层工具。"""

    name = "resume_after_approval"
    contract = RESUME_AFTER_APPROVAL_TOOL_CONTRACT
    is_concurrency_safe = False
    requires_approval = False
    timeout_seconds = 30.0
    max_retries = 0
    retry_policy = "none"
    idempotency = "read_only"
    degradation_mode = "none"
    fallback_tool_names = ()

    def handle(self, tool_call: ToolCall, tool_use_context) -> ToolResultEnvelope:
        """完成审批决策落盘，并把任务切换到对应状态。"""
        service = ApprovalResumeIntakeService(tool_use_context.cwd)
        try:
            payload = service.resume_after_decision(dict(tool_call.tool_input)).to_payload()
        except ValueError as exc:
            return error_envelope(tool_call, "invalid_approval_resume_input", str(exc))
        return ToolResultEnvelope(
            tool_call.tool_use_id,
            tool_call.tool_name,
            True,
            json.dumps(payload, ensure_ascii=False),
            attachments=(
                str(payload["approval_file"]),
                str(payload["task_file"]),
            ),
        )


def _self_test() -> None:
    """验证工具名与 contract 一致。"""
    assert ResumeAfterApprovalTool.name == ResumeAfterApprovalTool.contract["function"]["name"]


if __name__ == "__main__":
    _self_test()
    print("dutyflow resume_after_approval logic self-test passed")
