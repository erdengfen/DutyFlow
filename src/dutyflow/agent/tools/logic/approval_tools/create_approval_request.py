# 本文件实现 create_approval_request 工具的审批创建逻辑。

from __future__ import annotations

import json
from pathlib import Path
import sys

if __package__ in {None, ""}:
    _SRC_ROOT = Path(__file__).resolve().parents[5]
    if str(_SRC_ROOT) not in sys.path:
        sys.path.insert(0, str(_SRC_ROOT))

from dutyflow.agent.tools.contracts.approval_tools.create_approval_request_contract import (
    CREATE_APPROVAL_REQUEST_TOOL_CONTRACT,
)
from dutyflow.agent.tools.types import ToolCall, ToolResultEnvelope, error_envelope
from dutyflow.approval.approval_flow import ApprovalStore
from dutyflow.approval.approval_request_intake import ApprovalRequestIntakeService
from dutyflow.config.env import load_env_config
from dutyflow.feedback.gateway import FeedbackGateway


class CreateApprovalRequestTool:
    """为后台任务创建审批请求并更新任务等待态的高层工具。"""

    name = "create_approval_request"
    contract = CREATE_APPROVAL_REQUEST_TOOL_CONTRACT
    is_concurrency_safe = False
    requires_approval = False
    timeout_seconds = 30.0
    max_retries = 0
    retry_policy = "none"
    idempotency = "read_only"
    degradation_mode = "none"
    fallback_tool_names = ()

    def handle(self, tool_call: ToolCall, tool_use_context) -> ToolResultEnvelope:
        """创建审批记录、中断记录并更新任务状态。"""
        service = ApprovalRequestIntakeService(tool_use_context.cwd)
        try:
            payload = service.create_request(dict(tool_call.tool_input)).to_payload()
        except ValueError as exc:
            return error_envelope(tool_call, "invalid_approval_request_input", str(exc))
        payload.update(_send_approval_card_feedback(tool_use_context.cwd, payload))
        return ToolResultEnvelope(
            tool_call.tool_use_id,
            tool_call.tool_name,
            True,
            json.dumps(payload, ensure_ascii=False),
            attachments=(
                str(payload["approval_file"]),
                str(payload["interrupt_file"]),
                str(payload["task_file"]),
            ),
        )


def _send_approval_card_feedback(project_root, payload: dict[str, str]) -> dict[str, object]:
    """通过统一反馈接口发送审批卡片，并把发送结果挂回工具结果。"""
    approval = ApprovalStore(project_root).read_approval(payload["approval_id"])
    if approval is None:
        return {"approval_card_status": "approval_not_found", "approval_card_ok": False}
    result = FeedbackGateway(load_env_config(project_root)).send_owner_approval_card(
        _approval_to_card_payload(approval)
    )
    return {
        "approval_card_status": result.status,
        "approval_card_ok": result.ok,
        "approval_card_payload": result.payload,
    }


def _approval_to_card_payload(approval) -> dict[str, str]:
    """把审批记录转换成反馈网关构造卡片所需的稳定字段。"""
    return {
        "approval_id": approval.approval_id,
        "task_id": approval.task_id,
        "risk_level": approval.risk_level,
        "resume_token": approval.resume_token,
        "request": approval.request,
        "reason": approval.reason,
        "risk": approval.risk,
    }


def _self_test() -> None:
    """验证工具名与 contract 一致。"""
    assert CreateApprovalRequestTool.name == CreateApprovalRequestTool.contract["function"]["name"]


if __name__ == "__main__":
    _self_test()
    print("dutyflow create_approval_request logic self-test passed")
