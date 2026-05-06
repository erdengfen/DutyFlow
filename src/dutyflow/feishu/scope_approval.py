# 本文件负责把飞书 scope 启用动作接入用户审批流，并在审批通过后启用同步范围。

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping

from dutyflow.approval.approval_flow import ApprovalRecord, ApprovalStore
from dutyflow.approval.approval_request_intake import ApprovalRequestIntakeService
from dutyflow.feedback.gateway import FeedbackResult
from dutyflow.feishu.scope_registry import (
    DOC_SCOPE,
    DRIVE_FOLDER_SCOPE,
    FILE_SCOPE,
    GROUP_CHAT_SCOPE,
    P2P_CHAT_SCOPE,
    WIKI_SCOPE,
    FeishuScopeRecord,
    FeishuScopeRegistry,
)
from dutyflow.tasks.task_state import TaskRecord, TaskStore

ENABLE_FEISHU_SCOPE_ACTION = "enable_feishu_scope"
_RESUME_PAYLOAD_PREFIX = "json:"
# 关键开关：飞书 scope 启用审批默认保留 24 小时，避免过期审批长期悬挂。
DEFAULT_SCOPE_APPROVAL_EXPIRE_HOURS = 24


@dataclass(frozen=True)
class FeishuScopeApprovalRequestResult:
    """表示一次飞书 scope 启用审批请求的创建结果。"""

    ok: bool
    status: str
    detail: str
    scope_record: FeishuScopeRecord
    approval_id: str = ""
    task_id: str = ""
    resume_token: str = ""
    approval_file: str = ""
    task_file: str = ""
    approval_card_ok: bool = False
    approval_card_status: str = ""

    def to_payload(self) -> dict[str, object]:
        """转换为 CLI 和调试接口可稳定输出的结构。"""
        return {
            "ok": self.ok,
            "status": self.status,
            "detail": self.detail,
            "scope": _scope_payload(self.scope_record),
            "approval_id": self.approval_id,
            "task_id": self.task_id,
            "resume_token": self.resume_token,
            "approval_file": self.approval_file,
            "task_file": self.task_file,
            "approval_card_ok": self.approval_card_ok,
            "approval_card_status": self.approval_card_status,
        }


@dataclass(frozen=True)
class FeishuScopePostApprovalResult:
    """表示审批通过后尝试启用飞书 scope 的后置动作结果。"""

    ok: bool
    status: str
    detail: str
    scope_record: FeishuScopeRecord | None = None
    task_id: str = ""

    def to_payload(self) -> dict[str, object]:
        """转换为卡片回调和接入层可记录的结构。"""
        return {
            "ok": self.ok,
            "status": self.status,
            "detail": self.detail,
            "task_id": self.task_id,
            "scope": _scope_payload(self.scope_record) if self.scope_record else {},
        }


class FeishuScopeApprovalService:
    """封装飞书同步范围从 candidate 到 enabled 的审批边界。"""

    def __init__(
        self,
        project_root: Path,
        *,
        registry: FeishuScopeRegistry | None = None,
        task_store: TaskStore | None = None,
        approval_store: ApprovalStore | None = None,
        feedback_gateway: object | None = None,
    ) -> None:
        """绑定 scope registry、任务存储、审批存储和可选飞书回馈出口。"""
        self.project_root = Path(project_root).resolve()
        self.registry = registry or FeishuScopeRegistry(self.project_root)
        self.task_store = task_store or TaskStore(self.project_root)
        self.approval_store = approval_store or ApprovalStore(self.project_root)
        self.feedback_gateway = feedback_gateway

    def request_enable_scope(
        self,
        record: FeishuScopeRecord,
        *,
        expires_at: str = "",
        context_id: str = "",
        trace_id: str = "",
    ) -> FeishuScopeApprovalRequestResult:
        """为指定 scope 创建飞书端用户确认卡片，不直接启用 scope。"""
        if record.status == "enabled":
            return FeishuScopeApprovalRequestResult(True, "already_enabled", "scope already enabled", record)
        task = self.task_store.create_task(
            title=_task_title(record),
            status="queued",
            weight_level="high",
            source_id=record.scope_id,
            summary=_approval_request_text(record),
            next_action="等待用户在飞书审批卡片中确认是否允许 DutyFlow 读取该范围。",
        )
        created = self._create_approval_request(task, record, expires_at, context_id, trace_id)
        approval = self.approval_store.read_approval(created.approval_id)
        card = self._send_approval_card(approval)
        return FeishuScopeApprovalRequestResult(
            ok=True,
            status="approval_requested",
            detail="approval request created",
            scope_record=record,
            approval_id=created.approval_id,
            task_id=created.task_id,
            resume_token=created.resume_token,
            approval_file=created.approval_file,
            task_file=created.task_file,
            approval_card_ok=card.ok,
            approval_card_status=card.status,
        )

    def enable_scope_from_approval(self, approval_id: str) -> FeishuScopePostApprovalResult:
        """审批通过后按任务恢复载荷启用对应 scope；非 scope 审批直接跳过。"""
        approval = self.approval_store.read_approval(approval_id)
        if approval is None:
            return FeishuScopePostApprovalResult(False, "approval_not_found", approval_id)
        task = self.task_store.read_task(approval.task_id)
        if task is None:
            return FeishuScopePostApprovalResult(False, "task_not_found", approval.task_id)
        if task.resume_point != ENABLE_FEISHU_SCOPE_ACTION:
            return FeishuScopePostApprovalResult(True, "skipped", "resume_point is not feishu scope", task_id=task.task_id)
        if approval.status != "approved":
            return FeishuScopePostApprovalResult(True, "skipped", "approval is not approved", task_id=task.task_id)
        try:
            return self._enable_scope_from_task(approval, task)
        except ValueError as exc:
            return FeishuScopePostApprovalResult(False, "invalid_resume_payload", str(exc), task_id=task.task_id)

    def _create_approval_request(
        self,
        task: TaskRecord,
        record: FeishuScopeRecord,
        expires_at: str,
        context_id: str,
        trace_id: str,
    ):
        """调用现有审批 intake，复用审批记录、中断记录和任务等待态逻辑。"""
        service = ApprovalRequestIntakeService(
            self.project_root,
            task_store=self.task_store,
            approval_store=self.approval_store,
        )
        return service.create_request(
            {
                "task_id": task.task_id,
                "requested_action": ENABLE_FEISHU_SCOPE_ACTION,
                "risk_level": "high",
                "request": _approval_request_text(record),
                "reason": _approval_reason_text(record),
                "risk": _approval_risk_text(record),
                "original_action_kind": ENABLE_FEISHU_SCOPE_ACTION,
                "original_tool_name": "feishu_scope_approval",
                "original_tool_input_preview": _input_preview(record),
                "expires_at": expires_at or _default_expires_at(),
                "context_id": context_id,
                "trace_id": trace_id,
                "resume_point": ENABLE_FEISHU_SCOPE_ACTION,
                "resume_payload": _encode_resume_payload(record),
            }
        )

    def _send_approval_card(self, approval: ApprovalRecord | None) -> FeedbackResult:
        """通过现有反馈出口发送审批卡片；无出口时只保留本地审批记录。"""
        if approval is None:
            return FeedbackResult(False, "approval_not_found", "approval not found")
        if self.feedback_gateway is None:
            return FeedbackResult(False, "feedback_gateway_missing", "feedback gateway is not configured")
        sender = getattr(self.feedback_gateway, "send_owner_approval_card", None)
        if not callable(sender):
            return FeedbackResult(False, "feedback_gateway_invalid", "feedback gateway cannot send approval card")
        return sender(_approval_to_card_payload(approval))

    def _enable_scope_from_task(
        self,
        approval: ApprovalRecord,
        task: TaskRecord,
    ) -> FeishuScopePostApprovalResult:
        """解析恢复载荷并把 scope registry 推进到 enabled。"""
        payload = _decode_resume_payload(task.resume_payload)
        if payload.get("action") != ENABLE_FEISHU_SCOPE_ACTION:
            return FeishuScopePostApprovalResult(False, "invalid_resume_payload", "action mismatch", task_id=task.task_id)
        record = self.registry.read(
            str(payload.get("account_id", "")),
            str(payload.get("scope_type", "")),
            str(payload.get("scope_id", "")),
        )
        if record is None:
            return FeishuScopePostApprovalResult(False, "scope_not_found", "scope not found", task_id=task.task_id)
        self.registry.approve_scope(record.account_id, record.scope_type, record.scope_id, approved_by=approval.decided_by)
        enabled = self.registry.enable_scope(record.account_id, record.scope_type, record.scope_id)
        self._mark_task_completed(task, enabled, approval.approval_id)
        return FeishuScopePostApprovalResult(True, "scope_enabled", "scope enabled", enabled, task.task_id)

    def _mark_task_completed(
        self,
        task: TaskRecord,
        enabled: FeishuScopeRecord,
        approval_id: str,
    ) -> TaskRecord:
        """后置动作完成后把审批任务收敛为 completed，避免后台 worker 重复恢复。"""
        return self.task_store.update_task(
            task.task_id,
            frontmatter_updates={"status": "completed", "approval_id": approval_id, "next_retry_at": ""},
            state_updates={
                "approval_status": "approved",
                "last_result_summary": f"飞书 scope 已启用：{enabled.scope_type} {enabled.scope_id}",
            },
            section_updates={
                "next_action": "scope 已启用，等待对应 collector 在下一轮 collect_enabled_scopes 中消费。",
                "decision_trace": _append_scope_enable_trace(task.decision_trace, approval_id, enabled),
            },
        )


def _approval_to_card_payload(approval: ApprovalRecord) -> dict[str, str]:
    """把审批记录转换成反馈网关发送卡片所需字段。"""
    return {
        "approval_id": approval.approval_id,
        "task_id": approval.task_id,
        "risk_level": approval.risk_level,
        "resume_token": approval.resume_token,
        "request": approval.request,
        "reason": approval.reason,
        "risk": approval.risk,
    }


def _encode_resume_payload(record: FeishuScopeRecord) -> str:
    """把 scope 恢复信息压成 frontmatter 可保存的单行 JSON。"""
    payload = {
        "action": ENABLE_FEISHU_SCOPE_ACTION,
        "account_id": record.account_id,
        "scope_type": record.scope_type,
        "scope_id": record.scope_id,
        "record_id": record.record_id,
        "collector_names": list(record.collector_names),
    }
    return _RESUME_PAYLOAD_PREFIX + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _decode_resume_payload(raw_payload: str) -> dict[str, Any]:
    """解析 scope 审批恢复载荷，拒绝非结构化或非本动作载荷。"""
    text = raw_payload.strip()
    if not text.startswith(_RESUME_PAYLOAD_PREFIX):
        raise ValueError("resume_payload is not a feishu scope payload")
    parsed = json.loads(text.removeprefix(_RESUME_PAYLOAD_PREFIX))
    if not isinstance(parsed, Mapping):
        raise ValueError("resume_payload must be an object")
    return dict(parsed)


def _approval_request_text(record: FeishuScopeRecord) -> str:
    """生成用户审批卡片中的申请内容。"""
    return f"DutyFlow向您请求*{_scope_label(record)}*阅读权限"


def _approval_reason_text(record: FeishuScopeRecord) -> str:
    """说明 scope 启用后系统将按显式边界采集。"""
    return (
        "通过后 DutyFlow 才会把该范围从 candidate 标记为 enabled，"
        f"并允许 {', '.join(record.collector_names) or '对应 collector'} 在后续轮次读取。"
    )


def _approval_risk_text(record: FeishuScopeRecord) -> str:
    """说明阅读授权带来的本地落盘影响。"""
    if record.scope_type in {P2P_CHAT_SCOPE, GROUP_CHAT_SCOPE}:
        return "通过后会读取该对话或群聊的授权范围内消息，并保存到本地 ambient_context。"
    return "通过后会读取该云文档或云盘范围的授权内元数据，并保存到本地 ambient_context。"


def _scope_label(record: FeishuScopeRecord) -> str:
    """生成卡片中用于强调的资源标签。"""
    display = record.source_id or record.source_chat_id or record.source_url or record.scope_id
    return f"{_scope_kind_text(record.scope_type)} {display}".strip()


def _scope_kind_text(scope_type: str) -> str:
    """把内部 scope_type 转成人可读资源类型。"""
    mapping = {
        P2P_CHAT_SCOPE: "对话",
        GROUP_CHAT_SCOPE: "群聊",
        DRIVE_FOLDER_SCOPE: "云盘文件夹",
        DOC_SCOPE: "云文档",
        WIKI_SCOPE: "知识库文档",
        FILE_SCOPE: "云盘文件",
    }
    return mapping.get(scope_type, "飞书范围")


def _task_title(record: FeishuScopeRecord) -> str:
    """生成审批任务标题，便于任务列表人工检查。"""
    return "飞书阅读范围授权：" + _scope_label(record)


def _input_preview(record: FeishuScopeRecord) -> str:
    """生成原动作输入预览，不泄露访问 token。"""
    return f"record_id={record.record_id}; scope_type={record.scope_type}; scope_id={record.scope_id}"


def _append_scope_enable_trace(existing_trace: str, approval_id: str, record: FeishuScopeRecord) -> str:
    """追加 scope 启用动作的决策留痕。"""
    line = f"approval_id={approval_id}; action=enable_feishu_scope; scope_id={record.scope_id}; status=enabled"
    if not existing_trace.strip():
        return line
    return existing_trace.rstrip() + "\n" + line


def _default_expires_at() -> str:
    """生成默认审批过期时间。"""
    expires_at = datetime.now().astimezone() + timedelta(hours=DEFAULT_SCOPE_APPROVAL_EXPIRE_HOURS)
    return expires_at.isoformat(timespec="seconds")


def _scope_payload(record: FeishuScopeRecord) -> dict[str, object]:
    """生成 scope 审批结果中的稳定摘要。"""
    return {
        "record_id": record.record_id,
        "account_id": record.account_id,
        "scope_type": record.scope_type,
        "scope_id": record.scope_id,
        "status": record.status,
        "collector_names": list(record.collector_names),
    }


def _self_test() -> None:
    """验证恢复载荷可被编码和解析。"""
    record = FeishuScopeRecord("account", GROUP_CHAT_SCOPE, "oc_group", collector_names=("collector",))
    payload = _decode_resume_payload(_encode_resume_payload(record))
    assert payload["action"] == ENABLE_FEISHU_SCOPE_ACTION
    assert payload["scope_id"] == "oc_group"


if __name__ == "__main__":
    _self_test()
    print("dutyflow feishu scope approval self-test passed")
