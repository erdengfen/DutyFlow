# 本文件负责后台 subagent 执行器，复用 AgentLoop 核心执行单个任务。

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys
from typing import TYPE_CHECKING

if __package__ in {None, ""}:
    _SRC_ROOT = Path(__file__).resolve().parents[2]
    if str(_SRC_ROOT) not in sys.path:
        sys.path.insert(0, str(_SRC_ROOT))

from dutyflow.agent.core_loop import AgentLoop, AgentLoopResult
from dutyflow.agent.model_client import ModelClient, ModelResponse
from dutyflow.agent.skills import SkillRegistry
from dutyflow.agent.state import AgentContentBlock
from dutyflow.agent.tools.registry import ToolRegistry, create_runtime_tool_registry
from dutyflow.tasks.task_state import TaskRecord

if TYPE_CHECKING:
    from dutyflow.logging.audit_log import AuditLogger


# 关键开关：写回任务状态表的结果摘要最多保留 500 字，完整结果后续由结果 Markdown 承载。
_RESULT_SUMMARY_MAX_CHARS = 500
_FORBIDDEN_BACKGROUND_TOOLS = frozenset(
    {
        "open_cli_session",
        "exec_cli_command",
        "close_cli_session",
    }
)


@dataclass(frozen=True)
class BackgroundSubagentResult:
    """表示后台 subagent 执行完单个任务后的系统可消费结果。"""

    status: str
    retry_status: str
    last_result_summary: str
    next_action: str
    user_visible_final_text: str
    stop_reason: str
    tool_result_count: int
    query_id: str


class BackgroundSubagentExecutor:
    """用独立 AgentLoop 执行后台任务，不直接绑定飞书感知和回信。"""

    def __init__(
        self,
        project_root: Path,
        model_client: ModelClient,
        *,
        registry: ToolRegistry | None = None,
        skill_registry: SkillRegistry | None = None,
        audit_logger: "AuditLogger | None" = None,
        max_turns: int = 30,
    ) -> None:
        """绑定后台执行所需的模型、工具注册表、技能注册表和工作目录。"""
        self.project_root = Path(project_root).resolve()
        self.model_client = model_client
        self.registry = registry or create_runtime_tool_registry()
        self.skill_registry = skill_registry or SkillRegistry(self.project_root / "skills")
        self.audit_logger = audit_logger
        # 关键开关：后台 subagent 单任务最多允许 30 轮工具续转，避免长任务无限循环。
        self.max_turns = max_turns

    def execute_task(self, task: TaskRecord) -> BackgroundSubagentResult:
        """执行一条任务记录，并返回 worker 可写回的状态结果。"""
        query_id = _build_query_id(task)
        loop = self._build_task_loop(task, query_id)
        if isinstance(loop, BackgroundSubagentResult):
            return loop
        result = loop.run_until_stop(_build_task_prompt(task), query_id=query_id)
        return _build_execution_result(query_id, result)

    def _build_task_loop(self, task: TaskRecord, query_id: str) -> AgentLoop | BackgroundSubagentResult:
        """按任务字段构造隔离的 AgentLoop；能力面非法时直接失败。"""
        try:
            registry = _select_task_tool_registry(task, self.registry)
            skill_registry = _select_task_skill_registry(task, self.skill_registry)
        except ValueError as exc:
            return _capability_error_result(query_id, str(exc))
        except KeyError as exc:
            return _capability_error_result(query_id, str(exc))
        return AgentLoop(
            self.model_client,
            registry,
            self.project_root,
            max_turns=self.max_turns,
            permission_mode="auto",
            approval_requester=None,
            audit_logger=self.audit_logger,
            skill_registry=skill_registry,
            system_prompt_preamble=_BACKGROUND_SUBAGENT_SYSTEM_PROMPT,
        )


def _build_query_id(task: TaskRecord) -> str:
    """根据任务 ID 构造后台 subagent 查询 ID。"""
    return f"bg_task_{task.task_id}"


def _build_task_prompt(task: TaskRecord) -> str:
    """把任务记录转换成后台 subagent 的单次用户输入。"""
    return "\n".join(
        (
            "你正在作为 DutyFlow 后台 subagent 执行一个已落盘的后台任务。",
            "只处理该任务范围内的事项；如果缺少权限、缺少上下文或需要审批，必须明确说明，不要伪装完成。",
            "最终输出必须是可直接发给用户的中文结果，语义清晰，不使用 Markdown。",
            "",
            f"task_id: {task.task_id}",
            f"title: {task.title}",
            f"status: {task.status}",
            f"run_mode: {task.run_mode}",
            f"scheduled_for: {task.scheduled_for}",
            f"execution_profile: {task.execution_profile}",
            f"requested_capabilities: {task.requested_capabilities}",
            f"resolved_skills: {task.resolved_skills}",
            f"resolved_tools: {task.resolved_tools}",
            f"resume_point: {task.resume_point}",
            f"resume_payload: {task.resume_payload}",
            "",
            "summary:",
            task.summary,
            "",
            "decision_trace:",
            task.decision_trace,
        )
    )


def _select_task_tool_registry(task: TaskRecord, base_registry: ToolRegistry) -> ToolRegistry:
    """根据任务 resolved_tools 构造后台 subagent 可见工具注册表。"""
    selected = ToolRegistry()
    for name in _split_csv(task.resolved_tools):
        if name in _FORBIDDEN_BACKGROUND_TOOLS:
            raise ValueError(f"forbidden background tool: {name}")
        if not base_registry.has(name):
            raise ValueError(f"unknown background tool: {name}")
        selected.register(base_registry.get(name), base_registry.get_handler(name))
    return selected


def _select_task_skill_registry(task: TaskRecord, base_registry: SkillRegistry) -> SkillRegistry:
    """根据任务 resolved_skills 构造后台 subagent 可见技能注册表。"""
    return base_registry.select(_split_csv(task.resolved_skills))


def _split_csv(raw_value: str) -> tuple[str, ...]:
    """解析任务 frontmatter 中的英文逗号分隔字段并去重。"""
    items: list[str] = []
    for item in raw_value.split(","):
        normalized = item.strip()
        if normalized and normalized not in items:
            items.append(normalized)
    return tuple(items)


def _build_execution_result(query_id: str, result: AgentLoopResult) -> BackgroundSubagentResult:
    """把 AgentLoopResult 转换成后台任务 worker 可消费的执行结果。"""
    final_text = result.final_text.strip()
    if result.stop_reason == "stop" and final_text:
        return _completed_result(query_id, result, final_text)
    if result.pending_restarts or not final_text:
        return _blocked_result(query_id, result)
    return _failed_result(query_id, result)


def _capability_error_result(query_id: str, message: str) -> BackgroundSubagentResult:
    """生成任务能力面构造失败结果，不启动模型调用。"""
    return BackgroundSubagentResult(
        status="failed",
        retry_status="failed",
        last_result_summary=f"后台 subagent 能力面构造失败：{message}",
        next_action="等待人工检查任务 resolved_tools / resolved_skills 后决定是否重试。",
        user_visible_final_text="",
        stop_reason="capability_resolution_failed",
        tool_result_count=0,
        query_id=query_id,
    )


def _completed_result(
    query_id: str,
    result: AgentLoopResult,
    final_text: str,
) -> BackgroundSubagentResult:
    """生成后台任务完成结果。"""
    return BackgroundSubagentResult(
        status="completed",
        retry_status="done",
        last_result_summary=_trim_summary(final_text),
        next_action="等待系统回推任务结果给用户。",
        user_visible_final_text=final_text,
        stop_reason=result.stop_reason,
        tool_result_count=result.tool_result_count,
        query_id=query_id,
    )


def _blocked_result(query_id: str, result: AgentLoopResult) -> BackgroundSubagentResult:
    """生成需要后续恢复或人工检查的阻塞结果。"""
    return BackgroundSubagentResult(
        status="blocked",
        retry_status="blocked",
        last_result_summary=_blocked_summary(result),
        next_action="等待后续恢复策略或人工检查后继续处理。",
        user_visible_final_text=result.final_text.strip(),
        stop_reason=result.stop_reason,
        tool_result_count=result.tool_result_count,
        query_id=query_id,
    )


def _failed_result(query_id: str, result: AgentLoopResult) -> BackgroundSubagentResult:
    """生成后台任务失败结果。"""
    return BackgroundSubagentResult(
        status="failed",
        retry_status="failed",
        last_result_summary=f"后台 subagent 执行失败：{result.stop_reason}",
        next_action="等待人工检查失败原因后决定是否重试。",
        user_visible_final_text=result.final_text.strip(),
        stop_reason=result.stop_reason,
        tool_result_count=result.tool_result_count,
        query_id=query_id,
    )


def _blocked_summary(result: AgentLoopResult) -> str:
    """生成阻塞状态的可读摘要。"""
    if result.pending_restarts:
        return f"后台 subagent 暂停，等待恢复：{result.pending_restarts[0].restart_action}"
    if not result.final_text.strip():
        return "后台 subagent 未生成可见结果，任务未标记完成。"
    return f"后台 subagent 暂停：{result.stop_reason}"


def _trim_summary(text: str) -> str:
    """压缩写入任务状态表的结果摘要。"""
    normalized = " ".join(text.split())
    if len(normalized) <= _RESULT_SUMMARY_MAX_CHARS:
        return normalized
    return normalized[: _RESULT_SUMMARY_MAX_CHARS - 3] + "..."


_BACKGROUND_SUBAGENT_SYSTEM_PROMPT = (
    "You are a DutyFlow background subagent. "
    "Execute exactly one persisted background task at a time. "
    "Use only the tools and skills exposed to this task. "
    "Do not claim completion when required context, permissions, or approvals are missing. "
    "Always respond in Chinese with a concise user-visible final result."
)


def _self_test() -> None:
    """验证后台 subagent executor 可复用 AgentLoop 完成最小任务。"""
    from tempfile import TemporaryDirectory

    from dutyflow.tasks.task_state import TaskStore

    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        task = TaskStore(root).create_task(
            title="self test background task",
            task_id="task_selftest",
            status="running",
            summary="执行自测任务。",
        )
        executor = BackgroundSubagentExecutor(root, _SelfTestModelClient())
        result = executor.execute_task(task)
    assert result.status == "completed"
    assert result.user_visible_final_text == "后台任务已完成。"


class _SelfTestModelClient:
    """为文件自测提供最小模型响应。"""

    def call_model(self, state, tools) -> ModelResponse:
        """返回固定完成文本。"""
        del state, tools
        return ModelResponse((AgentContentBlock(type="text", text="后台任务已完成。"),), "stop")


if __name__ == "__main__":
    _self_test()
    print("dutyflow background subagent executor self-test passed")
