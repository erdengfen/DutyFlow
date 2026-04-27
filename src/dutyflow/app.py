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
import os
from typing import Any, Mapping, Sequence

from dutyflow.agent.loop import AgentLoop, ChatDebugSession
from dutyflow.agent.model_client import OpenAICompatibleModelClient
from dutyflow.agent.skills import SkillRegistry
from dutyflow.cli.main import CliConsole
from dutyflow.config.env import load_env_config
from dutyflow.feishu.runtime import FeishuIngressService
from dutyflow.logging.audit_log import AuditLogger, build_audit_preview
from dutyflow.storage.file_store import FileStore
from dutyflow.storage.markdown_store import MarkdownDocument, MarkdownStore
from dutyflow.agent.tools.registry import create_runtime_tool_registry


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
        self._feishu_ingress_service: FeishuIngressService | None = None

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
        self._ensure_agent_control_state(markdown_store, config.data_dir)
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
            data_dir / "contexts",
            data_dir / "approvals" / "pending",
            data_dir / "approvals" / "completed",
            data_dir / "tasks",
            data_dir / "reports",
            data_dir / "plans",
        ):
            store.ensure_dir(relative)

    def _ensure_agent_control_state(self, store: MarkdownStore, data_dir: Path) -> None:
        """缺失时创建最小 Agent 运行状态快照文件。"""
        state_path = data_dir / "state" / "agent_control_state.md"
        if store.exists(state_path):
            return
        document = MarkdownDocument(
            frontmatter={
                "schema": "dutyflow.agent_control_state.v1",
                "id": "agent_control_state_local_user",
                "updated_at": "1970-01-01T00:00:00+00:00",
                "current_model": "",
                "permission_mode": "default",
                "active_task_ids": "",
                "waiting_approval_task_ids": "",
                "last_event_id": "",
            },
            body=(
                "# Agent Control State Snapshot\n\n"
                "## Runtime\n\n"
                "- status: initialized\n"
                "- current_model:\n"
                "- permission_mode: default\n"
                "- last_event:\n\n"
                "## Task Control\n\n"
                "| task_id | weight_level | attempt_count | approval_status | retry_status | next_action |\n"
                "|---|---|---:|---|---|---|\n\n"
                "## Recovery\n\n"
                "| scope_id | continuation_attempts | compact_attempts | transport_attempts | tool_error_attempts |\n"
                "|---|---:|---:|---:|---:|\n\n"
                "## Notes\n\n"
                "Step 1 initialized placeholder runtime snapshot.\n"
            ),
        )
        store.write_document(state_path, document)

    def run_chat_debug(self, user_text: str) -> str:
        """运行 CLI /chat 调试链路并返回完整可见结果。"""
        if not user_text.strip():
            return _chat_error("empty_input", "usage: /chat 用户输入")
        try:
            result = self.create_chat_debug_session().run_turn(user_text)
        except Exception as exc:  # noqa: BLE001
            return _chat_error("chat_failed", str(exc))
        return result.to_debug_text()

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

    def start_feishu_listener_debug(self) -> str:
        """启动 Step 5 飞书长连接监听调试入口。"""
        try:
            service = self._get_or_create_feishu_ingress_service()
            result = service.start_long_connection()
        except Exception as exc:  # noqa: BLE001
            return _feishu_error("listener_failed", str(exc))
        detail = result.detail
        if result.ok and result.status in {"listener_started", "already_running"}:
            detail = (
                f"{result.detail}. send /bind to the bot in a p2p chat and watch this terminal "
                "for realtime Feishu event logs."
            )
        return _feishu_debug_payload(
            status="ok" if result.ok else "error",
            action=result.status,
            event_id="",
            message_id="",
            record_path="",
            detail=detail,
            payload=result.payload,
        )

    def start_feishu_doctor_debug(self) -> str:
        """启动飞书长连接并返回 doctor 诊断快照。"""
        service = self._get_or_create_feishu_ingress_service()
        listener_status = service.client.get_listener_status()
        if listener_status is None or listener_status.status not in {
            "listener_started",
            "already_running",
        }:
            start_payload = self.start_feishu_listener_debug()
            if _debug_payload_is_error(start_payload):
                return start_payload
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

    def run(self, args: Sequence[str] | None = None) -> int:
        """根据命令参数启动健康检查或 CLI 控制台。"""
        parser = self._build_parser()
        parsed = parser.parse_args(args)
        if parsed.health:
            print(self.health_check().to_text())
            return 0
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
        self._feishu_ingress_service = FeishuIngressService(self.project_root, config)
        return self._feishu_ingress_service

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
