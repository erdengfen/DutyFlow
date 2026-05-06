# 本文件负责通过用户 OAuth 会话发现飞书群组并写入 scope registry candidate。

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from dutyflow.feishu.collector_budget import CollectorBudget, CollectorBudgetGuard
from dutyflow.feishu.scope_registry import (
    GROUP_CHAT_SCOPE,
    GROUP_DOCUMENT_COLLECTOR,
    GROUP_MESSAGE_COLLECTOR,
    FeishuScopeRecord,
    FeishuScopeRegistry,
    scope_account_id_from_config,
)
from dutyflow.feishu.user_client import FeishuUserClient
from dutyflow.feishu.user_request import FeishuUserResponse

DISCOVERY_NAME = "group_candidate_discovery"
_FEISHU_CHATS_URL = "https://open.feishu.cn/open-apis/im/v1/chats"
# 关键开关：群组发现单次请求 100 个会话，等于飞书接口允许的最大单页条数。
_DISCOVERY_PAGE_SIZE = 100
# 关键开关：群组发现单次最多翻 5 页，避免大型组织一次发现写入过量 candidate scope。
MAX_DISCOVERY_PAGES = 5
# 关键开关：群组发现单轮最多写入 500 个候选 scope，与 5 页 * 每页 100 个会话预算对齐。
MAX_DISCOVERY_ITEMS = MAX_DISCOVERY_PAGES * _DISCOVERY_PAGE_SIZE


@dataclass(frozen=True)
class GroupCandidateDiscoveryResult:
    """表示 group_candidate_discovery 单轮发现结果。"""

    ok: bool
    status: str
    scopes_written: int
    scope_records: tuple[FeishuScopeRecord, ...]
    has_more: bool
    detail: str


class GroupCandidateDiscovery:
    """通过用户 OAuth 拉取群会话列表并写入 scope registry candidate。"""

    def __init__(
        self,
        project_root: Path,
        user_client: FeishuUserClient,
        config: object,
        *,
        registry: FeishuScopeRegistry | None = None,
        budget: CollectorBudget | None = None,
    ) -> None:
        """绑定用户面 client、scope registry 和单轮预算。"""
        self.project_root = Path(project_root).resolve()
        self.user_client = user_client
        self.config = config
        self.registry = registry or FeishuScopeRegistry(self.project_root)
        self.budget = budget or CollectorBudget(
            collector_name=DISCOVERY_NAME,
            max_pages_per_run=MAX_DISCOVERY_PAGES,
            max_items_per_run=MAX_DISCOVERY_ITEMS,
        )

    def discover(self, *, save_raw: bool = False) -> GroupCandidateDiscoveryResult:
        """翻页拉取群列表，把非 p2p 群写入 candidate 后返回发现结果。"""
        guard = CollectorBudgetGuard(self.budget)
        account_id = scope_account_id_from_config(self.config)
        records: list[FeishuScopeRecord] = []
        page_token = ""
        has_more = False
        while guard.record_page():
            response = self._request_page(page_token, save_raw)
            if not response.ok:
                return GroupCandidateDiscoveryResult(
                    ok=False,
                    status=response.status,
                    scopes_written=len(records),
                    scope_records=tuple(records),
                    has_more=False,
                    detail=response.detail,
                )
            stopped_by_item_budget = False
            for item in _response_items(response):
                record = _item_to_scope_record(item, account_id, self.config)
                if record is None:
                    continue
                if not guard.can_accept_item():
                    has_more = True
                    stopped_by_item_budget = True
                    break
                written = self.registry.upsert_candidate(record)
                guard.record_item()
                records.append(written)
            has_more = response.has_more or stopped_by_item_budget
            page_token = response.page_token
            if stopped_by_item_budget or not has_more or not page_token:
                break
        return GroupCandidateDiscoveryResult(
            ok=True,
            status="ok",
            scopes_written=len(records),
            scope_records=tuple(records),
            has_more=has_more,
            detail="",
        )

    def _request_page(self, page_token: str, save_raw: bool) -> FeishuUserResponse:
        """请求一页群组会话列表。"""
        params: dict[str, Any] = {
            "page_size": _DISCOVERY_PAGE_SIZE,
            "user_id_type": "open_id",
        }
        if page_token:
            params["page_token"] = page_token
        return self.user_client.get(
            _FEISHU_CHATS_URL,
            params=params,
            timeout_seconds=self.budget.request_timeout_seconds,
            trace_id=DISCOVERY_NAME,
            collector_name=DISCOVERY_NAME,
            save_raw=save_raw,
        )


def _item_to_scope_record(
    item: Mapping[str, Any],
    account_id: str,
    config: object,
) -> FeishuScopeRecord | None:
    """把会话列表 item 转为 group_chat scope 候选；p2p 会话直接跳过。"""
    chat_id = _as_text(item.get("chat_id"))
    chat_mode = _as_text(item.get("chat_mode"))
    if not chat_id or chat_mode == "p2p":
        return None
    tenant_key = str(getattr(config, "feishu_tenant_key", "")).strip()
    owner_open_id = str(getattr(config, "feishu_owner_open_id", "")).strip()
    return FeishuScopeRecord(
        account_id=account_id,
        scope_type=GROUP_CHAT_SCOPE,
        scope_id=chat_id,
        status="candidate",
        collector_names=(GROUP_MESSAGE_COLLECTOR, GROUP_DOCUMENT_COLLECTOR),
        discovered_from="oauth_chat_list",
        tenant_key=tenant_key,
        owner_open_id=owner_open_id,
        owner_user_id=_as_text(item.get("owner_user_id")),
        source_id=chat_id,
        source_chat_id=chat_id,
    )


def _response_items(response: FeishuUserResponse) -> tuple[Mapping[str, Any], ...]:
    """从统一响应中提取会话 item 列表。"""
    items = response.data.get("items")
    if not isinstance(items, list):
        return ()
    return tuple(dict(item) for item in items if isinstance(item, Mapping))


def _as_text(value: Any) -> str:
    """把值稳定转换为去空白字符串。"""
    if value is None:
        return ""
    return str(value).strip()


def _self_test() -> None:
    """验证发现结果可正确过滤 p2p 会话并写入 candidate。"""
    import tempfile

    class _FakeClient:
        def get(self, url, *, params, timeout_seconds, trace_id, collector_name, save_raw):
            return FeishuUserResponse(
                ok=True,
                status="ok",
                http_status=200,
                feishu_code=0,
                detail="",
                data={
                    "items": [
                        {"chat_id": "oc_group1", "chat_mode": "group", "owner_user_id": "ou_1"},
                        {"chat_id": "oc_p2p1", "chat_mode": "p2p", "owner_user_id": "ou_1"},
                        {"chat_id": "oc_group2", "chat_mode": "group", "owner_user_id": "ou_1"},
                    ],
                    "has_more": False,
                    "page_token": "",
                },
                page_token="",
                has_more=False,
                raw_path="",
            )

    class _FakeConfig:
        feishu_tenant_key = "tk_1"
        feishu_owner_open_id = "ou_1"

    with tempfile.TemporaryDirectory() as tmp:
        discovery = GroupCandidateDiscovery(Path(tmp), _FakeClient(), _FakeConfig())
        result = discovery.discover()
        assert result.ok, f"expected ok, got {result.status}: {result.detail}"
        assert result.scopes_written == 2
        ids = {r.scope_id for r in result.scope_records}
        assert "oc_group1" in ids
        assert "oc_group2" in ids
        assert "oc_p2p1" not in ids
        assert result.scope_records[0].scope_type == GROUP_CHAT_SCOPE
        assert result.scope_records[0].status == "candidate"
        assert GROUP_MESSAGE_COLLECTOR in result.scope_records[0].collector_names
        assert GROUP_DOCUMENT_COLLECTOR in result.scope_records[0].collector_names


if __name__ == "__main__":
    _self_test()
    print("dutyflow feishu group candidate discovery self-test passed")
