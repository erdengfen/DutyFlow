# 本文件负责解析飞书审批卡片按钮回调，并桥接到审批恢复服务。

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import json
import sys
from typing import Any, Mapping

if __package__ in {None, ""}:
    _SRC_ROOT = Path(__file__).resolve().parents[2]
    if str(_SRC_ROOT) not in sys.path:
        sys.path.insert(0, str(_SRC_ROOT))

from dutyflow.approval.approval_resume_intake import ApprovalResumeIntakeService

_DECISION_ALIASES = {
    "approve": "approved",
    "approved": "approved",
    "reject": "rejected",
    "rejected": "rejected",
    "defer": "deferred",
    "deferred": "deferred",
    "expire": "expired",
    "expired": "expired",
}


@dataclass(frozen=True)
class ApprovalCardActionResult:
    """表示一次飞书审批卡片按钮回调的处理结果。"""

    ok: bool
    status: str
    event_id: str
    approval_id: str
    decision_result: str
    task_id: str = ""
    task_status: str = ""
    toast_type: str = "info"
    toast_content: str = ""
    payload: dict[str, Any] = field(default_factory=dict)


class ApprovalCardActionService:
    """把飞书卡片 action.value 转换为审批恢复请求。"""

    def __init__(
        self,
        project_root: Path,
        *,
        resume_service: ApprovalResumeIntakeService | None = None,
    ) -> None:
        """绑定工作区和审批恢复服务。"""
        self.project_root = Path(project_root).resolve()
        self.resume_service = resume_service or ApprovalResumeIntakeService(self.project_root)

    def handle_raw_event(self, raw_event: Mapping[str, Any]) -> ApprovalCardActionResult:
        """处理一条 `card.action.trigger` 原始事件。"""
        event_id = _extract_event_id(raw_event)
        value = _extract_action_value(raw_event)
        if value.get("dutyflow_action") != "approval_decision":
            return _ignored_result(event_id, value)
        try:
            result = self.resume_service.resume_after_decision(_build_resume_input(raw_event, value))
        except ValueError as exc:
            return _failed_result(event_id, value, str(exc))
        payload = result.to_payload()
        return ApprovalCardActionResult(
            ok=True,
            status="approval_resumed",
            event_id=event_id,
            approval_id=result.approval_id,
            decision_result=result.decision_result,
            task_id=result.task_id,
            task_status=result.task_status,
            toast_type="success",
            toast_content=_success_toast(result.decision_result),
            payload=payload,
        )


def _build_resume_input(raw_event: Mapping[str, Any], value: Mapping[str, str]) -> dict[str, object]:
    """把卡片按钮 value 转成审批恢复服务输入。"""
    return {
        "approval_id": _require_value(value, "approval_id"),
        "decision_result": _normalize_decision(_require_value(value, "decision_result")),
        "decided_by": _extract_operator_open_id(raw_event) or "feishu_card",
        "resume_token": str(value.get("resume_token", "")).strip(),
        "comment": "decision from Feishu approval card",
    }


def _extract_action_value(raw_event: Mapping[str, Any]) -> dict[str, str]:
    """从飞书卡片回调中提取 action.value。"""
    event = _mapping(raw_event.get("event"))
    action = _mapping(event.get("action"))
    value = action.get("value", {})
    if isinstance(value, str):
        return _parse_value_json(value)
    if isinstance(value, Mapping):
        return {str(key): str(item) for key, item in value.items()}
    return {}


def _parse_value_json(value: str) -> dict[str, str]:
    """兼容 SDK 把 action.value 序列化成 JSON 字符串的情况。"""
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, Mapping):
        return {}
    return {str(key): str(item) for key, item in parsed.items()}


def _extract_operator_open_id(raw_event: Mapping[str, Any]) -> str:
    """从卡片回调中提取操作者 open_id，兼容不同 SDK 结构。"""
    event = _mapping(raw_event.get("event"))
    operator = _mapping(event.get("operator"))
    operator_id = _mapping(operator.get("operator_id"))
    return _pick_text(
        operator.get("open_id"),
        operator_id.get("open_id"),
        _mapping(operator.get("user_id")).get("open_id"),
    )


def _extract_event_id(raw_event: Mapping[str, Any]) -> str:
    """从 header 中提取事件 ID。"""
    header = _mapping(raw_event.get("header"))
    return _pick_text(header.get("event_id"), raw_event.get("event_id"))


def _normalize_decision(raw_value: str) -> str:
    """把按钮值或直接决策值转换为审批恢复枚举。"""
    normalized = raw_value.strip().lower()
    decision = _DECISION_ALIASES.get(normalized, "")
    if not decision:
        raise ValueError("decision_result must be one of: approved, rejected, deferred, expired")
    return decision


def _require_value(value: Mapping[str, str], key: str) -> str:
    """读取 action.value 中的必填字段。"""
    text = str(value.get(key, "")).strip()
    if text:
        return text
    raise ValueError(f"{key} is required")


def _mapping(value: object) -> dict[str, Any]:
    """把不确定对象安全转换为字典。"""
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _pick_text(*values: object) -> str:
    """返回第一个非空文本。"""
    for value in values:
        text = "" if value is None else str(value).strip()
        if text:
            return text
    return ""


def _ignored_result(event_id: str, value: Mapping[str, str]) -> ApprovalCardActionResult:
    """返回非 DutyFlow 审批按钮的忽略结果。"""
    return ApprovalCardActionResult(
        ok=True,
        status="ignored",
        event_id=event_id,
        approval_id=str(value.get("approval_id", "")),
        decision_result=str(value.get("decision_result", "")),
        toast_type="info",
        toast_content="该卡片动作不属于 DutyFlow 审批。",
        payload={"action_value": dict(value)},
    )


def _failed_result(event_id: str, value: Mapping[str, str], error_message: str) -> ApprovalCardActionResult:
    """返回审批恢复失败结果，供飞书按钮回调展示错误 toast。"""
    return ApprovalCardActionResult(
        ok=False,
        status="approval_resume_failed",
        event_id=event_id,
        approval_id=str(value.get("approval_id", "")),
        decision_result=str(value.get("decision_result", "")),
        toast_type="error",
        toast_content="审批处理失败，请稍后重试或检查本地任务状态。",
        payload={"error": error_message, "action_value": dict(value)},
    )


def _success_toast(decision_result: str) -> str:
    """根据审批结果生成卡片按钮回调 toast。"""
    if decision_result == "approved":
        return "审批已通过，任务已进入后台恢复队列。"
    if decision_result == "rejected":
        return "审批已拒绝，任务不会继续执行原动作。"
    if decision_result == "deferred":
        return "审批已延后，任务暂不继续执行。"
    return "审批已标记超时，任务等待后续确认。"


def _self_test() -> None:
    """验证 action.value 能被解析为审批恢复输入。"""
    raw_event = {
        "header": {"event_id": "evt_card", "event_type": "card.action.trigger"},
        "event": {
            "operator": {"open_id": "ou_owner"},
            "action": {
                "value": {
                    "dutyflow_action": "approval_decision",
                    "approval_id": "approval_001",
                    "resume_token": "resume_001",
                    "decision_result": "approved",
                }
            },
        },
    }
    value = _extract_action_value(raw_event)
    resume_input = _build_resume_input(raw_event, value)
    assert resume_input["approval_id"] == "approval_001"
    assert resume_input["decision_result"] == "approved"
    assert resume_input["decided_by"] == "ou_owner"


if __name__ == "__main__":
    _self_test()
    print("dutyflow approval card action self-test passed")
