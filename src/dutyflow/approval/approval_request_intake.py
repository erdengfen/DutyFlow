# 本文件负责审批创建工具的最小校验、审批落盘、中断留痕和任务状态更新。

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from dutyflow.approval.approval_flow import ApprovalStore
from dutyflow.approval.task_interrupt import TaskInterruptStore
from dutyflow.tasks.task_state import TaskStore


@dataclass(frozen=True)
class ApprovalRequestToolResult:
    """表示审批创建工具落盘后的最小结果。"""

    approval_id: str
    approval_file: str
    interrupt_id: str
    interrupt_file: str
    task_id: str
    task_file: str
    task_status: str
    approval_status: str
    resume_token: str
    expires_at: str

    def to_payload(self) -> dict[str, str]:
        """把审批创建结果转换为工具层稳定 JSON 结构。"""
        return {
            "approval_id": self.approval_id,
            "approval_file": self.approval_file,
            "interrupt_id": self.interrupt_id,
            "interrupt_file": self.interrupt_file,
            "task_id": self.task_id,
            "task_file": self.task_file,
            "task_status": self.task_status,
            "approval_status": self.approval_status,
            "resume_token": self.resume_token,
            "expires_at": self.expires_at,
        }


class ApprovalRequestIntakeService:
    """为审批创建工具提供统一校验、落盘和任务状态更新能力。"""

    def __init__(
        self,
        project_root: Path,
        *,
        task_store: TaskStore | None = None,
        approval_store: ApprovalStore | None = None,
        interrupt_store: TaskInterruptStore | None = None,
    ) -> None:
        """绑定工作区及任务、审批、中断三个持久化入口。"""
        self.project_root = Path(project_root).resolve()
        self.task_store = task_store or TaskStore(self.project_root)
        self.approval_store = approval_store or ApprovalStore(self.project_root)
        self.interrupt_store = interrupt_store or TaskInterruptStore(self.project_root)

    def create_request(self, tool_input: dict[str, object]) -> ApprovalRequestToolResult:
        """创建审批记录与中断记录，并把任务切到 waiting_approval。"""
        task_id = _require_text(tool_input, "task_id")
        task = self.task_store.read_task(task_id)
        if task is None:
            raise ValueError(f"task not found: {task_id}")
        requested_action = _require_text(tool_input, "requested_action")
        risk_level = _require_text(tool_input, "risk_level")
        request = _require_text(tool_input, "request")
        reason = _require_text(tool_input, "reason")
        risk = _require_text(tool_input, "risk")
        original_action_kind = _require_text(tool_input, "original_action_kind")
        original_tool_name = _require_text(tool_input, "original_tool_name")
        original_tool_input_preview = _require_text(tool_input, "original_tool_input_preview")
        expires_at = _require_iso_datetime(tool_input, "expires_at")
        context_id = _read_text(tool_input, "context_id")
        trace_id = _read_text(tool_input, "trace_id")
        resume_point = _read_text(tool_input, "resume_point") or original_action_kind
        resume_payload = _read_text(tool_input, "resume_payload") or task.resume_payload
        original_action = _read_text(tool_input, "original_action") or requested_action
        resume_token = "resume_" + uuid4().hex[:12]
        approval = self.approval_store.create_approval(
            task_id=task_id,
            requested_action=requested_action,
            risk_level=risk_level,
            request=request,
            reason=reason,
            risk=risk,
            resume_token=resume_token,
            original_action=original_action,
            original_tool_name=original_tool_name,
            original_tool_input_preview=original_tool_input_preview,
            context_id=context_id,
            trace_id=trace_id,
        )
        interrupt = self.interrupt_store.create_interrupt(
            approval_id=approval.approval_id,
            task_id=task_id,
            original_tool_name=original_tool_name,
            original_tool_input_preview=original_tool_input_preview,
            original_action_kind=original_action_kind,
            context_id=context_id,
            trace_id=trace_id,
            resume_token=resume_token,
            expires_at=expires_at,
            summary=_build_interrupt_summary(requested_action, risk_level),
        )
        updated_task = self.task_store.update_task(
            task_id,
            frontmatter_updates={
                "status": "waiting_approval",
                "approval_id": approval.approval_id,
                "resume_point": resume_point,
                "resume_payload": resume_payload,
            },
            state_updates={
                "approval_status": "waiting",
                "last_result_summary": _build_task_summary(requested_action),
            },
            section_updates={
                "next_action": "等待用户在飞书端完成审批后再决定是否恢复该任务。",
                "decision_trace": _append_approval_trace(
                    task.decision_trace,
                    approval.approval_id,
                    requested_action,
                    risk_level,
                    expires_at,
                ),
            },
        )
        return ApprovalRequestToolResult(
            approval_id=approval.approval_id,
            approval_file=_relative_path(self.project_root, approval.path),
            interrupt_id=interrupt.interrupt_id,
            interrupt_file=_relative_path(self.project_root, interrupt.path),
            task_id=updated_task.task_id,
            task_file=_relative_path(self.project_root, updated_task.path),
            task_status=updated_task.status,
            approval_status=updated_task.approval_status,
            resume_token=resume_token,
            expires_at=expires_at,
        )


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


def _require_iso_datetime(tool_input: dict[str, object], key: str) -> str:
    """校验时间字段是否符合 ISO-8601。"""
    value = _require_text(tool_input, key)
    try:
        datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{key} must be an ISO-8601 datetime") from exc
    return value


def _build_interrupt_summary(requested_action: str, risk_level: str) -> str:
    """生成任务中断记录的简洁摘要。"""
    return f"等待审批完成后再恢复 {requested_action} 动作，当前风险等级为 {risk_level}。"


def _build_task_summary(requested_action: str) -> str:
    """生成任务进入审批等待态后的状态摘要。"""
    return f"任务已进入审批等待状态，待确认后再继续执行 {requested_action}。"


def _append_approval_trace(
    existing_trace: str,
    approval_id: str,
    requested_action: str,
    risk_level: str,
    expires_at: str,
) -> str:
    """把新审批节点追加到任务决策留痕文本。"""
    trace_line = (
        f"approval_id={approval_id}; requested_action={requested_action}; "
        f"risk_level={risk_level}; expires_at={expires_at}"
    )
    if not existing_trace.strip():
        return trace_line
    return existing_trace.rstrip() + "\n" + trace_line


def _relative_path(project_root: Path, path: Path) -> str:
    """把绝对路径转换为相对项目根目录的稳定展示路径。"""
    try:
        return str(path.relative_to(project_root))
    except ValueError:
        return str(path)


def _self_test() -> None:
    """验证审批创建服务可生成审批和中断，并更新任务状态。"""
    from tempfile import TemporaryDirectory

    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        task_store = TaskStore(root)
        task = task_store.create_task(title="测试任务")
        service = ApprovalRequestIntakeService(root, task_store=task_store)
        result = service.create_request(
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
        loaded = task_store.read_task(task.task_id)
        assert loaded is not None
        assert result.task_status == "waiting_approval"
        assert loaded.approval_status == "waiting"


if __name__ == "__main__":
    _self_test()
    print("dutyflow approval request intake self-test passed")
