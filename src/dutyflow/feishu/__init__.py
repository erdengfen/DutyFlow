# 本文件声明 DutyFlow 的飞书接入层包，使用惰性导出避免包级循环依赖。

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "CollectorBudget",
    "CollectorBudgetGuard",
    "CollectorBudgetUsage",
    "AmbientContextRecord",
    "AmbientContextStore",
    "AmbientContextWriteResult",
    "AmbientDocLink",
    "AmbientFileClue",
    "DirectMessageCollectResult",
    "DirectMessageCollector",
    "FeishuScopeRecord",
    "FeishuScopeRegistry",
    "FeishuClient",
    "FeishuClientResult",
    "FeishuEventAdapter",
    "FeishuEventEnvelope",
    "FeishuIngressResult",
    "FeishuIngressService",
    "FeishuCollectorSyncState",
    "FeishuSyncStateStore",
    "FeishuUserRequest",
    "FeishuUserRequestClient",
    "FeishuUserResponse",
    "FeishuUserClient",
    "FeishuUserTokenHealth",
    "FeishuUserTokenProvider",
    "seed_owner_p2p_scope",
    "scope_account_id_from_config",
]

_EXPORT_MAP = {
    "AmbientContextRecord": ("dutyflow.feishu.ambient_context", "AmbientContextRecord"),
    "AmbientContextStore": ("dutyflow.feishu.ambient_context", "AmbientContextStore"),
    "AmbientContextWriteResult": (
        "dutyflow.feishu.ambient_context",
        "AmbientContextWriteResult",
    ),
    "AmbientDocLink": ("dutyflow.feishu.ambient_context", "AmbientDocLink"),
    "AmbientFileClue": ("dutyflow.feishu.ambient_context", "AmbientFileClue"),
    "CollectorBudget": ("dutyflow.feishu.collector_budget", "CollectorBudget"),
    "CollectorBudgetGuard": (
        "dutyflow.feishu.collector_budget",
        "CollectorBudgetGuard",
    ),
    "CollectorBudgetUsage": (
        "dutyflow.feishu.collector_budget",
        "CollectorBudgetUsage",
    ),
    "DirectMessageCollectResult": (
        "dutyflow.feishu.collectors.direct_message_collector",
        "DirectMessageCollectResult",
    ),
    "DirectMessageCollector": (
        "dutyflow.feishu.collectors.direct_message_collector",
        "DirectMessageCollector",
    ),
    "FeishuScopeRecord": ("dutyflow.feishu.scope_registry", "FeishuScopeRecord"),
    "FeishuScopeRegistry": ("dutyflow.feishu.scope_registry", "FeishuScopeRegistry"),
    "FeishuClient": ("dutyflow.feishu.client", "FeishuClient"),
    "FeishuClientResult": ("dutyflow.feishu.client", "FeishuClientResult"),
    "FeishuEventAdapter": ("dutyflow.feishu.events", "FeishuEventAdapter"),
    "FeishuEventEnvelope": ("dutyflow.feishu.events", "FeishuEventEnvelope"),
    "FeishuIngressResult": ("dutyflow.feishu.runtime", "FeishuIngressResult"),
    "FeishuIngressService": ("dutyflow.feishu.runtime", "FeishuIngressService"),
    "FeishuCollectorSyncState": (
        "dutyflow.feishu.sync_state",
        "FeishuCollectorSyncState",
    ),
    "FeishuSyncStateStore": ("dutyflow.feishu.sync_state", "FeishuSyncStateStore"),
    "FeishuUserRequest": ("dutyflow.feishu.user_request", "FeishuUserRequest"),
    "FeishuUserRequestClient": (
        "dutyflow.feishu.user_request",
        "FeishuUserRequestClient",
    ),
    "FeishuUserResponse": ("dutyflow.feishu.user_request", "FeishuUserResponse"),
    "FeishuUserClient": ("dutyflow.feishu.user_client", "FeishuUserClient"),
    "FeishuUserTokenHealth": (
        "dutyflow.feishu.user_token_provider",
        "FeishuUserTokenHealth",
    ),
    "FeishuUserTokenProvider": (
        "dutyflow.feishu.user_token_provider",
        "FeishuUserTokenProvider",
    ),
    "seed_owner_p2p_scope": ("dutyflow.feishu.scope_registry", "seed_owner_p2p_scope"),
    "scope_account_id_from_config": (
        "dutyflow.feishu.scope_registry",
        "scope_account_id_from_config",
    ),
}


def __getattr__(name: str) -> Any:
    """按需导出飞书接入层类型，避免模块初始化时互相引用。"""
    target = _EXPORT_MAP.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = target
    module = import_module(module_name)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value


def _self_test() -> None:
    """验证飞书接入层包导出的核心类型可被正常导入。"""
    from dutyflow.feishu.ambient_context import AmbientContextStore
    from dutyflow.feishu.collector_budget import CollectorBudget
    from dutyflow.feishu.collectors.direct_message_collector import DirectMessageCollector
    from dutyflow.feishu.client import FeishuClient
    from dutyflow.feishu.runtime import FeishuIngressService
    from dutyflow.feishu.scope_registry import FeishuScopeRegistry
    from dutyflow.feishu.sync_state import FeishuSyncStateStore
    from dutyflow.feishu.user_client import FeishuUserClient
    from dutyflow.feishu.user_request import FeishuUserRequestClient
    from dutyflow.feishu.user_token_provider import FeishuUserTokenProvider

    assert AmbientContextStore is not None
    assert CollectorBudget is not None
    assert DirectMessageCollector is not None
    assert FeishuClient is not None
    assert FeishuIngressService is not None
    assert FeishuScopeRegistry is not None
    assert FeishuSyncStateStore is not None
    assert FeishuUserClient is not None
    assert FeishuUserRequestClient is not None
    assert FeishuUserTokenProvider is not None


if __name__ == "__main__":
    _self_test()
    print("dutyflow feishu package self-test passed")
