# 本文件负责工具注册、查找和最小输入校验，不负责工具执行。

from __future__ import annotations

import sys

_THIS_DIR = __file__.rsplit("/", 1)[0]
if sys.path and sys.path[0] == _THIS_DIR:
    sys.path.pop(0)

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from dutyflow.agent.tools.types import ToolCall, ToolResultEnvelope, ToolSpec
from dutyflow.agent.tools.logic.approval_tools.create_approval_request import CreateApprovalRequestTool
from dutyflow.agent.tools.logic.approval_tools.resume_after_approval import ResumeAfterApprovalTool
from dutyflow.agent.tools.logic.cli_tools.close_cli_session import CloseCliSessionTool
from dutyflow.agent.tools.logic.cli_tools.exec_cli_command import ExecCliCommandTool
from dutyflow.agent.tools.logic.cli_tools.open_cli_session import OpenCliSessionTool
from dutyflow.agent.tools.logic.contact_tools.add_contact_knowledge import AddContactKnowledgeTool
from dutyflow.agent.tools.logic.contact_tools.get_contact_knowledge_detail import GetContactKnowledgeDetailTool
from dutyflow.agent.tools.logic.contact_tools.search_contact_knowledge_headers import SearchContactKnowledgeHeadersTool
from dutyflow.agent.tools.logic.contact_tools.update_contact_knowledge import UpdateContactKnowledgeTool
from dutyflow.agent.tools.logic.identity_tools.lookup_contact_identity import LookupContactIdentityTool
from dutyflow.agent.tools.logic.identity_tools.lookup_responsibility_context import LookupResponsibilityContextTool
from dutyflow.agent.tools.logic.identity_tools.lookup_source_context import LookupSourceContextTool
from dutyflow.agent.tools.logic.skill_tools.create_skill import CreateSkillTool
from dutyflow.agent.tools.logic.skill_tools.load_skill import LoadSkillTool
from dutyflow.agent.tools.logic.task_tools.create_background_task import CreateBackgroundTaskTool
from dutyflow.agent.tools.logic.task_tools.schedule_background_task import ScheduleBackgroundTaskTool
from dutyflow.agent.tools.logic.feishu_tools.get_file_meta import FeishuGetFileMetaTool
from dutyflow.agent.tools.logic.feishu_tools.read_doc import FeishuReadDocTool
from dutyflow.agent.tools.logic.web_tools.web_fetch import WebFetchTool
from dutyflow.agent.tools.logic.web_tools.web_read_link import WebReadLinkTool
from dutyflow.agent.tools.logic.web_tools.web_search import WebSearchTool

if TYPE_CHECKING:
    from dutyflow.agent.tools.context import ToolUseContext

ToolHandler = Callable[[ToolCall, "ToolUseContext"], ToolResultEnvelope]

TOOL_REGISTRY = {
    FeishuGetFileMetaTool.name: FeishuGetFileMetaTool(),
    FeishuReadDocTool.name: FeishuReadDocTool(),
    AddContactKnowledgeTool.name: AddContactKnowledgeTool(),
    CreateApprovalRequestTool.name: CreateApprovalRequestTool(),
    CloseCliSessionTool.name: CloseCliSessionTool(),
    CreateBackgroundTaskTool.name: CreateBackgroundTaskTool(),
    CreateSkillTool.name: CreateSkillTool(),
    ExecCliCommandTool.name: ExecCliCommandTool(),
    GetContactKnowledgeDetailTool.name: GetContactKnowledgeDetailTool(),
    LoadSkillTool.name: LoadSkillTool(),
    LookupContactIdentityTool.name: LookupContactIdentityTool(),
    LookupResponsibilityContextTool.name: LookupResponsibilityContextTool(),
    LookupSourceContextTool.name: LookupSourceContextTool(),
    OpenCliSessionTool.name: OpenCliSessionTool(),
    ResumeAfterApprovalTool.name: ResumeAfterApprovalTool(),
    ScheduleBackgroundTaskTool.name: ScheduleBackgroundTaskTool(),
    SearchContactKnowledgeHeadersTool.name: SearchContactKnowledgeHeadersTool(),
    UpdateContactKnowledgeTool.name: UpdateContactKnowledgeTool(),
    WebFetchTool.name: WebFetchTool(),
    WebReadLinkTool.name: WebReadLinkTool(),
    WebSearchTool.name: WebSearchTool(),
}


@dataclass(frozen=True)
class RegisteredTool:
    """保存工具定义和可选 native handler。"""

    spec: ToolSpec
    handler: ToolHandler | None = None


class ToolRegistry:
    """维护工具注册表并提供基础输入校验。"""

    def __init__(self) -> None:
        """初始化空工具注册表。"""
        self._tools: dict[str, RegisteredTool] = {}

    def register(self, spec: ToolSpec, handler: ToolHandler | None = None) -> None:
        """注册工具定义，native 工具必须提供 handler。"""
        if spec.name in self._tools:
            raise ValueError(f"Tool already registered: {spec.name}")
        if spec.source == "native" and handler is None:
            raise ValueError("native tool requires handler")
        self._tools[spec.name] = RegisteredTool(spec=spec, handler=handler)

    def get(self, name: str) -> ToolSpec:
        """按名称获取工具定义。"""
        return self._require_registered(name).spec

    def get_handler(self, name: str) -> ToolHandler | None:
        """按名称获取工具 handler。"""
        return self._require_registered(name).handler

    def has(self, name: str) -> bool:
        """判断工具是否已注册。"""
        return name in self._tools

    def list_specs(self) -> tuple[ToolSpec, ...]:
        """返回按名称排序的工具定义列表。"""
        return tuple(self._tools[name].spec for name in sorted(self._tools))

    def validate_tool_input(self, tool_call: ToolCall) -> None:
        """按 ToolSpec 的 required 字段执行最小输入校验。"""
        spec = self.get(tool_call.tool_name)
        missing = [key for key in spec.required_inputs() if key not in tool_call.tool_input]
        if missing:
            raise ValueError("Missing required tool input: " + ", ".join(missing))

    def _require_registered(self, name: str) -> RegisteredTool:
        """获取已注册工具，不存在时给出明确错误。"""
        if name not in self._tools:
            raise KeyError(f"Tool is not registered: {name}")
        return self._tools[name]


def create_runtime_tool_registry() -> ToolRegistry:
    """根据 contract 层和 logic 层生成运行时工具注册表。"""
    registry = ToolRegistry()
    for tool in TOOL_REGISTRY.values():
        registry.register(
            ToolSpec.from_contract(
                tool.contract,
                is_concurrency_safe=tool.is_concurrency_safe,
                requires_approval=tool.requires_approval,
                timeout_seconds=float(getattr(tool, "timeout_seconds", 30.0)),
                max_retries=int(getattr(tool, "max_retries", 3)),
                retry_policy=str(getattr(tool, "retry_policy", "transient_only")),
                idempotency=str(getattr(tool, "idempotency", "read_only")),
                degradation_mode=str(getattr(tool, "degradation_mode", "none")),
                fallback_tool_names=tuple(getattr(tool, "fallback_tool_names", ())),
            ),
            tool.handle,
        )
    return registry


def _self_test() -> None:
    """验证注册表会拒绝重复工具名，并可生成运行时注册表。"""
    registry = ToolRegistry()
    spec = ToolSpec("placeholder_tool", "demo", source="placeholder")
    registry.register(spec)
    try:
        registry.register(spec)
    except ValueError:
        runtime_registry = create_runtime_tool_registry()
        assert runtime_registry.has("feishu_get_file_meta")
        assert runtime_registry.has("feishu_read_doc")
        assert runtime_registry.has("add_contact_knowledge")
        assert runtime_registry.has("create_approval_request")
        assert runtime_registry.has("close_cli_session")
        assert runtime_registry.has("create_background_task")
        assert runtime_registry.has("create_skill")
        assert runtime_registry.has("exec_cli_command")
        assert runtime_registry.has("get_contact_knowledge_detail")
        assert runtime_registry.has("load_skill")
        assert runtime_registry.has("lookup_contact_identity")
        assert runtime_registry.has("lookup_responsibility_context")
        assert runtime_registry.has("lookup_source_context")
        assert runtime_registry.has("open_cli_session")
        assert runtime_registry.has("resume_after_approval")
        assert runtime_registry.has("schedule_background_task")
        assert runtime_registry.has("search_contact_knowledge_headers")
        assert runtime_registry.has("update_contact_knowledge")
        return
    raise AssertionError("duplicate tool registration was not blocked")


if __name__ == "__main__":
    _self_test()
    print("dutyflow tool registry self-test passed")
