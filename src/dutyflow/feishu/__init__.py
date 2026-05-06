# 本文件声明 DutyFlow 的飞书接入层包，使用惰性导出避免包级循环依赖。

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "FeishuClient",
    "FeishuClientResult",
    "FeishuEventAdapter",
    "FeishuEventEnvelope",
    "FeishuIngressResult",
    "FeishuIngressService",
    "FeishuUserRequest",
    "FeishuUserRequestClient",
    "FeishuUserResponse",
    "FeishuUserTokenHealth",
    "FeishuUserTokenProvider",
]

_EXPORT_MAP = {
    "FeishuClient": ("dutyflow.feishu.client", "FeishuClient"),
    "FeishuClientResult": ("dutyflow.feishu.client", "FeishuClientResult"),
    "FeishuEventAdapter": ("dutyflow.feishu.events", "FeishuEventAdapter"),
    "FeishuEventEnvelope": ("dutyflow.feishu.events", "FeishuEventEnvelope"),
    "FeishuIngressResult": ("dutyflow.feishu.runtime", "FeishuIngressResult"),
    "FeishuIngressService": ("dutyflow.feishu.runtime", "FeishuIngressService"),
    "FeishuUserRequest": ("dutyflow.feishu.user_request", "FeishuUserRequest"),
    "FeishuUserRequestClient": (
        "dutyflow.feishu.user_request",
        "FeishuUserRequestClient",
    ),
    "FeishuUserResponse": ("dutyflow.feishu.user_request", "FeishuUserResponse"),
    "FeishuUserTokenHealth": (
        "dutyflow.feishu.user_token_provider",
        "FeishuUserTokenHealth",
    ),
    "FeishuUserTokenProvider": (
        "dutyflow.feishu.user_token_provider",
        "FeishuUserTokenProvider",
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
    from dutyflow.feishu.client import FeishuClient
    from dutyflow.feishu.runtime import FeishuIngressService
    from dutyflow.feishu.user_request import FeishuUserRequestClient
    from dutyflow.feishu.user_token_provider import FeishuUserTokenProvider

    assert FeishuClient is not None
    assert FeishuIngressService is not None
    assert FeishuUserRequestClient is not None
    assert FeishuUserTokenProvider is not None


if __name__ == "__main__":
    _self_test()
    print("dutyflow feishu package self-test passed")
