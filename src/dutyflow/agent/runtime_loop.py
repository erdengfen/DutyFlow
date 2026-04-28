# 本文件负责正式 runtime worker 对感知记录的消费编排，复用现有 AgentLoop 与反馈接口。

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from dutyflow.agent.core_loop import AgentLoop, AgentLoopResult
from dutyflow.agent.model_client import ModelClient, OpenAICompatibleModelClient
from dutyflow.agent.runtime_service import RuntimeWorkItem
from dutyflow.agent.skills import SkillRegistry
from dutyflow.agent.tools.registry import ToolRegistry, create_runtime_tool_registry
from dutyflow.config.env import EnvConfig
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
            system_prompt_preamble=_RUNTIME_SYSTEM_PROMPT,
        )
        self.latest_result: RuntimeLoopExecutionResult | None = None

    def handle_work_item(self, work_item: RuntimeWorkItem) -> None:
        """消费一条 runtime work item，并把结果通过统一反馈接口发回会话。"""
        loop_input = self._require_loop_input(work_item.perception_id)
        loop_result = self.agent_loop.run_until_stop(
            _build_runtime_user_text(loop_input),
            query_id=work_item.work_id,
            tool_content=_build_runtime_tool_content(work_item, loop_input),
        )
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
        return _wrap_user_message_for_runtime(raw_text)
    content_preview = str(loop_input.get("content_preview", "")).strip()
    if content_preview:
        return _preview_fallback_text(loop_input, content_preview)
    return _empty_fallback_text(loop_input)


def _wrap_user_message_for_runtime(raw_text: str) -> str:
    """为正式 runtime 的用户消息补一层最小执行边界提示。"""
    return (
        "你正在处理一条实时飞书用户消息。优先直接回复用户；"
        "只有在确实需要补充身份、来源或联系人知识时才调用工具。\n"
        f"用户原始消息：{raw_text}"
    )


def _preview_fallback_text(loop_input: Mapping[str, Any], content_preview: str) -> str:
    """为非文本消息构造带预览内容的最小输入文本。"""
    message_type = str(loop_input.get("message_type", "")).strip() or "unknown"
    trigger_kind = str(loop_input.get("trigger_kind", "")).strip() or "unknown"
    return (
        "你正在处理一条实时飞书用户消息。优先直接回复用户；"
        "只有在确实需要补充身份、来源或联系人知识时才调用工具。\n"
        f"收到一条飞书 {trigger_kind} 消息，类型为 {message_type}，内容线索：{content_preview}"
    )


def _empty_fallback_text(loop_input: Mapping[str, Any]) -> str:
    """为没有文本也没有预览的消息构造兜底输入。"""
    message_type = str(loop_input.get("message_type", "")).strip() or "unknown"
    chat_type = str(loop_input.get("chat_type", "")).strip() or "unknown"
    return (
        "你正在处理一条实时飞书用户消息。优先直接回复用户；"
        "只有在确实需要补充身份、来源或联系人知识时才调用工具。\n"
        f"收到一条来自飞书 {chat_type} 会话的 {message_type} 消息，请结合当前工具链判断如何处理。"
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


_RUNTIME_SYSTEM_PROMPT = (
    "You are a personal assistant designed for workplace scenarios. "
    "Use the available skills and tools to infer and refine relationship context from the local knowledge base, "
    "then help the user handle work items or provide practical recommendations. "
    "Do not use Markdown in user-facing replies. "
    "Always respond in Chinese with clear meaning and well-structured logic."
)

_CLI_TOOL_NAMES = (
    "open_cli_session",
    "exec_cli_command",
    "close_cli_session",
)


def _self_test() -> None:
    """验证正式 runtime loop 可消费一条感知记录并走到统一反馈出口。"""
    from tempfile import TemporaryDirectory

    from dutyflow.agent.model_client import ModelResponse
    from dutyflow.agent.runtime_service import RuntimeLoopInput
    from dutyflow.agent.state import AgentContentBlock
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
