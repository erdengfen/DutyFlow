# 本文件负责 DutyFlow 本地单进程应用的启动、生命周期编排和健康检查。

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in {None, ""}:
    _SCRIPT_DIR = Path(__file__).resolve().parent
    _SRC_ROOT = _SCRIPT_DIR.parent
    if sys.path and Path(sys.path[0]).resolve() == _SCRIPT_DIR:
        sys.path.pop(0)
    if str(_SRC_ROOT) not in sys.path:
        sys.path.insert(0, str(_SRC_ROOT))

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timezone
import os
from typing import Any, Mapping, Sequence

from dutyflow.agent.background_task_worker import BackgroundTaskWorker
from dutyflow.agent.control_state_store import AgentControlStateStore
from dutyflow.agent.debug_chat_service import ChatDebugService, ChatDebugTask
from dutyflow.agent.core_loop import AgentLoop, ChatDebugSession
from dutyflow.agent.runtime_service import RuntimeService
from dutyflow.agent.runtime_loop import RuntimeAgentLoop
from dutyflow.agent.model_client import OpenAICompatibleModelClient
from dutyflow.agent.skills import SkillRegistry
from dutyflow.cli.main import CliConsole
from dutyflow.config.env import load_env_config
from dutyflow.feishu.collectors.direct_message_collector import DirectMessageCollector
from dutyflow.feishu.oauth import FeishuOAuthManager
from dutyflow.feishu.runtime import FeishuIngressService
from dutyflow.feishu.scope_registry import (
    DIRECT_MESSAGE_COLLECTOR,
    FeishuScopeRecord,
    FeishuScopeRegistry,
    seed_owner_p2p_scope,
    scope_account_id_from_config,
)
from dutyflow.feishu.user_client import FeishuUserClient
from dutyflow.feedback.gateway import FeedbackGateway
from dutyflow.logging.audit_log import AuditLogger, build_audit_preview
from dutyflow.storage.file_store import FileStore
from dutyflow.storage.markdown_store import MarkdownStore
from dutyflow.agent.tools.registry import create_runtime_tool_registry
from dutyflow.tasks.task_scheduler import TaskDispatchItem, TaskSchedulerService
from dutyflow.tasks.task_state import TaskStore

# 关键开关：CLI 私信 collector 调试默认回拉最近 3600 秒，避免一次命令误扫过大历史窗口。
DEFAULT_DM_COLLECT_LOOKBACK_SECONDS = 3600
# 关键开关：CLI 私信 collector 调试最大允许回拉 604800 秒，即 7 天，避免人工误输导致大范围拉取。
MAX_DM_COLLECT_LOOKBACK_SECONDS = 604800


@dataclass
class HealthStatus:
    """表示 Step 1 阶段可验证的应用健康状态。"""

    status: str
    app_entry: str
    cli_entry: str
    data_dir_exists: bool
    skills_dir_exists: bool
    test_dir_exists: bool
    agent_control_state_exists: bool
    log_dir_exists: bool

    def to_text(self) -> str:
        """将健康状态转换为 CLI 可读文本。"""
        return (
            f"status={self.status}\n"
            f"app_entry={self.app_entry}\n"
            f"cli_entry={self.cli_entry}\n"
            f"data_dir_exists={self.data_dir_exists}\n"
            f"skills_dir_exists={self.skills_dir_exists}\n"
            f"test_dir_exists={self.test_dir_exists}\n"
            f"agent_control_state_exists={self.agent_control_state_exists}\n"
            f"log_dir_exists={self.log_dir_exists}"
        )


class DutyFlowApp:
    """编排 DutyFlow 本地 Demo 应用的生命周期。"""

    def __init__(self, project_root: Path | None = None) -> None:
        """初始化应用根目录和 CLI 控制台。"""
        self.project_root = project_root or Path.cwd()
        self.cli = CliConsole(self)
        self._chat_debug_service: ChatDebugService | None = None
        self._persistent_chat_session: ChatDebugSession | None = None
        self._feishu_ingress_service: FeishuIngressService | None = None
        self._runtime_service: RuntimeService | None = None
        self._runtime_loop: RuntimeAgentLoop | None = None
        self._background_task_worker: BackgroundTaskWorker | None = None
        self._task_scheduler_service: TaskSchedulerService | None = None

    def health_check(self) -> HealthStatus:
        """返回 Step 1 可验证的占位健康检查结果。"""
        self._ensure_runtime_layout()
        data_dir = self.project_root / "data"
        return HealthStatus(
            status="ok",
            app_entry="src/dutyflow/app.py",
            cli_entry="src/dutyflow/cli/main.py",
            data_dir_exists=data_dir.exists(),
            skills_dir_exists=(self.project_root / "skills").exists(),
            test_dir_exists=(self.project_root / "test").exists(),
            agent_control_state_exists=(
                data_dir / "state" / "agent_control_state.md"
            ).exists(),
            log_dir_exists=(data_dir / "logs").exists(),
        )

    def _ensure_runtime_layout(self) -> None:
        """初始化 Step 1 所需的数据目录、Agent 运行状态快照和日志。"""
        config = load_env_config(self.project_root)
        file_store = FileStore(self.project_root)
        markdown_store = MarkdownStore(file_store)
        self._ensure_data_dirs(file_store, config.data_dir)
        self._ensure_agent_control_state(markdown_store, config)
        AuditLogger(markdown_store, config.log_dir).record(
            event_type="health_check",
            note="Step 1 health check initialized runtime layout.",
        )

    def _ensure_data_dirs(self, store: FileStore, data_dir: Path) -> None:
        """创建 Demo 期本地运行所需的基础数据目录。"""
        for relative in (
            data_dir,
            data_dir / "state",
            data_dir / "logs",
            data_dir / "events",
            data_dir / "perception",
            data_dir / "contexts",
            data_dir / "approvals" / "pending",
            data_dir / "approvals" / "completed",
            data_dir / "tasks",
            data_dir / "reports",
            data_dir / "plans",
        ):
            store.ensure_dir(relative)

    def _ensure_agent_control_state(self, store: MarkdownStore, config) -> None:
        """刷新 Agent 控制快照，便于 CLI 和人工检查当前任务控制面。"""
        AgentControlStateStore(
            self.project_root,
            markdown_store=store,
            data_dir=config.data_dir,
        ).sync(
            current_model=config.model_name,
            permission_mode=config.permission_mode,
        )

    def run_chat_debug(self, user_text: str) -> str:
        """运行 CLI /chat 调试链路并返回完整可见结果。"""
        if not user_text.strip():
            return _chat_error("empty_input", "usage: /chat 用户输入")
        try:
            result = self.create_chat_debug_session().run_turn(user_text)
        except Exception as exc:  # noqa: BLE001
            return _chat_error("chat_failed", str(exc))
        return result.to_debug_text()

    def submit_chat_debug_task(self, user_text: str) -> str:
        """以非阻塞方式提交一条 /chat 调试任务。"""
        clean_text = user_text.strip()
        if not clean_text:
            return _chat_debug_payload(
                status="error",
                action="empty_input",
                detail="usage: /chat run 用户输入",
            )
        service = self._get_or_create_chat_debug_service()
        service.start()
        task = service.enqueue(clean_text)
        return _chat_debug_payload(
            status="ok",
            action="accepted",
            detail="chat debug task accepted",
            payload={
                "task_id": task.task_id,
                "user_text": task.user_text,
                "enqueued_at": task.enqueued_at,
            },
        )

    def get_chat_debug_status(self) -> str:
        """返回当前非阻塞 /chat 调试服务的状态。"""
        if self._chat_debug_service is None:
            return _chat_debug_payload(
                status="empty",
                action="no_worker",
                detail="chat debug worker has not started yet",
            )
        state = self._chat_debug_service.get_state()
        return _chat_debug_payload(
            status="ok",
            action="worker_status",
            detail="chat debug worker status",
            payload={
                "status": state.status,
                "worker_started": state.worker_started,
                "worker_alive": state.worker_alive,
                "queue_size": state.queue_size,
                "accepted_count": state.accepted_count,
                "processed_count": state.processed_count,
                "failed_count": state.failed_count,
                "latest_task_id": state.latest_task_id,
                "latest_action": state.latest_action,
                "latest_error": state.latest_error,
                "updated_at": state.updated_at,
            },
        )

    def get_latest_chat_debug(self) -> str:
        """返回最近一条 /chat 调试任务结果。"""
        if self._chat_debug_service is None:
            return _chat_debug_payload(
                status="empty",
                action="no_result",
                detail="no chat debug task has been submitted yet",
            )
        result = self._chat_debug_service.get_latest_result()
        if result is None:
            return _chat_debug_payload(
                status="empty",
                action="no_result",
                detail="no chat debug task has finished yet",
            )
        payload = {
            "task_id": result.task_id,
            "user_text": result.user_text,
            "task_status": result.task_status,
            "completed_at": result.completed_at,
        }
        if result.task_status == "completed":
            payload["result_text"] = result.result_text
        else:
            payload["error_text"] = result.error_text
        return _chat_debug_payload(
            status="ok" if result.task_status == "completed" else "error",
            action=result.task_status,
            detail="latest chat debug task result",
            payload=payload,
        )

    def get_agent_state_debug(self) -> str:
        """返回最近一次正式 runtime AgentState 和上下文预算调试视图。"""
        if self._runtime_loop is None:
            payload = {
                "status": "empty",
                "action": "no_runtime_loop",
                "detail": "formal runtime loop has not started yet",
                "payload": {},
            }
            return json.dumps(payload, ensure_ascii=False, indent=2)
        return json.dumps(
            self._runtime_loop.build_agent_state_debug_payload(),
            ensure_ascii=False,
            indent=2,
        )

    def run_feishu_fixture_debug(self, user_text: str) -> str:
        """使用本地 fixture 事件验证 Step 5 接入链路。"""
        if not user_text.strip():
            return _feishu_error("empty_input", "usage: /feishu fixture 文本")
        service = self._get_or_create_feishu_ingress_service()
        raw_event = service.adapter.create_local_fixture_event(user_text)
        result = service.handle_raw_event(raw_event)
        return _feishu_debug_payload(
            status="ok",
            action=result.action,
            event_id=result.event_id,
            message_id=result.message_id,
            record_path=result.record_path,
            detail=result.detail,
            payload=result.payload,
        )

    def run_feishu_dm_debug(self, arg_text: str) -> str:
        """用 owner 用户身份运行 direct_message_collector 的本地调试入口。"""
        config = load_env_config(self.project_root)
        registry = FeishuScopeRegistry(self.project_root)
        default_chat_id = _default_dm_chat_id(arg_text, config, registry)
        parsed = _parse_dm_debug_args(arg_text, default_chat_id)
        if parsed.error_kind:
            return _feishu_error(parsed.error_kind, parsed.detail)
        try:
            oauth_manager = FeishuOAuthManager(config, self.project_root)
            user_client = FeishuUserClient.from_oauth_manager(
                oauth_manager,
                audit_logger=self._create_audit_logger(config),
                raw_response_enabled=True,
            )
            result = DirectMessageCollector(self.project_root, user_client).collect(
                parsed.chat_id,
                start_time=parsed.start_time,
                end_time=parsed.end_time,
                save_raw=True,
            )
        except Exception as exc:  # noqa: BLE001
            return _feishu_error("dm_collect_failed", str(exc))
        return _dm_collect_debug_payload(result, parsed)

    def run_feishu_scopes_debug(self, arg_text: str) -> str:
        """查看飞书 Scope Registry 当前记录。"""
        config = load_env_config(self.project_root)
        registry = FeishuScopeRegistry(self.project_root)
        seed_owner_p2p_scope(registry, config)
        status = "candidate" if arg_text.strip() == "candidates" else ""
        records = registry.list_records(account_id=scope_account_id_from_config(config), status=status)
        return _feishu_scopes_payload("scopes", records, self.project_root)

    def approve_feishu_scope_debug(self, identifier: str) -> str:
        """批准并启用一个飞书 scope。"""
        config = load_env_config(self.project_root)
        registry = FeishuScopeRegistry(self.project_root)
        seed_owner_p2p_scope(registry, config)
        record = _resolve_single_scope(registry, identifier)
        if isinstance(record, str):
            return _feishu_error("scope_not_found", record)
        registry.approve_scope(record.account_id, record.scope_type, record.scope_id)
        enabled = registry.enable_scope(record.account_id, record.scope_type, record.scope_id)
        return _feishu_scopes_payload("scope_approved", (enabled,), self.project_root)

    def disable_feishu_scope_debug(self, identifier: str) -> str:
        """禁用一个飞书 scope。"""
        config = load_env_config(self.project_root)
        registry = FeishuScopeRegistry(self.project_root)
        seed_owner_p2p_scope(registry, config)
        record = _resolve_single_scope(registry, identifier)
        if isinstance(record, str):
            return _feishu_error("scope_not_found", record)
        disabled = registry.disable_scope(record.account_id, record.scope_type, record.scope_id, reason="manual")
        return _feishu_scopes_payload("scope_disabled", (disabled,), self.project_root)

    def get_feishu_status_debug(self) -> str:
        """返回当前飞书监听状态，不再承担启动监听的语义。"""
        service = self._get_or_create_feishu_ingress_service()
        result = service.client.get_listener_status()
        if result is None:
            return _feishu_debug_payload(
                status="empty",
                action="no_listener",
                event_id="",
                message_id="",
                record_path="",
                detail="feishu listener status is unavailable; listener should auto-start with app bootstrap",
            )
        detail = result.detail
        if result.ok:
            detail = f"{result.detail}. listener auto-starts with app bootstrap."
        return _feishu_debug_payload(
            status="ok" if result.ok else "error",
            action="listener_status",
            event_id="",
            message_id="",
            record_path="",
            detail=detail,
            payload=result.payload,
        )

    def start_feishu_listener_debug(self) -> str:
        """兼容旧接口，现仅返回当前飞书监听状态。"""
        return self.get_feishu_status_debug()

    def start_feishu_doctor_debug(self) -> str:
        """兼容旧接口，现仅返回 doctor 诊断快照。"""
        return self.get_feishu_doctor_debug()

    def get_latest_feishu_debug(self) -> str:
        """返回最近一条飞书接入结果，便于本地 CLI 调试查看。"""
        service = self._get_or_create_feishu_ingress_service()
        if service.latest_result is None:
            listener_status = service.client.get_listener_status()
            if listener_status is not None:
                return _feishu_debug_payload(
                    status="ok" if listener_status.ok else "error",
                    action=listener_status.status,
                    event_id="",
                    message_id="",
                    record_path="",
                    detail=listener_status.detail,
                    payload=listener_status.payload,
                )
            return _feishu_debug_payload(
                status="empty",
                action="no_event",
                event_id="",
                message_id="",
                record_path="",
                detail="no feishu ingress event has been processed yet",
            )
        result = service.latest_result
        return _feishu_debug_payload(
            status="ok",
            action=result.action,
            event_id=result.event_id,
            message_id=result.message_id,
            record_path=result.record_path,
            detail=result.detail,
            payload=result.payload,
        )

    def get_feishu_doctor_debug(self) -> str:
        """返回当前飞书监听实例的本地诊断视图。"""
        service = self._get_or_create_feishu_ingress_service()
        listener_status = service.client.get_listener_status()
        latest_result = service.latest_result
        if listener_status is None:
            return _feishu_debug_payload(
                status="empty",
                action="doctor_no_listener",
                event_id="",
                message_id="",
                record_path="",
                detail="feishu listener is not running",
                payload=self._build_feishu_doctor_payload(service, None, latest_result),
            )
        return _feishu_debug_payload(
            status="ok" if listener_status.ok else "error",
            action="doctor_status",
            event_id=latest_result.event_id if latest_result is not None else "",
            message_id=latest_result.message_id if latest_result is not None else "",
            record_path=latest_result.record_path if latest_result is not None else "",
            detail=listener_status.detail,
            payload=self._build_feishu_doctor_payload(service, listener_status, latest_result),
        )

    def create_chat_debug_session(self) -> ChatDebugSession:
        """创建可持续复用 Agent State 的 /chat 调试会话。"""
        self._ensure_runtime_layout()
        config = load_env_config(self.project_root)
        client = OpenAICompatibleModelClient(config)
        skill_registry = SkillRegistry(self.project_root / "skills")
        registry = create_runtime_tool_registry()
        audit_logger = self._create_audit_logger(config)
        return ChatDebugSession(
            AgentLoop(
                client,
                registry,
                self.project_root,
                permission_mode=config.permission_mode,
                approval_requester=self._prompt_cli_permission,
                audit_logger=audit_logger,
                skill_registry=skill_registry,
            )
        )

    def _get_or_create_chat_debug_service(self) -> ChatDebugService:
        """构造并复用非阻塞 /chat 调试服务。"""
        if self._chat_debug_service is not None:
            return self._chat_debug_service
        self._chat_debug_service = ChatDebugService(self._handle_chat_debug_task)
        return self._chat_debug_service

    def _get_or_create_persistent_chat_session(self) -> ChatDebugSession:
        """构造并复用跨任务持续的 /chat 调试会话，供上下文命令操作。"""
        if self._persistent_chat_session is None:
            self._persistent_chat_session = self.create_chat_debug_session()
        return self._persistent_chat_session

    def _handle_chat_debug_task(self, task: ChatDebugTask) -> str:
        """执行单条调试任务，复用持续会话以保留跨任务上下文状态。"""
        session = self._get_or_create_persistent_chat_session()
        try:
            result = session.run_turn(task.user_text)
        except Exception as exc:  # noqa: BLE001
            return _chat_error("chat_failed", str(exc))
        return result.to_debug_text()

    def clear_context_debug(self) -> str:
        """清空当前持续 /chat 调试会话的运行时上下文投影缓存。"""
        if self._persistent_chat_session is None:
            return _context_debug_payload(
                "empty", "no_session", "no chat debug session; run /chat run first"
            )
        self._persistent_chat_session.loop.runtime_context_manager.reset()
        return _context_debug_payload("ok", "cleared", "runtime context projection cache cleared")

    def compress_context_debug(self) -> str:
        """对当前持续 /chat 调试会话触发手动 LLM 阶段摘要压缩。"""
        session = self._get_or_create_persistent_chat_session()
        if session.state is None:
            return _context_debug_payload(
                "empty", "no_state", "no context state; run /chat run first"
            )
        loop = session.loop
        manager = loop.runtime_context_manager
        try:
            projected_state = manager.project_state_for_model(session.state)
        except Exception as exc:  # noqa: BLE001
            return _context_debug_payload("error", "projection_failed", str(exc))
        working_set = manager.latest_working_set
        if working_set is None:
            return _context_debug_payload("error", "no_working_set", "projection produced no working set")
        try:
            trigger, record = loop.phase_summary_service.maybe_create_summary(
                model_client=loop.model_client,
                state=session.state,
                projected_messages=projected_state.messages,
                working_set=working_set,
                delta=manager.latest_state_delta,
                budget=manager.latest_budget_report,
                forced_reason="manual_compress",
            )
        except Exception as exc:  # noqa: BLE001
            return _context_debug_payload("error", "compress_failed", str(exc))
        if record is None:
            return _context_debug_payload(
                "ok",
                "no_summary",
                f"trigger={trigger.reason} mode={trigger.mode}",
                {"trigger": trigger.to_dict()},
            )
        return _context_debug_payload(
            "ok",
            "compressed",
            f"phase summary generated: {record.relative_path}",
            {
                "trigger": trigger.to_dict(),
                "record_path": record.relative_path,
                "summary_id": record.summary_id,
            },
        )

    def run(self, args: Sequence[str] | None = None) -> int:
        """根据命令参数启动健康检查或 CLI 控制台。"""
        parser = self._build_parser()
        parsed = parser.parse_args(args)
        if parsed.health:
            print(self.health_check().to_text())
            return 0
        self._bootstrap_background_services()
        return self.cli.start(interactive=not parsed.no_interactive)

    def _build_parser(self) -> argparse.ArgumentParser:
        """构建应用启动参数解析器。"""
        parser = argparse.ArgumentParser(prog="dutyflow")
        parser.add_argument("--health", action="store_true", help="运行健康检查")
        parser.add_argument(
            "--interactive",
            action="store_true",
            help="兼容参数；默认已启动本地 CLI 控制台",
        )
        parser.add_argument(
            "--no-interactive",
            action="store_true",
            help="只输出启动提示，不进入持续 CLI 控制台",
        )
        return parser

    def _create_audit_logger(self, config) -> AuditLogger:
        """构造当前运行链路可复用的审计日志对象。"""
        markdown_store = MarkdownStore(FileStore(self.project_root))
        return AuditLogger(markdown_store, config.log_dir)

    def _build_feishu_doctor_payload(
        self,
        service: FeishuIngressService,
        listener_status: object,
        latest_result: object,
    ) -> dict[str, Any]:
        """汇总当前监听器、配置占位和最近接入结果，供 doctor 模式查看。"""
        config = service.config
        listener_payload = {}
        listener_ok = False
        listener_state = ""
        if listener_status is not None:
            listener_ok = bool(getattr(listener_status, "ok", False))
            listener_state = str(getattr(listener_status, "status", "") or "")
            listener_payload = dict(getattr(listener_status, "payload", {}) or {})
        latest_payload = {}
        latest_action = ""
        if latest_result is not None:
            latest_action = str(getattr(latest_result, "action", "") or "")
            latest_payload = dict(getattr(latest_result, "payload", {}) or {})
        return {
            "pid": os.getpid(),
            "app_id": config.feishu_app_id,
            "event_mode": config.feishu_event_mode,
            "log_level": config.log_level,
            "listener_ok": listener_ok,
            "listener_state": listener_state,
            "latest_ingress_action": latest_action,
            "tenant_key_configured": _is_real_env_value(config.feishu_tenant_key),
            "owner_open_id_configured": _is_real_env_value(config.feishu_owner_open_id),
            "owner_report_chat_id_configured": _is_real_env_value(
                config.feishu_owner_report_chat_id
            ),
            "listener": listener_payload,
            "latest_ingress_payload": latest_payload,
        }

    def _get_or_create_feishu_ingress_service(self) -> FeishuIngressService:
        """按当前配置构造并复用飞书接入层服务。"""
        self._ensure_runtime_layout()
        if self._feishu_ingress_service is not None:
            return self._feishu_ingress_service
        config = load_env_config(self.project_root)
        self._feishu_ingress_service = FeishuIngressService(
            self.project_root,
            config,
            runtime_service=self._get_or_create_runtime_service(),
        )
        return self._feishu_ingress_service

    def _get_or_create_runtime_service(self) -> RuntimeService:
        """构造并复用正式 runtime service 骨架。"""
        if self._runtime_service is not None:
            return self._runtime_service
        self._runtime_service = RuntimeService(
            self._get_or_create_runtime_loop().handle_work_item
        )
        return self._runtime_service

    def _get_or_create_runtime_loop(self) -> RuntimeAgentLoop:
        """构造并复用正式 runtime loop 包装层。"""
        if self._runtime_loop is not None:
            return self._runtime_loop
        config = load_env_config(self.project_root)
        self._runtime_loop = RuntimeAgentLoop(
            self.project_root,
            config,
            audit_logger=self._create_audit_logger(config),
        )
        return self._runtime_loop

    def _bootstrap_background_services(self) -> None:
        """在应用启动时静默拉起正式 runtime、后台任务执行面、调度器和飞书监听。"""
        self._ensure_runtime_layout()
        self._get_or_create_runtime_service().start()
        self._get_or_create_background_task_worker().start()
        self._get_or_create_task_scheduler_service().start()
        self._get_or_create_feishu_ingress_service().start_long_connection()

    def _get_or_create_background_task_worker(self) -> BackgroundTaskWorker:
        """构造并复用正式 runtime 之外的后台任务 worker。"""
        if self._background_task_worker is not None:
            return self._background_task_worker
        config = load_env_config(self.project_root)
        self._background_task_worker = BackgroundTaskWorker(
            TaskStore(self.project_root),
            model_client=OpenAICompatibleModelClient(config),
            feedback_gateway=FeedbackGateway(config),
        )
        return self._background_task_worker

    def _get_or_create_task_scheduler_service(self) -> TaskSchedulerService:
        """构造并复用后台任务调度器，把到时任务送入独立 worker。"""
        if self._task_scheduler_service is not None:
            return self._task_scheduler_service
        self._task_scheduler_service = TaskSchedulerService(
            TaskStore(self.project_root),
            self._enqueue_scheduled_task_to_background_worker,
        )
        return self._task_scheduler_service

    def _enqueue_scheduled_task_to_background_worker(self, dispatch: TaskDispatchItem) -> None:
        """把调度器发现的到时任务送入后台任务 worker。"""
        self._get_or_create_background_task_worker().enqueue_task(dispatch.task_id, source="scheduler")

    def _prompt_cli_permission(
        self,
        tool_name: str,
        reason: str,
        tool_input: Mapping[str, Any],
    ) -> bool:
        """在 CLI 中询问用户是否允许敏感工具继续执行。"""
        preview = build_audit_preview(dict(tool_input), max_chars=200)
        print("\n[Permission Required]")
        print(f"tool={tool_name}")
        print(f"reason={reason}")
        print(f"input={preview}")
        try:
            answer = input("Press Enter to approve, type 'no' to reject: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return False
        return answer in {"", "y", "yes"}


def main(args: Sequence[str] | None = None) -> int:
    """提供 uv run dutyflow 使用的程序入口。"""
    app = DutyFlowApp()
    return app.run(args)


def _chat_error(error_kind: str, message: str) -> str:
    """格式化 /chat 调试错误，避免泄露密钥。"""
    payload = {
        "error": error_kind,
        "message": message,
        "final_text": "",
        "stop_reason": "failed",
        "turn_count": 0,
        "tool_result_count": 0,
        "tools": [],
        "pending_restart_count": 0,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _chat_debug_payload(
    *,
    status: str,
    action: str,
    detail: str,
    payload: Mapping[str, Any] | None = None,
) -> str:
    """格式化非阻塞 /chat 调试服务的标准输出。"""
    body = {
        "status": status,
        "action": action,
        "detail": detail,
        "payload": dict(payload or {}),
    }
    return json.dumps(body, ensure_ascii=False, indent=2)


def _feishu_debug_payload(
    *,
    status: str,
    action: str,
    event_id: str,
    message_id: str,
    record_path: str,
    detail: str,
    payload: Mapping[str, Any] | None = None,
) -> str:
    """格式化飞书接入层本地调试输出。"""
    body = {
        "status": status,
        "action": action,
        "event_id": event_id,
        "message_id": message_id,
        "record_path": record_path,
        "detail": detail,
        "payload": dict(payload or {}),
    }
    return json.dumps(body, ensure_ascii=False, indent=2)


def _context_debug_payload(
    status: str,
    action: str,
    detail: str,
    payload: Mapping[str, Any] | None = None,
) -> str:
    """格式化 /context 调试命令的标准输出。"""
    body = {
        "status": status,
        "action": action,
        "detail": detail,
        "payload": dict(payload or {}),
    }
    return json.dumps(body, ensure_ascii=False, indent=2)


def _feishu_error(error_kind: str, message: str) -> str:
    """格式化飞书接入层调试错误，保持 CLI 返回稳定 JSON。"""
    return _feishu_debug_payload(
        status="error",
        action=error_kind,
        event_id="",
        message_id="",
        record_path="",
        detail=message,
    )


@dataclass(frozen=True)
class _DmDebugArgs:
    """表示 /feishu dm 调试命令解析后的参数。"""

    chat_id: str
    start_time: int
    end_time: int
    error_kind: str = ""
    detail: str = ""


def _default_dm_chat_id(arg_text: str, config: object, registry: FeishuScopeRegistry) -> str:
    """返回 /feishu dm 默认 p2p chat_id，优先使用 Scope Registry。"""
    if not _dm_args_need_default_chat_id(arg_text):
        return ""
    seeded = seed_owner_p2p_scope(registry, config)
    if seeded is not None and seeded.status == "disabled":
        return ""
    account_id = scope_account_id_from_config(config)
    scopes = registry.list_enabled(DIRECT_MESSAGE_COLLECTOR, account_id=account_id)
    for scope in scopes:
        if scope.scope_type == "p2p_chat":
            return scope.scope_id
    return ""


def _dm_args_need_default_chat_id(arg_text: str) -> bool:
    """判断 /feishu dm 参数是否需要默认 chat_id。"""
    tokens = [_clean_dm_token(token) for token in arg_text.split()]
    tokens = [token for token in tokens if token]
    return not tokens or _is_integer_text(tokens[0])


def _resolve_single_scope(registry: FeishuScopeRegistry, identifier: str) -> FeishuScopeRecord | str:
    """把 CLI 中的 scope_id 或 record_id 解析成唯一 scope。"""
    matches = registry.resolve_identifier(identifier)
    if not matches:
        return "scope not found: " + identifier
    if len(matches) > 1:
        return "scope identifier is ambiguous: " + identifier
    return matches[0]


def _feishu_scopes_payload(
    action: str,
    records: Sequence[FeishuScopeRecord],
    project_root: Path,
) -> str:
    """格式化 Scope Registry 调试输出。"""
    return _feishu_debug_payload(
        status="ok",
        action=action,
        event_id="",
        message_id="",
        record_path=_scope_record_path(records[0], project_root) if records else "",
        detail="ok",
        payload={"scopes": [_scope_payload(record, project_root) for record in records]},
    )


def _scope_payload(record: FeishuScopeRecord, project_root: Path) -> dict[str, str]:
    """构造单个 scope 的 CLI 输出。"""
    return {
        "record_id": record.record_id,
        "account_id": record.account_id,
        "scope_type": record.scope_type,
        "scope_id": record.scope_id,
        "status": record.status,
        "collector_names": ",".join(record.collector_names),
        "discovered_from": record.discovered_from,
        "detail_file": _scope_record_path(record, project_root),
    }


def _scope_record_path(record: FeishuScopeRecord, project_root: Path) -> str:
    """返回 scope 详情文件的项目相对路径。"""
    registry = FeishuScopeRegistry(project_root)
    path = registry.path_for(record.account_id, record.scope_type, record.scope_id)
    try:
        return str(path.relative_to(project_root.resolve()))
    except ValueError:
        return str(path)


def _parse_dm_debug_args(arg_text: str, default_chat_id: str) -> _DmDebugArgs:
    """解析 /feishu dm 的短参数形式。"""
    now = _now_unix_seconds()
    tokens = [_clean_dm_token(token) for token in arg_text.split()]
    tokens = [token for token in tokens if token]
    chat_id = ""
    time_tokens: list[str] = []
    if not tokens:
        chat_id = default_chat_id
    elif _is_integer_text(tokens[0]):
        chat_id = default_chat_id
        time_tokens = tokens
    else:
        chat_id = tokens[0]
        time_tokens = tokens[1:]
    if not _is_real_env_value(chat_id):
        return _dm_parse_error("missing_chat_id", _dm_usage_text())
    return _parse_dm_time_tokens(chat_id, time_tokens, now)


def _parse_dm_time_tokens(
    chat_id: str,
    time_tokens: list[str],
    now: int,
) -> _DmDebugArgs:
    """解析 lookback 或 start/end 时间参数。"""
    if not time_tokens:
        return _DmDebugArgs(chat_id, now - DEFAULT_DM_COLLECT_LOOKBACK_SECONDS, now)
    if len(time_tokens) == 1 and _is_integer_text(time_tokens[0]):
        lookback = _bounded_dm_lookback(int(time_tokens[0]))
        return _DmDebugArgs(chat_id, now - lookback, now)
    if len(time_tokens) == 2 and all(_is_integer_text(value) for value in time_tokens):
        start_time = int(time_tokens[0])
        end_time = int(time_tokens[1])
        if start_time >= end_time:
            return _dm_parse_error("invalid_time_window", "start_time must be before end_time")
        return _DmDebugArgs(chat_id, start_time, end_time)
    return _dm_parse_error("invalid_args", _dm_usage_text())


def _bounded_dm_lookback(value: int) -> int:
    """把回拉秒数限制在 CLI 调试允许范围内。"""
    if value <= 0:
        return DEFAULT_DM_COLLECT_LOOKBACK_SECONDS
    return min(value, MAX_DM_COLLECT_LOOKBACK_SECONDS)


def _dm_collect_debug_payload(result: object, parsed: _DmDebugArgs) -> str:
    """把 direct_message_collector 结果格式化为 CLI 调试 JSON。"""
    record_paths = tuple(getattr(result, "record_paths", ()) or ())
    return _feishu_debug_payload(
        status="ok" if bool(getattr(result, "ok", False)) else "error",
        action="dm_collect",
        event_id="",
        message_id="",
        record_path=record_paths[0] if record_paths else "",
        detail=str(getattr(result, "detail", "") or getattr(result, "status", "")),
        payload={
            "chat_id": parsed.chat_id,
            "start_time": parsed.start_time,
            "end_time": parsed.end_time,
            "collector_status": str(getattr(result, "status", "")),
            "items_written": int(getattr(result, "items_written", 0) or 0),
            "record_paths": record_paths,
            "cursor": str(getattr(result, "cursor", "")),
            "next_cursor": str(getattr(result, "next_cursor", "")),
            "has_more": bool(getattr(result, "has_more", False)),
            "next_page_token": str(getattr(result, "next_page_token", "")),
            "sync_state_path": str(getattr(result, "sync_state_path", "")),
            "stopped_reason": str(getattr(result, "stopped_reason", "")),
        },
    )


def _dm_parse_error(error_kind: str, detail: str) -> _DmDebugArgs:
    """构造 /feishu dm 参数错误。"""
    return _DmDebugArgs("", 0, 0, error_kind=error_kind, detail=detail)


def _dm_usage_text() -> str:
    """返回 /feishu dm 的短用法说明。"""
    return (
        "usage: /feishu dm [chat_id] [lookback_seconds] "
        "or /feishu dm [chat_id] <start_time> <end_time>"
    )


def _is_integer_text(value: str) -> bool:
    """判断字符串是否为整数。"""
    return value.strip().isdigit()


def _clean_dm_token(value: str) -> str:
    """清理 CLI 参数尾随标点，兼容复制命令时带入的中英文冒号。"""
    return value.strip().rstrip(":：")


def _now_unix_seconds() -> int:
    """返回当前 UTC 秒级时间戳。"""
    return int(datetime.now(timezone.utc).timestamp())


def _is_real_env_value(value: str) -> bool:
    """判断配置值是否已脱离示例占位，便于 doctor 模式快速查看。"""
    normalized = value.strip().lower()
    return bool(normalized) and not normalized.startswith("replace-with-")


def _debug_payload_is_error(text: str) -> bool:
    """判断本地调试输出是否为 error，供 doctor 启动链路复用。"""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return False
    return payload.get("status") == "error"


def _self_test() -> None:
    """验证应用入口和健康检查的最小行为。"""
    app = DutyFlowApp(Path.cwd())
    status = app.health_check()
    assert status.status == "ok"
    assert status.app_entry == "src/dutyflow/app.py"


if __name__ == "__main__":
    _self_test()
    raise SystemExit(main())
