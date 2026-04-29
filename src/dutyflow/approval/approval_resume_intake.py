# 本文件负责审批恢复工具的最小决策校验、审批完成态写入和任务状态流转。

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dutyflow.agent.control_state_store import AgentControlStateStore
from dutyflow.approval.approval_flow import ApprovalStore
from dutyflow.approval.task_interrupt import TaskInterruptStore
from dutyflow.tasks.task_state import TaskStore

_ALLOWED_DECISIONS = frozenset({"approved", "rejected", "deferred", "expired"})


@dataclass(frozen=True)
class ApprovalResumeToolResult:
    """表示审批恢复工具处理决策后的最小结果。"""

    approval_id: str
    approval_file: str
    interrupt_id: str
    task_id: str
    task_file: str
    decision_result: str
    task_status: str
    approval_status: str
    resume_token: str
    resume_point: str
    resumed: bool

    def to_payload(self) -> dict[str, object]:
        """把审批恢复结果转换为工具层稳定 JSON 结构。"""
        return {
            "approval_id": self.approval_id,
            "approval_file": self.approval_file,
            "interrupt_id": self.interrupt_id,
            "task_id": self.task_id,
            "task_file": self.task_file,
            "decision_result": self.decision_result,
            "task_status": self.task_status,
            "approval_status": self.approval_status,
            "resume_token": self.resume_token,
            "resume_point": self.resume_point,
            "resumed": self.resumed,
        }


class ApprovalResumeIntakeService:
    """为审批恢复工具提供统一校验和任务状态更新能力。"""

    def __init__(
        self,
        project_root: Path,
        *,
        task_store: TaskStore | None = None,
        approval_store: ApprovalStore | None = None,
        interrupt_store: TaskInterruptStore | None = None,
        control_state_store: AgentControlStateStore | None = None,
    ) -> None:
        """绑定工作区及任务、审批、中断三个持久化入口。"""
        self.project_root = Path(project_root).resolve()
        self.task_store = task_store or TaskStore(self.project_root)
        self.approval_store = approval_store or ApprovalStore(self.project_root)
        self.interrupt_store = interrupt_store or TaskInterruptStore(self.project_root)
        self.control_state_store = control_state_store or AgentControlStateStore(
            self.project_root,
            task_store=self.task_store,
        )

    def resume_after_decision(self, tool_input: dict[str, object]) -> ApprovalResumeToolResult:
        """根据审批决策完成审批记录，并更新对应任务状态。"""
        approval_id = _require_text(tool_input, "approval_id")
        decision = _normalize_decision(_require_text(tool_input, "decision_result"))
        decided_by = _require_text(tool_input, "decided_by")
        comment = _read_text(tool_input, "comment")
        resume_token = _read_text(tool_input, "resume_token")
        approval = self.approval_store.read_approval(approval_id)
        if approval is None:
            raise ValueError(f"approval not found: {approval_id}")
        if approval.status != "waiting":
            raise ValueError(f"approval is not waiting: {approval_id}")
        if resume_token and resume_token != approval.resume_token:
            raise ValueError("resume_token does not match approval")
        interrupt = self.interrupt_store.find_by_approval_id(approval_id)
        if interrupt is None:
            raise ValueError(f"interrupt not found for approval: {approval_id}")
        task = self.task_store.read_task(approval.task_id)
        if task is None:
            raise ValueError(f"task not found: {approval.task_id}")
        resolved = self.approval_store.resolve_approval(
            approval_id,
            result=decision,
            decided_by=decided_by,
            comment=comment,
        )
        updated_task = self.task_store.update_task(
            task.task_id,
            frontmatter_updates=_task_frontmatter_updates(task, resolved.approval_id, decision),
            state_updates=_task_state_updates(resolved.requested_action, decision),
            section_updates={
                "next_action": _next_action_for_decision(decision),
                "decision_trace": _append_resume_trace(task.decision_trace, resolved.approval_id, decision),
            },
        )
        self.control_state_store.sync()
        return ApprovalResumeToolResult(
            approval_id=resolved.approval_id,
            approval_file=_relative_path(self.project_root, resolved.path),
            interrupt_id=interrupt.interrupt_id,
            task_id=updated_task.task_id,
            task_file=_relative_path(self.project_root, updated_task.path),
            decision_result=decision,
            task_status=updated_task.status,
            approval_status=updated_task.approval_status,
            resume_token=resolved.resume_token,
            resume_point=updated_task.resume_point,
            resumed=decision == "approved",
        )


def _task_frontmatter_updates(task, approval_id: str, decision: str) -> dict[str, str]:
    """根据审批决策生成任务 frontmatter 更新。"""
    if decision == "approved":
        return {
            "status": "queued",
            "approval_id": approval_id,
            "next_retry_at": "",
        }
    if decision == "expired":
        return {
            "status": "expired",
            "approval_id": approval_id,
            "next_retry_at": task.next_retry_at,
        }
    if decision == "deferred":
        return {
            "status": "blocked",
            "approval_id": approval_id,
            "next_retry_at": task.next_retry_at,
        }
    return {
        "status": "cancelled",
        "approval_id": approval_id,
        "next_retry_at": "",
    }


def _task_state_updates(requested_action: str, decision: str) -> dict[str, str]:
    """根据审批决策生成任务当前状态 section 更新。"""
    return {
        "approval_status": decision,
        "last_result_summary": _last_result_summary(requested_action, decision),
    }


def _last_result_summary(requested_action: str, decision: str) -> str:
    """生成审批决策后的任务状态摘要。"""
    if decision == "approved":
        return f"审批已通过，任务等待后台 worker 恢复执行 {requested_action}。"
    if decision == "rejected":
        return f"审批已拒绝，任务不会继续执行 {requested_action}。"
    if decision == "deferred":
        return f"审批已延后，任务暂不继续执行 {requested_action}。"
    return f"审批已超时，任务暂不继续执行 {requested_action}。"


def _next_action_for_decision(decision: str) -> str:
    """生成审批决策后的下一步动作。"""
    if decision == "approved":
        return "等待后台 worker 按 resume_point 和 resume_payload 恢复任务。"
    if decision == "rejected":
        return "审批已拒绝，保留记录但不恢复原动作。"
    if decision == "deferred":
        return "审批已延后，等待用户后续重新确认。"
    return "审批已超时，下一次用户交互时再次询问继续执行还是弃用。"


def _append_resume_trace(existing_trace: str, approval_id: str, decision: str) -> str:
    """把审批恢复结果追加到任务决策留痕文本。"""
    trace_line = f"approval_id={approval_id}; decision_result={decision}; resume_tool=resume_after_approval"
    if not existing_trace.strip():
        return trace_line
    return existing_trace.rstrip() + "\n" + trace_line


def _normalize_decision(value: str) -> str:
    """校验并规范化审批决策枚举。"""
    normalized = value.strip().lower()
    if normalized not in _ALLOWED_DECISIONS:
        raise ValueError("decision_result must be one of: approved, rejected, deferred, expired")
    return normalized


def _require_text(tool_input: dict[str, object], key: str) -> str:
    """读取必填文本字段；缺失时给出稳定错误。"""
    value = _read_text(tool_input, key)
    if value:
        return value
    raise ValueError(f"{key} is required")


def _read_text(tool_input: dict[str, object], key: str) -> str:
    """把任意输入值转换为去空白字符串。"""
    value = tool_input.get(key, "")
    if value is None:
        return ""
    return str(value).strip()


def _relative_path(project_root: Path, path: Path) -> str:
    """把绝对路径转换为相对项目根目录的稳定展示路径。"""
    try:
        return str(path.relative_to(project_root))
    except ValueError:
        return str(path)


def _self_test() -> None:
    """验证审批通过后任务会回到 queued 状态等待后台 worker。"""
    from tempfile import TemporaryDirectory

    from dutyflow.approval.approval_request_intake import ApprovalRequestIntakeService

    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        task_store = TaskStore(root)
        task = task_store.create_task(title="测试任务")
        created = ApprovalRequestIntakeService(root, task_store=task_store).create_request(
            {
                "task_id": task.task_id,
                "requested_action": "knowledge_write",
                "risk_level": "high",
                "request": "需要写入知识库。",
                "reason": "该动作会修改本地资料。",
                "risk": "可能写入错误信息。",
                "original_action_kind": "knowledge_write",
                "original_tool_name": "add_contact_knowledge",
                "original_tool_input_preview": "contact_id=contact_001",
                "expires_at": "2026-05-01T10:00:00+08:00",
            }
        )
        result = ApprovalResumeIntakeService(root, task_store=task_store).resume_after_decision(
            {
                "approval_id": created.approval_id,
                "decision_result": "approved",
                "decided_by": "user",
                "resume_token": created.resume_token,
            }
        )
        loaded = task_store.read_task(task.task_id)
        assert result.resumed is True
        assert loaded is not None
        assert loaded.status == "queued"


if __name__ == "__main__":
    _self_test()
    print("dutyflow approval resume intake self-test passed")
