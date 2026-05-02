# 本文件负责正式 runtime worker 对感知记录的消费编排，复用现有 AgentLoop 与反馈接口。

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from dutyflow.agent.core_loop import AgentLoop, AgentLoopResult
from dutyflow.agent.model_client import ModelClient, OpenAICompatibleModelClient
from dutyflow.agent.runtime_service import RuntimeWorkItem
from dutyflow.agent.skills import SkillRegistry
from dutyflow.agent.state import AgentMessage, AgentState
from dutyflow.agent.tools.registry import ToolRegistry, create_runtime_tool_registry
from dutyflow.config.env import EnvConfig
from dutyflow.config.prompt_config import get_main_agent_system_prompt
from dutyflow.feedback.gateway import FeedbackGateway, FeedbackResult
from dutyflow.logging.audit_log import AuditLogger
from dutyflow.perception.store import PerceptionRecordService


@dataclass(frozen=True)
class RuntimeLoopExecutionResult:
    """表示正式 runtime loop 消费一条感知记录后的最小结果。"""

    work_id: str
    perception_id: str
    query_id: str
    stop_reason: str
    final_text: str
    tool_result_count: int
    feedback_status: str
    feedback_ok: bool


class RuntimeAgentLoop:
    """把感知记录接到现有 AgentLoop 和统一反馈出口的正式 runtime 包装层。"""

    def __init__(
        self,
        project_root: Path,
        config: EnvConfig,
        *,
        model_client: ModelClient | None = None,
        registry: ToolRegistry | None = None,
        feedback_gateway: FeedbackGateway | None = None,
        perception_service: PerceptionRecordService | None = None,
        audit_logger: AuditLogger | None = None,
        skill_registry: SkillRegistry | None = None,
    ) -> None:
        """绑定正式 runtime loop 所需的模型、工具、感知和回馈依赖。"""
        self.project_root = Path(project_root).resolve()
        self.config = config
        self.feedback_gateway = feedback_gateway or FeedbackGateway(config)
        self.perception_service = perception_service or PerceptionRecordService(self.project_root)
        self.agent_loop = AgentLoop(
            model_client or OpenAICompatibleModelClient(config),
            registry or _build_formal_runtime_registry(),
            self.project_root,
            permission_mode="auto",
            approval_requester=None,
            audit_logger=audit_logger,
            skill_registry=skill_registry or SkillRegistry(self.project_root / "skills"),
            system_prompt_preamble=get_main_agent_system_prompt(),
        )
        self.latest_result: RuntimeLoopExecutionResult | None = None
        self.latest_agent_loop_result: AgentLoopResult | None = None

    def handle_work_item(self, work_item: RuntimeWorkItem) -> None:
        """消费一条 runtime work item，并把结果通过统一反馈接口发回会话。"""
        loop_input = self._require_loop_input(work_item.perception_id)
        loop_result = self.agent_loop.run_until_stop(
            _build_runtime_user_text(loop_input),
            query_id=work_item.work_id,
            tool_content=_build_runtime_tool_content(work_item, loop_input),
        )
        self.latest_agent_loop_result = loop_result
        feedback = self._send_feedback(loop_input, loop_result)
        self.latest_result = RuntimeLoopExecutionResult(
            work_id=work_item.work_id,
            perception_id=work_item.perception_id,
            query_id=work_item.work_id,
            stop_reason=loop_result.stop_reason,
            final_text=loop_result.final_text,
            tool_result_count=loop_result.tool_result_count,
            feedback_status=feedback.status,
            feedback_ok=feedback.ok,
        )

    def build_agent_state_debug_payload(self) -> dict[str, Any]:
        """返回最近一次正式 runtime AgentState 的只读调试视图。"""
        if self.latest_agent_loop_result is None:
            return {
                "status": "empty",
                "action": "no_agent_state",
                "detail": "formal runtime has not completed an agent loop yet",
                "payload": {},
            }
        loop_result = self.latest_agent_loop_result
        state = loop_result.state
        projected_messages = _project_messages_for_debug(self.agent_loop, state)
        budget = self.agent_loop.runtime_context_manager.estimate_budget(projected_messages)
        phase_summary = _phase_summary_debug(self.agent_loop.runtime_context_manager)
        compression_journal = _compression_journal_debug(self.agent_loop.runtime_context_manager)
        health_check = _health_check_debug(self.agent_loop.runtime_context_manager)
        return {
            "status": "ok",
            "action": "agent_state",
            "detail": "latest formal runtime agent state debug view",
            "payload": _agent_state_debug_payload(
                state,
                loop_result,
                projected_messages,
                budget.to_dict(),
                phase_summary,
                compression_journal,
                health_check,
            ),
        }

    def _require_loop_input(self, perception_id: str) -> dict[str, Any]:
        """按 perception_id 读取正式 loop 所需的标准输入。"""
        loop_input = self.perception_service.build_loop_input(record_id=perception_id)
        if loop_input is None:
            raise RuntimeError(f"perception record not found: {perception_id}")
        return loop_input

    def _send_feedback(
        self,
        loop_input: Mapping[str, Any],
        loop_result: AgentLoopResult,
    ) -> FeedbackResult:
        """根据 loop 结果向当前会话发送最终文本或最小状态更新。"""
        chat_id = str(loop_input.get("chat_id", "")).strip()
        if not chat_id:
            return FeedbackResult(
                ok=False,
                status="missing_chat_id",
                detail="perception loop input missing chat_id",
            )
        final_text = loop_result.final_text.strip()
        if final_text:
            return self.feedback_gateway.send_text(chat_id, final_text)
        return self.feedback_gateway.send_status_update(chat_id, "处理中", _build_empty_reply_summary(loop_result))


def _build_runtime_user_text(loop_input: Mapping[str, Any]) -> str:
    """把感知记录转换为正式 AgentLoop 的最小用户输入。"""
    raw_text = str(loop_input.get("raw_text", "")).strip()
    if raw_text:
        return _wrap_user_message_for_runtime(raw_text, loop_input)
    content_preview = str(loop_input.get("content_preview", "")).strip()
    if content_preview:
        return _preview_fallback_text(loop_input, content_preview)
    return _empty_fallback_text(loop_input)


def _wrap_user_message_for_runtime(raw_text: str, loop_input: Mapping[str, Any]) -> str:
    """为正式 runtime 的用户消息补一层最小执行边界提示。"""
    return (
        "你正在处理一条实时飞书用户消息。优先直接回复用户；"
        "只有在确实需要补充身份、来源、联系人知识、创建后台任务或发起审批时才调用工具。\n"
        f"{_build_time_context(loop_input)}\n"
        f"用户原始消息：{raw_text}"
    )


def _preview_fallback_text(loop_input: Mapping[str, Any], content_preview: str) -> str:
    """为非文本消息构造带预览内容的最小输入文本。"""
    message_type = str(loop_input.get("message_type", "")).strip() or "unknown"
    trigger_kind = str(loop_input.get("trigger_kind", "")).strip() or "unknown"
    return (
        "你正在处理一条实时飞书用户消息。优先直接回复用户；"
        "只有在确实需要补充身份、来源、联系人知识、创建后台任务或发起审批时才调用工具。\n"
        f"{_build_time_context(loop_input)}\n"
        f"收到一条飞书 {trigger_kind} 消息，类型为 {message_type}，内容线索：{content_preview}"
    )


def _empty_fallback_text(loop_input: Mapping[str, Any]) -> str:
    """为没有文本也没有预览的消息构造兜底输入。"""
    message_type = str(loop_input.get("message_type", "")).strip() or "unknown"
    chat_type = str(loop_input.get("chat_type", "")).strip() or "unknown"
    return (
        "你正在处理一条实时飞书用户消息。优先直接回复用户；"
        "只有在确实需要补充身份、来源、联系人知识、创建后台任务或发起审批时才调用工具。\n"
        f"{_build_time_context(loop_input)}\n"
        f"收到一条来自飞书 {chat_type} 会话的 {message_type} 消息，请结合当前工具链判断如何处理。"
    )


def _build_time_context(loop_input: Mapping[str, Any]) -> str:
    """为模型解析相对时间提供稳定锚点。"""
    received_at = str(loop_input.get("received_at", "")).strip() or "unknown"
    return (
        f"消息接收时间：{received_at}。"
        f"当前本地系统时间：{_now_iso()}。"
        "解析“明天”“稍后”“N 分钟后”等相对时间时，必须基于消息接收时间生成带时区的 ISO-8601 绝对时间；"
        "不得生成过去时间。"
    )


def _build_runtime_tool_content(
    work_item: RuntimeWorkItem,
    loop_input: Mapping[str, Any],
) -> dict[str, Any]:
    """把正式 runtime 的工作上下文挂入现有 AgentLoop 工具上下文。"""
    return {
        "perception": dict(loop_input),
        "runtime": {
            "work_id": work_item.work_id,
            "perception_id": work_item.perception_id,
            "trigger_kind": work_item.loop_input.trigger_kind,
        },
    }


def _build_formal_runtime_registry() -> ToolRegistry:
    """为正式 runtime 构造默认工具集，仅排除 CLI tools。"""
    base_registry = create_runtime_tool_registry()
    registry = ToolRegistry()
    for spec in base_registry.list_specs():
        if spec.name in _CLI_TOOL_NAMES:
            continue
        registry.register(spec, base_registry.get_handler(spec.name))
    return registry


def _build_empty_reply_summary(loop_result: AgentLoopResult) -> str:
    """为没有直接回复文本的情况生成稳定状态说明。"""
    if loop_result.stop_reason == "max_turns_reached":
        return "已收到消息，但当前处理轮数已达上限，后续将继续收口正式任务与恢复逻辑。"
    if loop_result.stop_reason == "context_overflow":
        return "已收到消息，但当前上下文过长，后续需要进入正式的上下文压缩与恢复链。"
    return "已收到消息，当前未生成直接回复文本。"


_CLI_TOOL_NAMES = (
    "open_cli_session",
    "exec_cli_command",
    "close_cli_session",
)

# 关键开关：CLI 查看 AgentState 时每个 block 只展示 600 字，避免调试输出再次膨胀。
STATE_DEBUG_BLOCK_PREVIEW_CHARS = 600


def _project_messages_for_debug(agent_loop: AgentLoop, state: AgentState) -> tuple[AgentMessage, ...]:
    """构造调试用投影 messages，不写回 canonical AgentState。"""
    manager = agent_loop.runtime_context_manager
    working_set = manager.build_working_set(state)
    return manager.project_messages(state, working_set=working_set)


def _agent_state_debug_payload(
    state: AgentState,
    loop_result: AgentLoopResult,
    projected_messages: tuple[AgentMessage, ...],
    budget_report: Mapping[str, Any],
    phase_summary: Mapping[str, Any],
    compression_journal: Mapping[str, Any],
    health_check: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """组装正式 runtime AgentState 的调试 payload。"""
    return {
        "state": _state_debug_summary(state),
        "latest_result": _loop_result_debug(loop_result),
        "compression": _compression_debug(state.messages, projected_messages),
        "phase_summary": dict(phase_summary),
        "compression_journal": dict(compression_journal),
        "health_check": dict(health_check) if health_check else {},
        "budget_report": budget_report,
        "messages": {
            "canonical": _messages_debug(state.messages),
            "projected": _messages_debug(projected_messages),
        },
    }


def _phase_summary_debug(manager) -> dict[str, Any]:
    """返回最近一次阶段摘要触发与落盘状态。"""
    trigger = getattr(manager, "latest_phase_summary_trigger", None)
    record = getattr(manager, "latest_phase_summary_record", None)
    return {
        "trigger": trigger.to_dict() if trigger is not None else {},
        "record": record.to_dict() if record is not None else {},
        "error": getattr(manager, "latest_phase_summary_error", ""),
    }


def _compression_journal_debug(manager) -> dict[str, Any]:
    """返回最近一次 Compression Journal 写入状态。"""
    record = getattr(manager, "latest_compression_journal_record", None)
    return {
        "record": record.to_dict() if record is not None else {},
        "error": getattr(manager, "latest_compression_journal_error", ""),
    }


def _health_check_debug(manager) -> dict[str, Any]:
    """返回最近一次 Context Health Check 结果。"""
    health = getattr(manager, "latest_health_check", None)
    if health is None:
        return {"passed": None, "failed_checks": [], "checks": []}
    return health.to_dict()


def _state_debug_summary(state: AgentState) -> dict[str, Any]:
    """返回 AgentState 的控制面摘要。"""
    return {
        "query_id": state.query_id,
        "turn_count": state.turn_count,
        "transition_reason": state.transition_reason,
        "current_event_id": state.current_event_id,
        "current_task_id": state.current_task_id,
        "pending_tool_use_ids": list(state.pending_tool_use_ids),
        "last_tool_result_ids": list(state.last_tool_result_ids),
        "message_count": len(state.messages),
        "created_at": state.created_at,
        "updated_at": state.updated_at,
    }


def _loop_result_debug(loop_result: AgentLoopResult) -> dict[str, Any]:
    """返回最近一次 AgentLoopResult 的简要调试信息。"""
    return {
        "final_text": loop_result.final_text,
        "stop_reason": loop_result.stop_reason,
        "turn_count": loop_result.turn_count,
        "tool_result_count": loop_result.tool_result_count,
        "pending_restart_count": len(loop_result.pending_restarts),
    }


def _compression_debug(
    canonical_messages: tuple[AgentMessage, ...],
    projected_messages: tuple[AgentMessage, ...],
) -> dict[str, Any]:
    """返回 canonical 与 projected messages 的压缩差异摘要。"""
    return {
        "projected_changed": projected_messages != canonical_messages,
        "canonical_message_count": len(canonical_messages),
        "projected_message_count": len(projected_messages),
        "canonical_tool_result_count": _tool_result_count(canonical_messages),
        "projected_tool_receipt_count": _tool_receipt_count(projected_messages),
        "projected_active_tool_result_count": _active_tool_result_count(projected_messages),
    }


def _messages_debug(messages: tuple[AgentMessage, ...]) -> list[dict[str, Any]]:
    """把 messages 转为可读调试列表。"""
    return [_message_debug(index, message) for index, message in enumerate(messages)]


def _message_debug(index: int, message: AgentMessage) -> dict[str, Any]:
    """把单条 AgentMessage 转为可读调试字典。"""
    return {
        "index": index,
        "role": message.role,
        "block_count": len(message.content),
        "content": [_block_debug(block_index, block) for block_index, block in enumerate(message.content)],
    }


def _block_debug(index: int, block) -> dict[str, Any]:
    """把单个 content block 转为可读调试字典。"""
    content = _block_debug_content(block)
    return {
        "index": index,
        "type": block.type,
        "tool_use_id": block.tool_use_id,
        "tool_name": block.tool_name,
        "is_error": block.is_error,
        "content_chars": len(content),
        "content_preview": _preview(content, STATE_DEBUG_BLOCK_PREVIEW_CHARS),
    }


def _block_debug_content(block) -> str:
    """提取 block 在调试视图中的主要内容。"""
    if block.type == "text":
        return block.text
    if block.type == "tool_result":
        return block.content
    if block.type == "tool_use":
        return json.dumps(dict(block.tool_input), ensure_ascii=False, sort_keys=True)
    return block.text or block.content


def _tool_result_count(messages: tuple[AgentMessage, ...]) -> int:
    """统计 messages 中 tool_result block 数量。"""
    return sum(1 for message in messages for block in message.content if block.type == "tool_result")


def _tool_receipt_count(messages: tuple[AgentMessage, ...]) -> int:
    """统计 projected messages 中 ToolReceipt 数量。"""
    return sum(1 for message in messages for block in message.content if _is_tool_receipt_block(block))


def _active_tool_result_count(messages: tuple[AgentMessage, ...]) -> int:
    """统计 projected messages 中仍保留原文的 tool_result 数量。"""
    return sum(
        1 for message in messages for block in message.content if block.type == "tool_result" and not _is_tool_receipt_block(block)
    )


def _is_tool_receipt_block(block) -> bool:
    """判断 block 是否是已收据化的工具结果。"""
    return block.type == "tool_result" and str(block.content).strip().startswith("ToolReceipt(")


def _preview(text: str, max_chars: int) -> str:
    """返回单行预览文本。"""
    normalized = " ".join(str(text).split())
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max_chars - 3] + "..."


def _now_iso() -> str:
    """返回当前本地时区时间，供正式 runtime 注入时间锚点。"""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _self_test() -> None:
    """验证正式 runtime loop 可消费一条感知记录并走到统一反馈出口。"""
    from tempfile import TemporaryDirectory

    from dutyflow.agent.runtime_service import RuntimeLoopInput
    from dutyflow.feishu.events import FeishuEventAdapter

    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        env_path = root / ".env"
        env_path.write_text(
            (
                "DUTYFLOW_MODEL_API_KEY=demo-key\n"
                "DUTYFLOW_MODEL_BASE_URL=https://example.invalid/model\n"
                "DUTYFLOW_MODEL_NAME=demo-model\n"
                "DUTYFLOW_FEISHU_APP_ID=app_demo\n"
                "DUTYFLOW_FEISHU_APP_SECRET=secret_demo\n"
                "DUTYFLOW_FEISHU_EVENT_VERIFY_TOKEN=verify_demo\n"
                "DUTYFLOW_FEISHU_EVENT_ENCRYPT_KEY=encrypt_demo\n"
                "DUTYFLOW_FEISHU_EVENT_MODE=fixture\n"
                "DUTYFLOW_FEISHU_TENANT_KEY=tenant_demo\n"
                "DUTYFLOW_FEISHU_OWNER_OPEN_ID=ou_owner\n"
                "DUTYFLOW_FEISHU_OWNER_REPORT_CHAT_ID=oc_owner\n"
                "DUTYFLOW_DATA_DIR=data\n"
                "DUTYFLOW_LOG_DIR=data/logs\n"
            ),
            encoding="utf-8",
        )
        from dutyflow.config.env import load_env_config

        config = load_env_config(root)
        perception = PerceptionRecordService(root)
        envelope = FeishuEventAdapter().build_event_envelope(
            FeishuEventAdapter().create_local_fixture_event("hello", message_id="msg_runtime")
        )
        record = perception.create_record(envelope, root / "data" / "events" / "evt_msg_runtime.md")
        loop = RuntimeAgentLoop(
            root,
            config,
            model_client=_SelfTestModelClient(),
            registry=ToolRegistry(),
            feedback_gateway=_SelfTestFeedbackGateway(),
            perception_service=perception,
        )
        work_item = RuntimeWorkItem(
            work_id="run_self_test",
            perception_id=record.record_id,
            enqueued_at="2026-04-28T00:00:00+00:00",
            loop_input=RuntimeLoopInput(
                perception_id=record.record_id,
                perception_file=str(record.path),
                trigger_kind=record.trigger_kind,
                payload=record.to_loop_input(),
            ),
        )
        loop.handle_work_item(work_item)
        assert loop.latest_result is not None
        assert loop.latest_result.final_text == "ok"


class _SelfTestModelClient:
    """为 runtime loop 自测提供最小文本响应。"""

    def call_model(self, state, tools) -> object:
        """返回固定文本响应，避免触发真实模型调用。"""
        from dutyflow.agent.model_client import ModelResponse
        from dutyflow.agent.state import AgentContentBlock

        del state, tools
        return ModelResponse((AgentContentBlock(type="text", text="ok"),), "stop")


class _SelfTestFeedbackGateway:
    """为 runtime loop 自测提供不触发真实发送的回馈网关。"""

    def send_text(self, chat_id: str, text: str) -> FeedbackResult:
        return FeedbackResult(ok=True, status="sent", detail="self-test", payload={"chat_id": chat_id, "text": text})

    def send_status_update(self, chat_id: str, title: str, summary: str) -> FeedbackResult:
        return FeedbackResult(
            ok=True,
            status="sent",
            detail="self-test",
            payload={"chat_id": chat_id, "title": title, "summary": summary},
        )


if __name__ == "__main__":
    _self_test()
    print("dutyflow runtime loop self-test passed")
