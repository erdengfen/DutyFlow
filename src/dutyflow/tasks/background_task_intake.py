# 本文件负责后台任务入口工具的最小意图校验、能力裁决和任务落盘。

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import sys
from typing import TYPE_CHECKING, Callable, Mapping, Protocol

if __package__ in {None, ""}:
    _SRC_ROOT = Path(__file__).resolve().parents[2]
    if str(_SRC_ROOT) not in sys.path:
        sys.path.insert(0, str(_SRC_ROOT))

from dutyflow.agent.control_state_store import AgentControlStateStore
from dutyflow.agent.skills import SkillRegistry
from dutyflow.tasks.task_result import TaskResultStore
from dutyflow.tasks.task_state import TaskStore

if TYPE_CHECKING:
    from dutyflow.agent.tools.registry import ToolRegistry


class ToolRegistryLike(Protocol):
    """定义后台任务入口服务判定工具白名单所需的最小接口。"""

    def has(self, name: str) -> bool:
        """返回指定工具名是否已存在于当前注册表。"""


_FORBIDDEN_BACKGROUND_TOOLS = frozenset(
    {
        "open_cli_session",
        "exec_cli_command",
        "close_cli_session",
    }
)


@dataclass(frozen=True)
class BackgroundTaskToolResult:
    """表示后台任务入口工具落盘后的最小结果。"""

    task_id: str
    status: str
    run_mode: str
    task_file: str
    title: str
    summary: str
    execution_profile: str
    requested_capabilities: tuple[str, ...]
    resolved_skills: tuple[str, ...]
    resolved_tools: tuple[str, ...]
    scheduled_for: str

    def to_payload(self) -> dict[str, object]:
        """把后台任务入口结果转换为工具层稳定 JSON 结构。"""
        return {
            "task_id": self.task_id,
            "status": self.status,
            "run_mode": self.run_mode,
            "task_file": self.task_file,
            "title": self.title,
            "summary": self.summary,
            "execution_profile": self.execution_profile,
            "requested_capabilities": list(self.requested_capabilities),
            "resolved_skills": list(self.resolved_skills),
            "resolved_tools": list(self.resolved_tools),
            "scheduled_for": self.scheduled_for,
        }


class BackgroundTaskIntakeService:
    """为后台任务入口工具提供统一的校验、裁决和落盘能力。"""

    def __init__(
        self,
        project_root: Path,
        registry: ToolRegistryLike,
        skill_registry: SkillRegistry,
        *,
        task_store: TaskStore | None = None,
        result_store: TaskResultStore | None = None,
        control_state_store: AgentControlStateStore | None = None,
        time_provider: Callable[[], datetime] | None = None,
        default_source_event_id: str = "",
        default_source_id: str = "",
    ) -> None:
        """绑定工作区、可用工具注册表和技能注册表。"""
        self.project_root = Path(project_root).resolve()
        self.registry = registry
        self.skill_registry = skill_registry
        self.task_store = task_store or TaskStore(self.project_root)
        self.result_store = result_store or TaskResultStore(self.project_root)
        self.time_provider = time_provider or _local_now
        self.default_source_event_id = default_source_event_id.strip()
        self.default_source_id = default_source_id.strip()
        self.control_state_store = control_state_store or AgentControlStateStore(
            self.project_root,
            task_store=self.task_store,
        )

    def create_async_task(self, tool_input: Mapping[str, object]) -> BackgroundTaskToolResult:
        """创建立即进入后台队列的异步任务。"""
        return self._create_task(tool_input, status="queued", run_mode="async_now", scheduled_for="")

    def create_scheduled_task(self, tool_input: Mapping[str, object]) -> BackgroundTaskToolResult:
        """创建在未来指定时间运行的一次性定时任务。"""
        scheduled_for = _require_future_iso_datetime(tool_input, "scheduled_for", self.time_provider())
        return self._create_task(
            tool_input,
            status="scheduled",
            run_mode="run_at",
            scheduled_for=scheduled_for,
        )

    def _create_task(
        self,
        tool_input: Mapping[str, object],
        *,
        status: str,
        run_mode: str,
        scheduled_for: str,
    ) -> BackgroundTaskToolResult:
        """按统一规则解析模型意图、裁决能力面并创建任务。"""
        title = _require_non_empty(tool_input, "title")
        goal = _require_non_empty(tool_input, "goal")
        success_criteria = _require_non_empty(tool_input, "success_criteria")
        summary = _build_user_visible_summary(
            _read_text(tool_input, "user_visible_summary") or goal,
            run_mode,
            scheduled_for,
        )
        context_refs = _normalize_csv(_read_text(tool_input, "context_refs"))
        capabilities = _normalize_csv(_read_text(tool_input, "capability_requirements"))
        resolved_skills = self._resolve_skills(_normalize_csv(_read_text(tool_input, "preferred_skills")))
        resolved_tools = self._resolve_tools(_normalize_csv(_read_text(tool_input, "preferred_tools")))
        execution_profile = _build_execution_profile(run_mode, capabilities, resolved_skills, resolved_tools)
        record = self.task_store.create_task(
            title=title,
            status=status,
            source_event_id=self.default_source_event_id,
            source_id=self.default_source_id,
            run_mode=run_mode,
            scheduled_for=scheduled_for,
            execution_profile=execution_profile,
            requested_capabilities=",".join(capabilities),
            resolved_skills=",".join(resolved_skills),
            resolved_tools=",".join(resolved_tools),
            summary=summary,
            resume_payload=_build_resume_payload(goal, success_criteria, context_refs),
            decision_trace=_build_decision_trace(
                title,
                goal,
                success_criteria,
                context_refs,
                capabilities,
                resolved_skills,
                resolved_tools,
            ),
            next_action=_build_next_action(run_mode, scheduled_for),
            last_result_summary=_build_last_result_summary(run_mode, scheduled_for),
        )
        self.result_store.create_placeholder(record)
        self.control_state_store.sync()
        return BackgroundTaskToolResult(
            task_id=record.task_id,
            status=record.status,
            run_mode=record.run_mode,
            task_file=_relative_path(self.project_root, record.path),
            title=record.title,
            summary=record.summary,
            execution_profile=record.execution_profile,
            requested_capabilities=capabilities,
            resolved_skills=resolved_skills,
            resolved_tools=resolved_tools,
            scheduled_for=record.scheduled_for,
        )

    def _resolve_skills(self, requested_skills: tuple[str, ...]) -> tuple[str, ...]:
        """校验模型建议的技能清单是否都在后台白名单内。"""
        if not requested_skills:
            return ()
        unknown = [name for name in requested_skills if not self.skill_registry.has(name)]
        if unknown:
            raise ValueError("unknown background skills: " + ", ".join(sorted(set(unknown))))
        return requested_skills

    def _resolve_tools(self, requested_tools: tuple[str, ...]) -> tuple[str, ...]:
        """校验模型建议的工具清单，并拒绝开发期 CLI tools。"""
        if not requested_tools:
            return ()
        forbidden = [name for name in requested_tools if name in _FORBIDDEN_BACKGROUND_TOOLS]
        if forbidden:
            raise ValueError("forbidden background tools: " + ", ".join(sorted(set(forbidden))))
        unknown = [name for name in requested_tools if not self.registry.has(name)]
        if unknown:
            raise ValueError("unknown background tools: " + ", ".join(sorted(set(unknown))))
        return requested_tools


def _read_text(tool_input: Mapping[str, object], key: str) -> str:
    """把工具输入中的任意值统一读取为已去空白字符串。"""
    value = tool_input.get(key, "")
    if value is None:
        return ""
    return str(value).strip()


def _require_non_empty(tool_input: Mapping[str, object], key: str) -> str:
    """读取必填字符串字段；缺失时给出稳定错误。"""
    value = _read_text(tool_input, key)
    if value:
        return value
    raise ValueError(f"{key} is required")


def _require_future_iso_datetime(tool_input: Mapping[str, object], key: str, reference_time: datetime) -> str:
    """校验必填时间字段是带时区 ISO-8601 且晚于当前时间。"""
    value = _require_non_empty(tool_input, key)
    parsed = _parse_required_iso_datetime(value, key)
    if parsed <= _ensure_aware(reference_time):
        raise ValueError(f"{key} must be later than current time: {_format_datetime(reference_time)}")
    return _format_datetime(parsed)


def _parse_required_iso_datetime(value: str, key: str) -> datetime:
    """把模型传入的 ISO-8601 时间解析为带时区 datetime。"""
    text = value.replace("Z", "+00:00") if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{key} must be an ISO-8601 datetime") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{key} must include timezone offset")
    return parsed


def _ensure_aware(value: datetime) -> datetime:
    """把参考时间规范为带时区时间，便于与模型时间比较。"""
    if value.tzinfo is None or value.utcoffset() is None:
        return value.astimezone()
    return value


def _format_datetime(value: datetime) -> str:
    """统一输出秒级 ISO-8601 时间，避免任务文件出现多种精度。"""
    return _ensure_aware(value).isoformat(timespec="seconds")


def _normalize_csv(raw_value: str) -> tuple[str, ...]:
    """把逗号分隔字符串去重并转换为稳定元组。"""
    items: list[str] = []
    for item in raw_value.split(","):
        normalized = item.strip()
        if normalized and normalized not in items:
            items.append(normalized)
    return tuple(items)


def _build_execution_profile(
    run_mode: str,
    capabilities: tuple[str, ...],
    resolved_skills: tuple[str, ...],
    resolved_tools: tuple[str, ...],
) -> str:
    """根据任务形态和建议能力面生成稳定执行 profile 名。"""
    suffix = "selected" if capabilities or resolved_skills or resolved_tools else "default"
    if run_mode == "run_at":
        return f"background_scheduled_{suffix}"
    return f"background_async_{suffix}"


def _build_user_visible_summary(raw_summary: str, run_mode: str, scheduled_for: str) -> str:
    """确保定时任务摘要包含绝对执行时间，避免只保存相对表述。"""
    summary = raw_summary.replace("\n", " ").strip()
    if run_mode != "run_at" or not scheduled_for:
        return summary
    if scheduled_for in summary:
        return summary
    return f"计划执行时间：{scheduled_for}。{summary}"


def _build_resume_payload(
    goal: str,
    success_criteria: str,
    context_refs: tuple[str, ...],
) -> str:
    """把后台任务恢复所需的最小意图压缩为简单单行文本。"""
    goal_text = goal.replace("\n", " ").strip()
    success_text = success_criteria.replace("\n", " ").strip()
    context_text = ",".join(context_refs)
    return (
        f"goal={goal_text}; "
        f"success_criteria={success_text}; "
        f"context_refs={context_text}"
    )


def _build_decision_trace(
    title: str,
    goal: str,
    success_criteria: str,
    context_refs: tuple[str, ...],
    capabilities: tuple[str, ...],
    resolved_skills: tuple[str, ...],
    resolved_tools: tuple[str, ...],
) -> str:
    """把后台任务创建时的判断依据压缩为可读 JSON 文本。"""
    return json.dumps(
        {
            "title": title,
            "goal": goal,
            "success_criteria": success_criteria,
            "context_refs": list(context_refs),
            "requested_capabilities": list(capabilities),
            "resolved_skills": list(resolved_skills),
            "resolved_tools": list(resolved_tools),
        },
        ensure_ascii=False,
        indent=2,
    )


def _build_next_action(run_mode: str, scheduled_for: str) -> str:
    """根据任务模式生成下一步动作描述。"""
    if run_mode == "run_at":
        return f"等待调度器在 {scheduled_for} 到时后入后台执行队列。"
    return "等待后台 worker 继续执行该任务。"


def _build_last_result_summary(run_mode: str, scheduled_for: str) -> str:
    """为新创建的后台任务生成初始状态摘要。"""
    if run_mode == "run_at":
        return f"后台任务已创建，计划在 {scheduled_for} 执行。"
    return "后台任务已创建，等待后台 worker 拉起执行。"


def _relative_path(project_root: Path, path: Path) -> str:
    """把绝对路径转换为相对项目根目录的稳定展示路径。"""
    try:
        return str(path.relative_to(project_root))
    except ValueError:
        return str(path)


def _local_now() -> datetime:
    """返回当前本地时区时间，供定时任务入参做未来时间校验。"""
    return datetime.now().astimezone()


def _self_test() -> None:
    """验证后台任务入口服务可创建一条最小异步任务。"""
    from tempfile import TemporaryDirectory

    from dutyflow.agent.tools.registry import create_runtime_tool_registry

    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        skills_dir = root / "skills" / "alpha_skill"
        skills_dir.mkdir(parents=True, exist_ok=True)
        (skills_dir / "SKILL.md").write_text(
            "---\nname: alpha_skill\ndescription: alpha\n---\n\n# Alpha\n\nbody\n",
            encoding="utf-8",
        )
        service = BackgroundTaskIntakeService(
            root,
            create_runtime_tool_registry(),
            SkillRegistry(root / "skills"),
        )
        result = service.create_async_task(
            {
                "title": "同步资料",
                "goal": "整理资料",
                "success_criteria": "生成简要结论",
                "preferred_skills": "alpha_skill",
            }
        )
        assert result.status == "queued"
        assert result.resolved_skills == ("alpha_skill",)


if __name__ == "__main__":
    _self_test()
    print("dutyflow background task intake self-test passed")
