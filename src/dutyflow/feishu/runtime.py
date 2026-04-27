# 本文件负责飞书接入层运行骨架，完成事件过滤、去重、落盘和长连接接入编排。

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
import tempfile
from typing import Any, Mapping

from dutyflow.config.env import EnvConfig, validate_feishu_ingress_config
from dutyflow.feishu.client import FeishuClient, FeishuClientResult
from dutyflow.feishu.events import FeishuEventAdapter, FeishuEventEnvelope
from dutyflow.storage.file_store import FileStore
from dutyflow.storage.markdown_store import MarkdownDocument, MarkdownStore


@dataclass(frozen=True)
class FeishuIngressResult:
    """表示接入层处理单条原始事件后的结果。"""

    action: str
    event_id: str
    message_id: str
    record_path: str
    detail: str


class FeishuIngressService:
    """编排 Step 5 的原始事件接入、过滤、去重和 Markdown 落盘。"""

    def __init__(
        self,
        project_root: Path,
        config: EnvConfig,
        *,
        adapter: FeishuEventAdapter | None = None,
        client: FeishuClient | None = None,
        markdown_store: MarkdownStore | None = None,
    ) -> None:
        """绑定配置、适配器和持久化依赖。"""
        self.project_root = project_root
        self.config = config
        self.adapter = adapter or FeishuEventAdapter()
        self.client = client or FeishuClient(config)
        self.markdown_store = markdown_store or MarkdownStore(FileStore(project_root))
        self.events_dir = config.data_dir / "events"
        self.latest_result: FeishuIngressResult | None = None
        self._ensure_events_dir()
        self.seen_event_ids, self.seen_message_ids = self._load_dedup_state()

    def start_long_connection(self) -> FeishuClientResult:
        """启动长连接接入骨架，收到事件后交给当前服务处理。"""
        validation = validate_feishu_ingress_config(self.config)
        if not validation.ok:
            return FeishuClientResult(
                ok=False,
                status="invalid_config",
                detail=validation.message(),
            )
        return self.client.connect_long_connection(self._handle_for_connection)

    def handle_raw_event(self, raw_event: Mapping[str, Any]) -> FeishuIngressResult:
        """处理单条原始事件，执行过滤、去重和结构化落盘。"""
        envelope = self.adapter.build_event_envelope(raw_event)
        if not self._is_supported_event(envelope):
            result = FeishuIngressResult(
                action="ignored",
                event_id=envelope.event_id,
                message_id=envelope.message_id,
                record_path="",
                detail="event is outside Step 5 initial scope",
            )
            self.latest_result = result
            return result
        duplicate = self._detect_duplicate(envelope)
        if duplicate is not None:
            self.latest_result = duplicate
            return duplicate
        record_path = self._write_event_record(envelope)
        self._remember_event(envelope)
        result = FeishuIngressResult(
            action="accepted",
            event_id=envelope.event_id,
            message_id=envelope.message_id,
            record_path=str(record_path),
            detail="event accepted and persisted",
        )
        self.latest_result = result
        return result

    def ack_event(self, result: FeishuIngressResult) -> dict[str, Any]:
        """返回接入层统一确认结构，便于后续接 WebSocket 或回调入口。"""
        return {"success": True, "action": result.action, "event_id": result.event_id}

    def _handle_for_connection(self, raw_event: Mapping[str, Any]) -> dict[str, Any]:
        """把长连接收到的原始事件桥接到统一确认格式。"""
        result = self.handle_raw_event(raw_event)
        return self.ack_event(result)

    def _is_supported_event(self, envelope: FeishuEventEnvelope) -> bool:
        """只允许私聊 Bot 或群聊 @Bot 进入 Step 5 初版主链。"""
        return envelope.is_p2p_message() or envelope.is_group_at_bot()

    def _detect_duplicate(
        self,
        envelope: FeishuEventEnvelope,
    ) -> FeishuIngressResult | None:
        """按 event_id 和 message_id 执行最小去重。"""
        if envelope.event_id and envelope.event_id in self.seen_event_ids:
            return FeishuIngressResult(
                action="duplicate_event",
                event_id=envelope.event_id,
                message_id=envelope.message_id,
                record_path="",
                detail="event_id already processed",
            )
        if envelope.message_id and envelope.message_id in self.seen_message_ids:
            return FeishuIngressResult(
                action="duplicate_message",
                event_id=envelope.event_id,
                message_id=envelope.message_id,
                record_path="",
                detail="message_id already processed",
            )
        return None

    def _write_event_record(self, envelope: FeishuEventEnvelope) -> Path:
        """把原始事件最小规范化后写入 Markdown 记录。"""
        record_id = _build_record_id(envelope)
        document = MarkdownDocument(
            frontmatter=_build_frontmatter(self.config, envelope, record_id),
            body=_build_event_body(self.config, envelope, record_id),
        )
        path = self.events_dir / f"{record_id}.md"
        return self.markdown_store.write_document(path, document)

    def _remember_event(self, envelope: FeishuEventEnvelope) -> None:
        """把当前事件写入进程内去重集合。"""
        if envelope.event_id:
            self.seen_event_ids.add(envelope.event_id)
        if envelope.message_id:
            self.seen_message_ids.add(envelope.message_id)

    def _ensure_events_dir(self) -> None:
        """保证事件目录存在，便于 fixture 和真实事件统一落盘。"""
        self.markdown_store.file_store.ensure_dir(self.events_dir)

    def _load_dedup_state(self) -> tuple[set[str], set[str]]:
        """从已落盘事件中恢复 event_id 和 message_id 去重状态。"""
        event_ids: set[str] = set()
        message_ids: set[str] = set()
        for path in self.markdown_store.file_store.resolve(self.events_dir).glob("evt_*.md"):
            self._collect_seen_ids_from_document(path, event_ids, message_ids)
        return event_ids, message_ids

    def _collect_seen_ids_from_document(
        self,
        path: Path,
        event_ids: set[str],
        message_ids: set[str],
    ) -> None:
        """从单条事件文档中提取已处理的 event_id 和 message_id。"""
        try:
            document = self.markdown_store.read_document(path)
        except Exception:  # noqa: BLE001
            return
        event_id = document.frontmatter.get("feishu_event_id", "").strip()
        message_id = document.frontmatter.get("message_id", "").strip()
        if event_id:
            event_ids.add(event_id)
        if message_id:
            message_ids.add(message_id)


def _build_record_id(envelope: FeishuEventEnvelope) -> str:
    """优先使用 message_id 作为稳定事件记录 ID。"""
    suffix = envelope.message_id or envelope.event_id or "unknown"
    return "evt_" + _sanitize_suffix(suffix)


def _sanitize_suffix(value: str) -> str:
    """把外部 ID 转成适合本地文件名的稳定后缀。"""
    cleaned = [char if char.isalnum() else "_" for char in value]
    result = "".join(cleaned).strip("_")
    return result or "unknown"


def _build_frontmatter(
    config: EnvConfig,
    envelope: FeishuEventEnvelope,
    record_id: str,
) -> dict[str, str]:
    """构造事件记录 frontmatter，保留接入层路由字段。"""
    app_id = envelope.app_id or config.feishu_app_id
    tenant_key = envelope.tenant_key or config.feishu_tenant_key
    return {
        "schema": "dutyflow.event_record.v1",
        "id": record_id,
        "received_at": envelope.received_at,
        "source_type": "chat",
        "source_id": envelope.chat_id,
        "sender_contact_id": "",
        "feishu_event_id": envelope.event_id,
        "event_kind": envelope.event_type or "message",
        "task_id": "",
        "message_id": envelope.message_id,
        "tenant_key": tenant_key,
        "app_id": app_id,
        "sender_open_id": envelope.sender_open_id,
        "owner_open_id": config.feishu_owner_open_id,
        "installation_scope_id": _join_scope_id(app_id, tenant_key),
        "owner_profile_id": _join_scope_id(app_id, tenant_key, config.feishu_owner_open_id),
        "sender_subject_id": _join_scope_id(app_id, tenant_key, envelope.sender_open_id),
        "chat_binding_id": _join_scope_id(app_id, tenant_key, envelope.chat_id),
    }


def _build_event_body(
    config: EnvConfig,
    envelope: FeishuEventEnvelope,
    record_id: str,
) -> str:
    """构造事件记录正文，保留最小摘要和原始 payload。"""
    app_id = envelope.app_id or config.feishu_app_id
    tenant_key = envelope.tenant_key or config.feishu_tenant_key
    payload = json.dumps(envelope.raw_event, ensure_ascii=False, indent=2, sort_keys=True)
    mention_state = "bot" if envelope.mentions_bot else ""
    return (
        f"# Event {record_id}\n\n"
        "## Raw Summary\n\n"
        f"- event_type: {envelope.event_type}\n"
        f"- chat_type: {envelope.chat_type}\n"
        f"- message_id: {envelope.message_id}\n"
        f"- sender_open_id: {envelope.sender_open_id}\n"
        f"- tenant_key: {tenant_key}\n"
        f"- installation_scope_id: {_join_scope_id(app_id, tenant_key)}\n"
        f"- owner_profile_id: {_join_scope_id(app_id, tenant_key, config.feishu_owner_open_id)}\n"
        f"- sender_subject_id: {_join_scope_id(app_id, tenant_key, envelope.sender_open_id)}\n"
        f"- chat_binding_id: {_join_scope_id(app_id, tenant_key, envelope.chat_id)}\n"
        f"- preview: {envelope.content_preview}\n\n"
        "## Raw Payload\n\n"
        "```json\n"
        f"{payload}\n"
        "```\n\n"
        "## Extracted Signals\n\n"
        f"- sender: {envelope.sender_open_id}\n"
        f"- source: {envelope.chat_id}\n"
        f"- mentioned_user: {mention_state}\n"
        "- file_or_doc:\n"
        "- action_hint:\n\n"
        "## Processing Status\n\n"
        "- identity_completed: no\n"
        "- weighting_completed: no\n"
        "- approval_required: no\n"
        "- task_created: no\n"
    )


def _join_scope_id(*parts: str) -> str:
    """把账号空间各段稳定拼成统一 scope id。"""
    return ":".join(part for part in parts if part)


def _self_test() -> None:
    """验证 fixture 事件可进入接入层并生成 Markdown 结果。"""
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        config = EnvConfig(
            model_api_key="",
            model_base_url="",
            model_name="",
            feishu_app_id="app_demo",
            feishu_app_secret="secret_demo",
            feishu_event_verify_token="",
            feishu_event_encrypt_key="",
            feishu_event_callback_url="",
            feishu_event_mode="fixture",
            feishu_tenant_key="tenant_demo",
            feishu_owner_open_id="ou_owner",
            feishu_owner_report_chat_id="oc_owner",
            feishu_owner_user_id="",
            feishu_owner_union_id="",
            feishu_oauth_redirect_uri="",
            feishu_oauth_default_scopes=[],
            feishu_owner_user_access_token="",
            feishu_owner_user_refresh_token="",
            feishu_owner_user_token_expires_at="",
            data_dir=Path("data"),
            log_dir=Path("data/logs"),
            runtime_env="test",
            log_level="INFO",
            permission_mode="default",
        )
        adapter = FeishuEventAdapter()
        service = FeishuIngressService(root, config, adapter=adapter)
        result = service.handle_raw_event(adapter.create_local_fixture_event("hello"))
        assert result.action == "accepted"


if __name__ == "__main__":
    _self_test()
    print("dutyflow feishu runtime self-test passed")
