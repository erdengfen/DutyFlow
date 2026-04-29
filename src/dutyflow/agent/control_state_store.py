# 本文件负责把后台任务、审批等待和最近事件同步到可见的 Agent 控制快照。

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sys
import tempfile
from typing import TYPE_CHECKING

if __package__ in {None, ""}:
    _SRC_ROOT = Path(__file__).resolve().parents[2]
    if str(_SRC_ROOT) not in sys.path:
        sys.path.insert(0, str(_SRC_ROOT))

from dutyflow.storage.file_store import FileStore
from dutyflow.storage.markdown_store import MarkdownDocument, MarkdownStore

if TYPE_CHECKING:
    from dutyflow.tasks.task_state import TaskRecord, TaskStore


ACTIVE_TASK_STATUSES = frozenset(
    {
        "queued",
        "scheduled",
        "running",
        "waiting_approval",
        "blocked",
        "expired",
    }
)
WAITING_APPROVAL_STATUS = "waiting"


@dataclass(frozen=True)
class AgentControlStateSnapshot:
    """表示一次 Agent 控制快照同步后的可观察结果。"""

    path: Path
    status: str
    current_model: str
    permission_mode: str
    active_task_ids: tuple[str, ...]
    waiting_approval_task_ids: tuple[str, ...]
    last_event_id: str
    updated_at: str


class AgentControlStateStore:
    """维护 `data/state/agent_control_state.md` 这份人工可读控制快照。"""

    def __init__(
        self,
        project_root: Path,
        *,
        task_store: TaskStore | None = None,
        markdown_store: MarkdownStore | None = None,
        data_dir: Path | None = None,
    ) -> None:
        """绑定项目根目录、任务存储和状态文件位置。"""
        self.project_root = Path(project_root).resolve()
        self.markdown_store = markdown_store or MarkdownStore(FileStore(self.project_root))
        if task_store is None:
            from dutyflow.tasks.task_state import TaskStore

            self.task_store = TaskStore(self.project_root)
        else:
            self.task_store = task_store
        self.data_dir = data_dir or Path("data")
        self.state_path = (
            _resolve_data_dir(self.project_root, self.data_dir)
            / "state"
            / "agent_control_state.md"
        )

    def sync(
        self,
        *,
        current_model: str = "",
        permission_mode: str = "",
        last_event_id: str = "",
    ) -> AgentControlStateSnapshot:
        """从任务 Markdown 汇总当前控制面，并写入 Agent 控制快照。"""
        existing = self._read_existing_frontmatter()
        tasks = self.task_store.list_tasks()
        active_task_ids = tuple(task.task_id for task in tasks if _is_active_task(task))
        waiting_task_ids = tuple(task.task_id for task in tasks if _is_waiting_approval_task(task))
        resolved_current_model = _select_text(current_model, existing.get("current_model", ""))
        resolved_permission_mode = _select_text(
            permission_mode,
            existing.get("permission_mode", "default"),
        )
        resolved_last_event_id = _select_text(last_event_id, existing.get("last_event_id", ""))
        updated_at = _now_iso()
        runtime_status = _runtime_status(active_task_ids, waiting_task_ids)
        document = MarkdownDocument(
            frontmatter={
                "schema": "dutyflow.agent_control_state.v1",
                "id": "agent_control_state_local_user",
                "updated_at": updated_at,
                "current_model": resolved_current_model,
                "permission_mode": resolved_permission_mode,
                "active_task_ids": ",".join(active_task_ids),
                "waiting_approval_task_ids": ",".join(waiting_task_ids),
                "last_event_id": resolved_last_event_id,
            },
            body=_build_body(
                tasks,
                status=runtime_status,
                current_model=resolved_current_model,
                permission_mode=resolved_permission_mode,
                last_event_id=resolved_last_event_id,
            ),
        )
        path = self.markdown_store.write_document(self.state_path, document)
        return AgentControlStateSnapshot(
            path=path,
            status=runtime_status,
            current_model=resolved_current_model,
            permission_mode=resolved_permission_mode,
            active_task_ids=active_task_ids,
            waiting_approval_task_ids=waiting_task_ids,
            last_event_id=resolved_last_event_id,
            updated_at=updated_at,
        )

    def _read_existing_frontmatter(self) -> dict[str, str]:
        """读取已有控制快照字段；文件缺失时返回空对象，损坏时显式报错。"""
        if not self.markdown_store.exists(self.state_path):
            return {}
        return self.markdown_store.read_document(self.state_path).frontmatter


def sync_agent_control_state(
    project_root: Path,
    *,
    task_store: TaskStore | None = None,
    current_model: str = "",
    permission_mode: str = "",
    last_event_id: str = "",
) -> AgentControlStateSnapshot:
    """便捷同步入口，供任务、审批和接入层在状态变化后调用。"""
    return AgentControlStateStore(project_root, task_store=task_store).sync(
        current_model=current_model,
        permission_mode=permission_mode,
        last_event_id=last_event_id,
    )


def _build_body(
    tasks: tuple[TaskRecord, ...],
    *,
    status: str,
    current_model: str,
    permission_mode: str,
    last_event_id: str,
) -> str:
    """渲染控制快照正文，保留任务控制表和恢复观察摘要。"""
    task_rows = "\n".join(_task_row(task) for task in tasks) or ""
    status_counts = _count_by_status(tasks)
    recovery_row = (
        f"| all_tasks | {status_counts.get('waiting_approval', 0)} | "
        f"{status_counts.get('blocked', 0)} | {status_counts.get('expired', 0)} | "
        f"{status_counts.get('failed', 0)} | |\n"
    )
    return (
        "# Agent Control State Snapshot\n\n"
        "## Runtime\n\n"
        f"- status: {status}\n"
        f"- current_model: {current_model}\n"
        f"- permission_mode: {permission_mode}\n"
        f"- last_event: {last_event_id}\n\n"
        "## Task Control\n\n"
        "| task_id | status | weight_level | attempt_count | approval_status | "
        "retry_status | next_action |\n"
        "|---|---|---|---:|---|---|---|\n"
        f"{task_rows}\n\n"
        "## Recovery\n\n"
        "| scope_id | waiting_approval_tasks | blocked_tasks | expired_tasks | "
        "failed_tasks | latest_resume_point |\n"
        "|---|---:|---:|---:|---:|---|\n"
        f"{recovery_row}\n"
        "## Recovery Scopes\n\n"
        "| recovery_id | scope_type | scope_id | status | failure_kind | "
        "interruption_reason | strategy | attempt_count | next_retry_at | resume_point |\n"
        "|---|---|---|---|---|---|---|---:|---|---|\n\n"
        "## Notes\n\n"
        "This file is a visibility snapshot generated from task and approval Markdown records. "
        "It does not replace the in-memory AgentState used inside a single model loop.\n"
    )


def _task_row(task: TaskRecord) -> str:
    """把任务记录转换为控制面表格的一行。"""
    return (
        f"| {_cell(task.task_id)} | {_cell(task.status)} | {_cell(task.weight_level)} | "
        f"{_attempt_count(task.attempt_count)} | {_cell(task.approval_status)} | "
        f"{_cell(task.retry_status)} | {_cell(task.next_action)} |"
    )


def _count_by_status(tasks: tuple[TaskRecord, ...]) -> dict[str, int]:
    """按任务状态统计数量。"""
    counts: dict[str, int] = {}
    for task in tasks:
        counts[task.status] = counts.get(task.status, 0) + 1
    return counts


def _is_active_task(task: TaskRecord) -> bool:
    """判断任务是否仍应展示在 active_task_ids 中。"""
    return task.status in ACTIVE_TASK_STATUSES


def _is_waiting_approval_task(task: TaskRecord) -> bool:
    """判断任务是否处于审批等待状态。"""
    return task.status == "waiting_approval" or task.approval_status == WAITING_APPROVAL_STATUS


def _runtime_status(
    active_task_ids: tuple[str, ...],
    waiting_task_ids: tuple[str, ...],
) -> str:
    """根据当前任务集合生成快照运行状态。"""
    if waiting_task_ids:
        return "waiting_approval"
    if active_task_ids:
        return "active"
    return "idle"


def _cell(value: str) -> str:
    """清理 Markdown 表格单元格，避免多行和竖线破坏结构。"""
    text = str(value or "").replace("\n", " ").replace("|", "/").strip()
    return text


def _attempt_count(value: str) -> str:
    """把任务中的尝试次数字段规范成表格可读数字。"""
    text = str(value or "0").strip()
    try:
        return str(max(0, int(text)))
    except ValueError:
        return "0"


def _select_text(preferred: str, fallback: str) -> str:
    """选择显式输入优先的文本字段。"""
    value = str(preferred or "").strip()
    if value:
        return value
    return str(fallback or "").strip()


def _resolve_data_dir(project_root: Path, data_dir: Path) -> Path:
    """把 data_dir 解析成工作区内绝对路径。"""
    if data_dir.is_absolute():
        return data_dir
    return project_root / data_dir


def _now_iso() -> str:
    """返回 UTC ISO 时间字符串，用于状态快照更新时间。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _self_test() -> None:
    """验证控制快照能从任务记录中生成 active 和 waiting 摘要。"""
    from dutyflow.tasks.task_state import TaskStore

    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        task_store = TaskStore(root)
        task_store.create_task(title="queued", task_id="task_control_selftest", status="queued")
        task_store.create_task(
            title="approval",
            task_id="task_control_waiting",
            status="waiting_approval",
            approval_status="waiting",
        )
        snapshot = AgentControlStateStore(root, task_store=task_store).sync(
            last_event_id="evt_selftest"
        )
        document = MarkdownStore(FileStore(root)).read_document(snapshot.path)
    assert snapshot.status == "waiting_approval"
    assert snapshot.waiting_approval_task_ids == ("task_control_waiting",)
    assert document.frontmatter["last_event_id"] == "evt_selftest"


if __name__ == "__main__":
    _self_test()
    print("dutyflow agent control state store self-test passed")
