# 本文件验证飞书客户端默认连接器的 SDK wiring 和本地占位行为。

from __future__ import annotations

from pathlib import Path
import sys
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dutyflow.config.env import EnvConfig  # noqa: E402
from dutyflow.feishu.client import (  # noqa: E402
    FeishuClient,
    _SdkLongConnectionConnector,
    _SdkMissingConnector,
)


class TestFeishuClient(unittest.TestCase):
    """验证飞书客户端在 Step 5 当前阶段的最小真实 wiring。"""

    def test_sdk_missing_connector_returns_clear_result(self) -> None:
        """未安装官方 SDK 时应返回明确占位结果。"""
        result = _SdkMissingConnector().connect(lambda _: None)
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "sdk_missing")

    def test_sdk_connector_uses_ws_client_and_dispatcher_builder(self) -> None:
        """官方 SDK 连接器应按 sample 链路构造 dispatcher 和 ws client。"""
        lark = _FakeLarkModule()
        connector = _SdkLongConnectionConnector(_config(), lark)
        captured: list[dict[str, object]] = []
        result = connector.connect(lambda event: captured.append(dict(event)))
        self.assertTrue(result.ok)
        self.assertEqual(result.status, "listener_started")
        connector._thread.join(timeout=1)
        self.assertEqual(lark.builder_tokens, ("verify", "encrypt"))
        self.assertEqual(lark.last_ws_args[:2], ("app_demo", "secret_demo"))
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0]["header"]["event_id"], "evt_sdk")

    def test_feishu_client_uses_injected_connector(self) -> None:
        """FeishuClient 应继续允许测试注入自定义连接器。"""
        connector = _FakeConnector()
        result = FeishuClient(_config(), connector=connector).connect_long_connection(lambda _: None)
        self.assertTrue(result.ok)
        self.assertTrue(connector.called)


class _FakeConnector:
    """提供最小可注入连接器。"""

    def __init__(self) -> None:
        """记录是否被调用。"""
        self.called = False

    def connect(self, event_handler) -> object:
        """模拟连接建立。"""
        self.called = True
        event_handler({"header": {"event_id": "evt_injected"}, "event": {"message": {"message_id": "msg"}}})
        from dutyflow.feishu.client import FeishuClientResult  # noqa: WPS433

        return FeishuClientResult(ok=True, status="connected", detail="fake connector")


class _FakeLarkModule:
    """模拟官方 SDK 暴露的最小对象集合。"""

    class JSON:
        """模拟 SDK JSON 工具。"""

        @staticmethod
        def marshal(data) -> str:
            """返回固定事件 JSON。"""
            return (
                '{"header": {"event_id": "evt_sdk", "tenant_key": "tenant_demo", "app_id": "app_demo"}, '
                '"event": {"message": {"message_id": "msg_sdk", "chat_id": "oc_demo", "chat_type": "p2p", '
                '"content": "{\\"text\\": \\"hello\\"}"}, '
                '"sender": {"sender_id": {"open_id": "ou_sender"}}}}'
            )

    class LogLevel:
        """模拟 SDK 日志级别常量。"""

        DEBUG = "DEBUG"
        INFO = "INFO"
        WARN = "WARN"
        ERROR = "ERROR"

    class EventDispatcherHandler:
        """模拟 SDK dispatcher builder。"""

        @staticmethod
        def builder(verify_token: str, encrypt_key: str):
            """返回记录参数的 builder。"""
            _FakeLarkModule.builder_tokens = (verify_token, encrypt_key)
            return _FakeDispatcherBuilder()

    class ws:
        """模拟 SDK WebSocket 模块。"""

        class Client:
            """模拟 SDK ws.Client。"""

            def __init__(self, app_id: str, app_secret: str, *, event_handler, log_level) -> None:
                """记录初始化参数。"""
                _FakeLarkModule.last_ws_args = (app_id, app_secret, event_handler, log_level)
                self.event_handler = event_handler

            def start(self) -> None:
                """模拟启动后立即投递一条事件。"""
                dispatcher = self.event_handler
                handler = dispatcher.handlers["im.message.receive_v1"]
                handler(object())

    builder_tokens = ("", "")
    last_ws_args = ("", "", None, None)


class _FakeDispatcherBuilder:
    """模拟官方 SDK 的 dispatcher builder 链式接口。"""

    def __init__(self) -> None:
        """初始化处理器表。"""
        self.handlers: dict[str, object] = {}

    def register_p2_im_message_receive_v1(self, handler):
        """记录消息事件处理器。"""
        self.handlers["im.message.receive_v1"] = handler
        return self

    def build(self):
        """返回可被 ws.Client 消费的简单对象。"""
        return self


def _config() -> EnvConfig:
    """构造最小可用的飞书长连接配置。"""
    return EnvConfig(
        model_api_key="demo-key",
        model_base_url="https://example.invalid/model",
        model_name="demo-model",
        feishu_app_id="app_demo",
        feishu_app_secret="secret_demo",
        feishu_event_verify_token="verify",
        feishu_event_encrypt_key="encrypt",
        feishu_event_callback_url="https://example.invalid/callback",
        feishu_event_mode="long_connection",
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


def _self_test() -> None:
    """运行本文件单元测试。"""
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestFeishuClient)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    _self_test()
