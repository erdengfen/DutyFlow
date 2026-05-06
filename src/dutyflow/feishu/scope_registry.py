# 本文件负责飞书资源同步范围注册表，统一管理 collector 可消费的授权边界。

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping

from dutyflow.storage.file_store import FileStore
from dutyflow.storage.markdown_store import MarkdownDocument, MarkdownStore

FEISHU_SCOPE_SCHEMA = "dutyflow.feishu_scope.v1"
FEISHU_SCOPE_INDEX_SCHEMA = "dutyflow.feishu_scope_index.v1"
DEFAULT_SCOPE_ACCOUNT_ID = "local_owner"
DIRECT_MESSAGE_COLLECTOR = "direct_message_collector"
GROUP_MESSAGE_COLLECTOR = "group_message_collector"
USER_DOCUMENT_COLLECTOR = "user_document_collector"
GROUP_DOCUMENT_COLLECTOR = "group_document_collector"
P2P_CHAT_SCOPE = "p2p_chat"
GROUP_CHAT_SCOPE = "group_chat"
DRIVE_FOLDER_SCOPE = "drive_folder"
DOC_SCOPE = "doc"
WIKI_SCOPE = "wiki"
FILE_SCOPE = "file"

_ALLOWED_STATUSES = {"candidate", "approved", "enabled", "disabled", "permission_denied", "stale"}
# 关键开关：scope 文件名片段最多保留 120 字符，避免外部 ID 异常过长导致路径难读。
MAX_SAFE_SCOPE_FILE_PART_CHARS = 120


@dataclass(frozen=True)
class FeishuScopeRecord:
    """表示一个飞书资源同步范围，不保存任何 token 或密钥。"""

    account_id: str
    scope_type: str
    scope_id: str
    status: str = "candidate"
    collector_names: tuple[str, ...] = ()
    discovered_from: str = ""
    tenant_key: str = ""
    owner_open_id: str = ""
    owner_user_id: str = ""
    source_platform: str = "feishu"
    source_id: str = ""
    source_chat_id: str = ""
    source_message_id: str = ""
    source_event_id: str = ""
    source_url: str = ""
    approved_at: str = ""
    approved_by: str = ""
    disabled_reason: str = ""
    permission_error: str = ""
    last_success_at: str = ""
    last_attempt_at: str = ""
    updated_at: str = ""

    @property
    def record_id(self) -> str:
        """返回稳定记录 ID，供索引和 CLI 引用。"""
        parts = (self.account_id, self.scope_type, self.scope_id)
        return "fscope_" + "_".join(_safe_file_part(part) for part in parts)

    def __post_init__(self) -> None:
        """校验 scope 的最小可定位字段。"""
        if not self.account_id:
            raise ValueError("FeishuScopeRecord.account_id is required")
        if not self.scope_type:
            raise ValueError("FeishuScopeRecord.scope_type is required")
        if not self.scope_id:
            raise ValueError("FeishuScopeRecord.scope_id is required")
        if self.status not in _ALLOWED_STATUSES:
            raise ValueError("unsupported feishu scope status: " + self.status)


class FeishuScopeRegistry:
    """用 Markdown 保存飞书资源同步范围和授权状态。"""

    def __init__(self, project_root: Path) -> None:
        """绑定项目根目录和 Markdown 存储。"""
        self.project_root = Path(project_root).resolve()
        self.markdown_store = MarkdownStore(FileStore(self.project_root))

    def upsert_candidate(self, record: FeishuScopeRecord) -> FeishuScopeRecord:
        """写入或更新候选 scope；不会覆盖用户禁用状态。"""
        incoming = _normalized_record(record, "candidate")
        existing = self.read(incoming.account_id, incoming.scope_type, incoming.scope_id)
        merged = _merge_candidate(existing, incoming) if existing else incoming
        return self._write(merged)

    def approve_scope(
        self,
        account_id: str,
        scope_type: str,
        scope_id: str,
        *,
        approved_by: str = "owner",
    ) -> FeishuScopeRecord:
        """把已存在 scope 标记为用户批准。"""
        record = self._require_record(account_id, scope_type, scope_id)
        return self._write(_mark_approved(record, approved_by))

    def enable_scope(self, account_id: str, scope_type: str, scope_id: str) -> FeishuScopeRecord:
        """把已批准或候选 scope 标记为运行中启用。"""
        record = self._require_record(account_id, scope_type, scope_id)
        approved = record if record.approved_at else _mark_approved(record, "owner")
        return self._write(replace(approved, status="enabled", updated_at=_now_iso()))

    def disable_scope(
        self,
        account_id: str,
        scope_type: str,
        scope_id: str,
        *,
        reason: str = "",
    ) -> FeishuScopeRecord:
        """禁用指定 scope，后续发现流程不能自动重新启用。"""
        record = self._require_record(account_id, scope_type, scope_id)
        return self._write(
            replace(record, status="disabled", disabled_reason=reason, updated_at=_now_iso())
        )

    def mark_permission_denied(
        self,
        account_id: str,
        scope_type: str,
        scope_id: str,
        detail: str,
    ) -> FeishuScopeRecord:
        """记录权限失败状态，供人工重新授权或降级处理。"""
        record = self._require_record(account_id, scope_type, scope_id)
        return self._write(
            replace(
                record,
                status="permission_denied",
                permission_error=_truncate(detail, 240),
                last_attempt_at=_now_iso(),
                updated_at=_now_iso(),
            )
        )

    def mark_success(self, account_id: str, scope_type: str, scope_id: str) -> FeishuScopeRecord:
        """记录 scope 最近一次被 collector 成功消费。"""
        record = self._require_record(account_id, scope_type, scope_id)
        return self._write(
            replace(record, last_success_at=_now_iso(), last_attempt_at=_now_iso(), updated_at=_now_iso())
        )

    def list_enabled(self, collector_name: str, *, account_id: str = "") -> tuple[FeishuScopeRecord, ...]:
        """返回指定 collector 可消费的 enabled scope。"""
        return tuple(
            record
            for record in self.list_records(account_id=account_id, status="enabled")
            if collector_name in record.collector_names
        )

    def list_records(
        self,
        *,
        account_id: str = "",
        status: str = "",
    ) -> tuple[FeishuScopeRecord, ...]:
        """列出 registry 中的 scope，可按 account 和状态过滤。"""
        records = [record for record in self._read_all_records() if _record_matches(record, account_id, status)]
        return tuple(sorted(records, key=lambda item: (item.account_id, item.scope_type, item.scope_id)))

    def resolve_identifier(self, identifier: str) -> tuple[FeishuScopeRecord, ...]:
        """按 record_id 或 scope_id 查找 scope，供 CLI 简写命令使用。"""
        target = identifier.strip()
        if not target:
            return ()
        return tuple(
            record for record in self._read_all_records() if record.record_id == target or record.scope_id == target
        )

    def read(self, account_id: str, scope_type: str, scope_id: str) -> FeishuScopeRecord | None:
        """读取一个 scope；不存在时返回 None。"""
        path = self.path_for(account_id, scope_type, scope_id)
        if not self.markdown_store.exists(path):
            return None
        return _record_from_frontmatter(self.markdown_store.read_document(path).frontmatter)

    def path_for(self, account_id: str, scope_type: str, scope_id: str) -> Path:
        """返回 scope Markdown 文件路径。"""
        return self.markdown_store.file_store.resolve(_scope_path(account_id, scope_type, scope_id))

    def _write(self, record: FeishuScopeRecord) -> FeishuScopeRecord:
        """写入 scope 详情和全局索引。"""
        stored = record if record.updated_at else replace(record, updated_at=_now_iso())
        self.markdown_store.write_document(_scope_path(stored.account_id, stored.scope_type, stored.scope_id), _document(stored))
        self._write_index()
        return stored

    def _require_record(self, account_id: str, scope_type: str, scope_id: str) -> FeishuScopeRecord:
        """读取必须存在的 scope，不存在时抛出明确错误。"""
        record = self.read(account_id, scope_type, scope_id)
        if record is None:
            raise ValueError("feishu scope not found: " + scope_id)
        return record

    def _read_all_records(self) -> tuple[FeishuScopeRecord, ...]:
        """扫描 scope 目录下的 Markdown 详情文件。"""
        root = self.markdown_store.file_store.resolve("data/feishu/scopes")
        if not root.exists():
            return ()
        records: list[FeishuScopeRecord] = []
        for path in root.glob("*/*/scope_*.md"):
            record = _try_read_scope(self.markdown_store, path)
            if record is not None:
                records.append(record)
        return tuple(records)

    def _write_index(self) -> Path:
        """重建 scope 全局索引。"""
        rows = [_index_row(self.project_root, self.path_for(item.account_id, item.scope_type, item.scope_id), item) for item in self._read_all_records()]
        document = MarkdownDocument(_index_frontmatter(), _index_body(rows))
        return self.markdown_store.write_document("data/feishu/scopes/index.md", document)


def seed_owner_p2p_scope(
    registry: FeishuScopeRegistry,
    config: object,
    *,
    discovered_from: str = "env",
) -> FeishuScopeRecord | None:
    """把当前 `.env` 中已绑定的 owner p2p chat_id seed 到 registry。"""
    chat_id = str(getattr(config, "feishu_owner_report_chat_id", "")).strip()
    if not chat_id:
        return None
    record = FeishuScopeRecord(
        account_id=scope_account_id_from_config(config),
        scope_type=P2P_CHAT_SCOPE,
        scope_id=chat_id,
        status="enabled",
        collector_names=(DIRECT_MESSAGE_COLLECTOR,),
        discovered_from=discovered_from,
        tenant_key=str(getattr(config, "feishu_tenant_key", "")),
        owner_open_id=str(getattr(config, "feishu_owner_open_id", "")),
        owner_user_id=str(getattr(config, "feishu_owner_user_id", "")),
        source_id=chat_id,
    )
    existing = registry.read(record.account_id, record.scope_type, record.scope_id)
    if existing and existing.status == "disabled":
        return existing
    registry.upsert_candidate(record)
    registry.approve_scope(record.account_id, record.scope_type, record.scope_id, approved_by="bind_or_env")
    return registry.enable_scope(record.account_id, record.scope_type, record.scope_id)


def scope_account_id_from_config(config: object) -> str:
    """根据租户和 owner 身份构造稳定 account_id。"""
    tenant_key = str(getattr(config, "feishu_tenant_key", "")).strip()
    owner = str(getattr(config, "feishu_owner_open_id", "")).strip()
    if tenant_key and owner:
        return _safe_file_part(tenant_key) + "_" + _safe_file_part(owner)
    if owner:
        return _safe_file_part(owner)
    return DEFAULT_SCOPE_ACCOUNT_ID


def _scope_path(account_id: str, scope_type: str, scope_id: str) -> Path:
    """构造 scope 详情文件相对路径。"""
    return (
        Path("data/feishu/scopes")
        / _safe_file_part(account_id)
        / _safe_file_part(scope_type)
        / ("scope_" + _safe_file_part(scope_id) + ".md")
    )


def _document(record: FeishuScopeRecord) -> MarkdownDocument:
    """把 scope 记录渲染为 Markdown 文档。"""
    return MarkdownDocument(_frontmatter(record), _body(record))


def _frontmatter(record: FeishuScopeRecord) -> dict[str, str]:
    """构造 scope frontmatter。"""
    values = {
        "schema": FEISHU_SCOPE_SCHEMA,
        "id": record.record_id,
        "account_id": record.account_id,
        "scope_type": record.scope_type,
        "scope_id": record.scope_id,
        "status": record.status,
        "collector_names": _join_values(record.collector_names),
        "discovered_from": record.discovered_from,
        "tenant_key": record.tenant_key,
        "owner_open_id": record.owner_open_id,
        "owner_user_id": record.owner_user_id,
        "source_platform": record.source_platform,
        "source_id": record.source_id,
        "source_chat_id": record.source_chat_id,
        "source_message_id": record.source_message_id,
        "source_event_id": record.source_event_id,
        "source_url": record.source_url,
        "approved_at": record.approved_at,
        "approved_by": record.approved_by,
        "disabled_reason": record.disabled_reason,
        "permission_error": record.permission_error,
        "last_success_at": record.last_success_at,
        "last_attempt_at": record.last_attempt_at,
        "updated_at": record.updated_at or _now_iso(),
    }
    return {key: _frontmatter_value(value) for key, value in values.items()}


def _body(record: FeishuScopeRecord) -> str:
    """渲染 scope 的人工审查正文。"""
    return (
        f"# Feishu Scope {record.record_id}\n\n"
        "## Summary\n\n"
        f"{record.scope_type} {record.scope_id} is {record.status} for {_join_values(record.collector_names)}.\n\n"
        "## Details\n\n"
        "| key | value |\n"
        "|---|---|\n"
        f"| account_id | {_cell(record.account_id)} |\n"
        f"| scope_type | {_cell(record.scope_type)} |\n"
        f"| scope_id | {_cell(record.scope_id)} |\n"
        f"| discovered_from | {_cell(record.discovered_from)} |\n"
        f"| source_id | {_cell(record.source_id)} |\n"
        f"| status | {_cell(record.status)} |\n"
        f"| collector_names | {_cell(_join_values(record.collector_names))} |\n"
    )


def _record_from_frontmatter(frontmatter: Mapping[str, str]) -> FeishuScopeRecord:
    """从 frontmatter 还原 scope 记录。"""
    return FeishuScopeRecord(
        account_id=frontmatter.get("account_id", ""),
        scope_type=frontmatter.get("scope_type", ""),
        scope_id=frontmatter.get("scope_id", ""),
        status=frontmatter.get("status", "candidate"),
        collector_names=_split_values(frontmatter.get("collector_names", "")),
        discovered_from=frontmatter.get("discovered_from", ""),
        tenant_key=frontmatter.get("tenant_key", ""),
        owner_open_id=frontmatter.get("owner_open_id", ""),
        owner_user_id=frontmatter.get("owner_user_id", ""),
        source_platform=frontmatter.get("source_platform", "feishu"),
        source_id=frontmatter.get("source_id", ""),
        source_chat_id=frontmatter.get("source_chat_id", ""),
        source_message_id=frontmatter.get("source_message_id", ""),
        source_event_id=frontmatter.get("source_event_id", ""),
        source_url=frontmatter.get("source_url", ""),
        approved_at=frontmatter.get("approved_at", ""),
        approved_by=frontmatter.get("approved_by", ""),
        disabled_reason=frontmatter.get("disabled_reason", ""),
        permission_error=frontmatter.get("permission_error", ""),
        last_success_at=frontmatter.get("last_success_at", ""),
        last_attempt_at=frontmatter.get("last_attempt_at", ""),
        updated_at=frontmatter.get("updated_at", ""),
    )


def _merge_candidate(existing: FeishuScopeRecord, incoming: FeishuScopeRecord) -> FeishuScopeRecord:
    """合并候选发现结果，保留用户态和运行态状态。"""
    status = existing.status if existing.status != "candidate" else incoming.status
    collectors = tuple(sorted(set(existing.collector_names) | set(incoming.collector_names)))
    return replace(
        incoming,
        status=status,
        collector_names=collectors,
        approved_at=existing.approved_at,
        approved_by=existing.approved_by,
        disabled_reason=existing.disabled_reason,
        permission_error=existing.permission_error,
        last_success_at=existing.last_success_at,
        last_attempt_at=existing.last_attempt_at,
        updated_at=_now_iso(),
    )


def _mark_approved(record: FeishuScopeRecord, approved_by: str) -> FeishuScopeRecord:
    """生成 approved 状态记录。"""
    return replace(
        record,
        status="approved",
        approved_at=record.approved_at or _now_iso(),
        approved_by=approved_by,
        updated_at=_now_iso(),
    )


def _normalized_record(record: FeishuScopeRecord, default_status: str) -> FeishuScopeRecord:
    """标准化写入前的默认字段。"""
    status = record.status or default_status
    return replace(record, status=status, updated_at=record.updated_at or _now_iso())


def _record_matches(record: FeishuScopeRecord, account_id: str, status: str) -> bool:
    """判断记录是否满足列表过滤条件。"""
    if account_id and record.account_id != account_id:
        return False
    if status and record.status != status:
        return False
    return True


def _try_read_scope(markdown_store: MarkdownStore, path: Path) -> FeishuScopeRecord | None:
    """读取单个 scope 文件，异常文件跳过以保证索引可恢复。"""
    try:
        document = markdown_store.read_document(path)
        if document.frontmatter.get("schema") != FEISHU_SCOPE_SCHEMA:
            return None
        return _record_from_frontmatter(document.frontmatter)
    except Exception:  # noqa: BLE001
        return None


def _index_frontmatter() -> dict[str, str]:
    """构造 scope 索引 frontmatter。"""
    return {
        "schema": FEISHU_SCOPE_INDEX_SCHEMA,
        "id": "feishu_scope_index",
        "updated_at": _now_iso(),
    }


def _index_row(root: Path, detail_path: Path, record: FeishuScopeRecord) -> dict[str, str]:
    """构造 scope 索引行。"""
    return {
        "id": record.record_id,
        "account_id": record.account_id,
        "scope_type": record.scope_type,
        "scope_id": record.scope_id,
        "status": record.status,
        "collector_names": _join_values(record.collector_names),
        "discovered_from": record.discovered_from,
        "detail_file": _relative_path(root, detail_path),
    }


def _index_body(rows: Iterable[dict[str, str]]) -> str:
    """渲染 scope 全局索引正文。"""
    headers = ("id", "account_id", "scope_type", "scope_id", "status", "collector_names", "discovered_from", "detail_file")
    lines = ["# Feishu Scope Index", "", "| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _item in headers) + " |"]
    for row in sorted(rows, key=lambda item: (item["account_id"], item["scope_type"], item["scope_id"])):
        lines.append("| " + " | ".join(_cell(row.get(header, "")) for header in headers) + " |")
    return "\n".join(lines) + "\n"


def _split_values(value: str) -> tuple[str, ...]:
    """解析逗号分隔字段。"""
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _join_values(values: Iterable[str]) -> str:
    """把多值字段渲染成 frontmatter 友好的逗号字符串。"""
    return ",".join(value.strip() for value in values if value.strip())


def _frontmatter_value(value: str) -> str:
    """清理 frontmatter 字符串，避免触发复杂 YAML 解析。"""
    clean = str(value).replace("\r", " ").replace("\n", " ").strip()
    if clean.startswith(("[", "{", "-")):
        return "'" + clean.replace("'", "’") + "'"
    return clean


def _cell(value: str) -> str:
    """转义 Markdown 表格单元格。"""
    return str(value).replace("\n", " ").replace("|", "\\|").strip()


def _relative_path(root: Path, path: Path) -> str:
    """返回项目内相对路径。"""
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def _safe_file_part(value: str) -> str:
    """把外部 ID 转成安全文件名片段。"""
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in str(value))
    return safe[:MAX_SAFE_SCOPE_FILE_PART_CHARS] or "unknown"


def _truncate(value: str, limit: int) -> str:
    """裁剪过长状态详情，避免 frontmatter 膨胀。"""
    return value if len(value) <= limit else value[: limit - 3] + "..."


def _now_iso() -> str:
    """返回 UTC ISO 时间字符串。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _self_test() -> None:
    """验证 registry 可以写入并列出 enabled scope。"""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        registry = FeishuScopeRegistry(Path(tmp))
        record = FeishuScopeRecord("account", P2P_CHAT_SCOPE, "oc_1", collector_names=(DIRECT_MESSAGE_COLLECTOR,))
        registry.upsert_candidate(record)
        registry.approve_scope("account", P2P_CHAT_SCOPE, "oc_1")
        registry.enable_scope("account", P2P_CHAT_SCOPE, "oc_1")
        assert registry.list_enabled(DIRECT_MESSAGE_COLLECTOR)[0].scope_id == "oc_1"


if __name__ == "__main__":
    _self_test()
    print("dutyflow feishu scope registry self-test passed")
