# 本文件负责飞书接入层的客户端骨架，统一封装长连接、资源获取和发送消息入口。

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import threading
from typing import Any, Callable, Mapping, Protocol

from dutyflow.config.env import EnvConfig, validate_feishu_ingress_config


@dataclass(frozen=True)
class FeishuClientResult:
    """表示一次飞书客户端操作的统一结果。"""

    ok: bool
    status: str
    detail: str
    payload: dict[str, Any] = field(default_factory=dict)


class LongConnectionConnector(Protocol):
    """定义长连接连接器的最小注入接口。"""

    def connect(
        self,
        event_handler: Callable[[Mapping[str, Any]], object],
    ) -> FeishuClientResult:
        """启动连接并把收到的原始事件转交给接入层处理。"""


class FeishuClient:
    """统一封装接入层对飞书客户端能力的访问。"""

    def __init__(
        self,
        config: EnvConfig,
        connector: LongConnectionConnector | None = None,
    ) -> None:
        """绑定配置和可选连接器，默认走懒加载 SDK 连接器。"""
        self.config = config
        self.connector = connector

    def connect_long_connection(
        self,
        event_handler: Callable[[Mapping[str, Any]], object],
    ) -> FeishuClientResult:
        """按当前配置启动飞书长连接骨架。"""
        mode = self.config.feishu_event_mode
        if mode != "long_connection":
            return FeishuClientResult(
                ok=False,
                status="disabled",
                detail=f"feishu long connection disabled in event mode: {mode}",
            )
        validation = validate_feishu_ingress_config(self.config)
        if not validation.ok:
            return FeishuClientResult(
                ok=False,
                status="invalid_config",
                detail=validation.message(),
            )
        connector = self.connector or _build_default_connector_for_config(self.config)
        return connector.connect(event_handler)

    def fetch_message_resource(
        self,
        message_id: str,
        file_key: str,
    ) -> FeishuClientResult:
        """保留消息资源获取入口，当前仍返回清晰占位结果。"""
        return FeishuClientResult(
            ok=False,
            status="not_implemented",
            detail="feishu message resource fetch is reserved for Step 5 real API phase",
            payload={"message_id": message_id, "file_key": file_key},
        )

    def send_message(
        self,
        chat_id: str,
        content: str,
        *,
        msg_type: str = "text",
    ) -> FeishuClientResult:
        """保留 Bot 发消息入口，但当前不伪装为真实发送成功。"""
        return FeishuClientResult(
            ok=False,
            status="not_implemented",
            detail="feishu send_message is reserved for Step 10 feedback phase",
            payload={"chat_id": chat_id, "msg_type": msg_type, "content_preview": content[:80]},
        )


class _SdkMissingConnector:
    """在本地未安装官方 SDK 时返回明确说明。"""

    def connect(
        self,
        event_handler: Callable[[Mapping[str, Any]], object],
    ) -> FeishuClientResult:
        """返回清晰占位结果，提示需要安装官方 SDK。"""
        del event_handler
        return FeishuClientResult(
            ok=False,
            status="sdk_missing",
            detail="install lark_oapi to enable Feishu long connection integration",
        )


class _SdkLongConnectionConnector:
    """使用官方 Python SDK 的长连接客户端接收原始飞书事件。"""

    def __init__(self, config: EnvConfig, lark_module: Any) -> None:
        """绑定运行配置和已导入的官方 SDK 模块。"""
        self.config = config
        self.lark = lark_module
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def connect(
        self,
        event_handler: Callable[[Mapping[str, Any]], object],
    ) -> FeishuClientResult:
        """按官方 sample 启动长连接，并把消息事件桥接为原始字典。"""
        with self._lock:
            if self._thread and self._thread.is_alive():
                return FeishuClientResult(
                    ok=True,
                    status="already_running",
                    detail="feishu long connection listener is already running",
                )
            dispatcher = self._build_dispatcher(event_handler)
            thread = threading.Thread(
                target=self._run_client,
                args=(dispatcher,),
                name="dutyflow-feishu-listener",
                daemon=True,
            )
            thread.start()
            self._thread = thread
        return FeishuClientResult(
            ok=True,
            status="listener_started",
            detail="feishu long connection listener started in background",
        )

    def _build_dispatcher(
        self,
        event_handler: Callable[[Mapping[str, Any]], object],
    ) -> object:
        """构造 SDK 事件分发器，只注册 Step 5 初版需要的消息事件。"""
        builder = self.lark.EventDispatcherHandler.builder(
            self.config.feishu_event_verify_token,
            self.config.feishu_event_encrypt_key,
        )
        return builder.register_p2_im_message_receive_v1(
            self._build_message_handler(event_handler)
        ).build()

    def _build_message_handler(
        self,
        event_handler: Callable[[Mapping[str, Any]], object],
    ) -> Callable[[object], None]:
        """把 SDK typed event 转回原始字典，再交给接入层。"""

        def _handle_message(data: object) -> None:
            raw_event = _marshal_sdk_event(self.lark, data)
            event_handler(raw_event)

        return _handle_message

    def _run_client(self, dispatcher: object) -> None:
        """在线程内阻塞运行 SDK WebSocket 客户端。"""
        client = self.lark.ws.Client(
            self.config.feishu_app_id,
            self.config.feishu_app_secret,
            event_handler=dispatcher,
            log_level=_map_lark_log_level(self.lark, self.config.log_level),
        )
        client.start()


def _build_default_connector_for_config(config: EnvConfig) -> LongConnectionConnector:
    """按当前环境和配置返回默认长连接连接器。"""
    try:
        import lark_oapi as lark  # type: ignore
    except ImportError:
        return _SdkMissingConnector()
    return _SdkLongConnectionConnector(config, lark)


def _marshal_sdk_event(lark_module: Any, data: object) -> dict[str, Any]:
    """使用 SDK 自带 JSON 序列化把 typed event 转成原始字典。"""
    payload = lark_module.JSON.marshal(data)
    if not isinstance(payload, str):
        return {}
    parsed = json.loads(payload)
    if isinstance(parsed, Mapping):
        return dict(parsed)
    return {}


def _map_lark_log_level(lark_module: Any, log_level: str) -> Any:
    """把项目内日志级别映射到官方 SDK 的日志级别常量。"""
    normalized = log_level.strip().upper()
    mapping = {
        "DEBUG": "DEBUG",
        "INFO": "INFO",
        "WARNING": "WARN",
        "ERROR": "ERROR",
    }
    target_name = mapping.get(normalized, "INFO")
    return getattr(lark_module.LogLevel, target_name, lark_module.LogLevel.INFO)


def _self_test() -> None:
    """验证禁用模式下会返回明确的长连接占位结果。"""
    config = EnvConfig(
        model_api_key="",
        model_base_url="",
        model_name="",
        feishu_app_id="",
        feishu_app_secret="",
        feishu_event_verify_token="",
        feishu_event_encrypt_key="",
        feishu_event_callback_url="",
        feishu_event_mode="fixture",
        feishu_tenant_key="",
        feishu_owner_open_id="",
        feishu_owner_report_chat_id="",
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
    result = FeishuClient(config).connect_long_connection(lambda _: None)
    assert result.status == "disabled"


if __name__ == "__main__":
    _self_test()
    print("dutyflow feishu client self-test passed")
