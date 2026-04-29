# 本文件验证飞书客户端默认连接器的 SDK wiring 和本地占位行为。

from __future__ import annotations

import asyncio
import base64
from enum import Enum
import json
from pathlib import Path
import sys
import time
from types import ModuleType
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
    _SdkEventHandlerBridge,
    _install_card_frame_handler_patch,
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
        self.assertEqual(lark.builder_tokens, ("encrypt", "verify"))
        self.assertEqual(lark.last_ws_args[:2], ("app_demo", "secret_demo"))
        self.assertIsNotNone(lark.ws.client.loop)
        latest = connector.latest_status()
        self.assertIsNotNone(latest)
        self.assertEqual(latest.payload["raw_event_count"], 1)
        self.assertEqual(latest.payload["last_raw_event_summary"]["event_id"], "evt_sdk")
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0]["header"]["event_id"], "evt_sdk")

    def test_feishu_client_uses_injected_connector(self) -> None:
        """FeishuClient 应继续允许测试注入自定义连接器。"""
        connector = _FakeConnector()
        result = FeishuClient(_config(), connector=connector).connect_long_connection(lambda _: None)
        self.assertTrue(result.ok)
        self.assertTrue(connector.called)

    def test_send_message_uses_sdk_create_message_api(self) -> None:
        """发送消息应走 SDK 的 client.im.v1.message.create 链路。"""
        lark = _FakeLarkModule()
        result = FeishuClient(_config(), sdk_module=lark).send_message("oc_bind", "绑定成功")
        self.assertTrue(result.ok)
        self.assertEqual(result.status, "sent")
        self.assertEqual(lark.sent_payload["receive_id_type"], "chat_id")
        self.assertEqual(lark.sent_payload["receive_id"], "oc_bind")
        self.assertEqual(lark.sent_payload["msg_type"], "text")

    def test_send_interactive_card_uses_interactive_msg_type(self) -> None:
        """发送审批卡片应走 interactive 消息类型。"""
        lark = _FakeLarkModule()
        result = FeishuClient(_config(), sdk_module=lark).send_interactive_card(
            "oc_owner",
            {"config": {"wide_screen_mode": True}},
        )
        self.assertTrue(result.ok)
        self.assertEqual(lark.sent_payload["receive_id"], "oc_owner")
        self.assertEqual(lark.sent_payload["msg_type"], "interactive")
        self.assertIn("wide_screen_mode", lark.sent_payload["content"])

    def test_sdk_dispatcher_registers_card_action_trigger(self) -> None:
        """长连接 dispatcher 应注册飞书卡片按钮回调。"""
        lark = _FakeLarkModule()
        connector = _SdkLongConnectionConnector(_config(), lark)
        captured: list[dict[str, object]] = []
        _FakeLarkModule.marshal_payload = (
            '{"header": {"event_id": "evt_card", "event_type": "card.action.trigger"}, '
            '"event": {"operator": {"open_id": "ou_owner"}, '
            '"action": {"value": {"dutyflow_action": "approval_decision"}}}}'
        )
        dispatcher = connector._build_dispatcher(
            lambda event: captured.append(dict(event)) or {"toast": {"type": "success", "content": "ok"}}
        )
        response = dispatcher.handlers["card.action.trigger"](object())
        _FakeLarkModule.marshal_payload = ""
        self.assertEqual(captured[0]["header"]["event_id"], "evt_card")
        self.assertEqual(response["toast"]["content"], "ok")

    def test_sdk_card_frame_is_dispatched_and_acknowledged(self) -> None:
        """SDK WebSocket card 帧应进入卡片回调 dispatcher 并返回 ack。"""
        captured: list[dict[str, object]] = []
        fake_lark, cleanup = _install_fake_ws_sdk_module()
        dispatcher = _FakeRawDispatcher(captured)
        client = _FakeWsFrameClient(dispatcher)
        _install_card_frame_handler_patch(client, fake_lark)
        frame = _build_ws_data_frame("card", _card_action_payload())
        try:
            asyncio.run(client._handle_data_frame(frame))
            response_payload = json.loads(client.sent_payloads[0].decode("utf-8"))
            response_data = base64.b64decode(response_payload["data"]).decode("utf-8")
            self.assertEqual(captured[0]["header"]["event_id"], "evt_card")
            self.assertEqual(response_payload["code"], 200)
            self.assertIn('"content": "ok"', response_data)
        finally:
            cleanup()

    def test_legacy_card_frame_uses_raw_handler_and_acknowledges(self) -> None:
        """旧版 card.action.trigger_v1 帧应绕过 SDK typed dispatcher 进入原始接入层。"""
        captured: list[dict[str, object]] = []
        fake_lark, cleanup = _install_fake_ws_sdk_module()
        bridge = _SdkEventHandlerBridge(
            _FailingRawDispatcher(),
            lambda summary: None,
            lambda event: captured.append(dict(event)) or {"toast": {"type": "success", "content": "ok"}},
        )
        client = _FakeWsFrameClient(bridge)
        _install_card_frame_handler_patch(client, fake_lark)
        frame = _build_ws_data_frame("card", _legacy_card_action_payload())
        try:
            asyncio.run(client._handle_data_frame(frame))
            response_payload = json.loads(client.sent_payloads[0].decode("utf-8"))
            response_data = base64.b64decode(response_payload["data"]).decode("utf-8")
            self.assertEqual(captured[0]["type"], "card.action.trigger_v1")
            self.assertEqual(response_payload["code"], 200)
            self.assertIn('"content": "ok"', response_data)
        finally:
            cleanup()

    def test_card_action_event_frame_bypasses_sdk_typed_dispatcher(self) -> None:
        """event 类型的 card.action.trigger 也应绕过 SDK typed dispatcher。"""
        captured: list[dict[str, object]] = []
        fake_lark, cleanup = _install_fake_ws_sdk_module()
        bridge = _SdkEventHandlerBridge(
            _FailingRawDispatcher(),
            lambda summary: None,
            lambda event: captured.append(dict(event)) or {"toast": {"type": "success", "content": "ok"}},
        )
        client = _FakeWsFrameClient(bridge)
        _install_card_frame_handler_patch(client, fake_lark)
        frame = _build_ws_data_frame("event", _card_action_payload())
        try:
            asyncio.run(client._handle_data_frame(frame))
            response_payload = json.loads(client.sent_payloads[0].decode("utf-8"))
            response_data = base64.b64decode(response_payload["data"]).decode("utf-8")
            self.assertEqual(captured[0]["header"]["event_type"], "card.action.trigger")
            self.assertEqual(response_payload["code"], 200)
            self.assertIn('"content": "ok"', response_data)
        finally:
            cleanup()


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

    def latest_status(self):
        """返回最近一次固定状态，兼容新版连接器协议。"""
        from dutyflow.feishu.client import FeishuClientResult  # noqa: WPS433

        if not self.called:
            return None
        return FeishuClientResult(ok=True, status="connected", detail="fake connector")


class _FakeLarkModule:
    """模拟官方 SDK 暴露的最小对象集合。"""

    class JSON:
        """模拟 SDK JSON 工具。"""

        @staticmethod
        def marshal(data) -> str:
            """返回固定事件 JSON。"""
            if _FakeLarkModule.marshal_payload:
                return _FakeLarkModule.marshal_payload
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
        def builder(encrypt_key: str, verify_token: str):
            """返回记录参数的 builder。"""
            _FakeLarkModule.builder_tokens = (encrypt_key, verify_token)
            return _FakeDispatcherBuilder()

    class Client:
        """模拟 SDK API client builder。"""

        @staticmethod
        def builder():
            """返回链式 builder。"""
            return _FakeClientBuilder()

    class CreateMessageRequest:
        """模拟 SDK 创建消息请求对象。"""

        @staticmethod
        def builder():
            """返回消息请求 builder。"""
            return _FakeCreateMessageRequestBuilder()

    class CreateMessageRequestBody:
        """模拟 SDK 创建消息请求体对象。"""

        @staticmethod
        def builder():
            """返回消息请求体 builder。"""
            return _FakeCreateMessageRequestBodyBuilder()

    class ws:
        """模拟 SDK WebSocket 模块。"""

        class client:
            """模拟 SDK `ws.client` 模块对象。"""

            loop = None

        class Client:
            """模拟 SDK ws.Client。"""

            def __init__(self, app_id: str, app_secret: str, *, event_handler, log_level) -> None:
                """记录初始化参数。"""
                _FakeLarkModule.last_ws_args = (app_id, app_secret, event_handler, log_level)
                self.event_handler = event_handler

            def start(self) -> None:
                """模拟启动后立即投递一条事件。"""
                dispatcher = getattr(self.event_handler, "dispatcher", self.event_handler)
                handler = dispatcher.handlers["im.message.receive_v1"]
                if hasattr(self.event_handler, "do_without_validation"):
                    payload = (
                        b'{"header": {"event_id": "evt_sdk", "event_type": "im.message.receive_v1", '
                        b'"tenant_key": "tenant_demo", "app_id": "app_demo"}, '
                        b'"event": {"message": {"message_id": "msg_sdk", "chat_id": "oc_demo", '
                        b'"chat_type": "p2p", "content": "{\\"text\\": \\"hello\\"}"}, '
                        b'"sender": {"sender_id": {"open_id": "ou_sender"}}}}'
                    )
                    self.event_handler.do_without_validation(payload)
                else:
                    handler(object())
                time.sleep(0.6)

    builder_tokens = ("", "")
    last_ws_args = ("", "", None, None)
    sent_payload = {}
    marshal_payload = ""


class _FakeDispatcherBuilder:
    """模拟官方 SDK 的 dispatcher builder 链式接口。"""

    def __init__(self) -> None:
        """初始化处理器表。"""
        self.handlers: dict[str, object] = {}

    def register_p2_im_message_receive_v1(self, handler):
        """记录消息事件处理器。"""
        self.handlers["im.message.receive_v1"] = handler
        return self

    def register_p2_card_action_trigger(self, handler):
        """记录卡片按钮回调处理器。"""
        self.handlers["card.action.trigger"] = handler
        return self

    def build(self):
        """返回可被 ws.Client 消费的简单对象。"""
        return self

    def do_without_validation(self, payload):
        """模拟 SDK dispatcher 对原始 payload 的分发。"""
        del payload
        handler = self.handlers["im.message.receive_v1"]
        handler(object())


class _FakeClientBuilder:
    """模拟 SDK API client builder。"""

    def __init__(self) -> None:
        """初始化 builder 状态。"""
        self.app_id_value = ""
        self.app_secret_value = ""

    def app_id(self, app_id: str):
        """记录 app_id。"""
        self.app_id_value = app_id
        return self

    def app_secret(self, app_secret: str):
        """记录 app_secret。"""
        self.app_secret_value = app_secret
        return self

    def log_level(self, log_level):
        """记录日志级别。"""
        self.log_level_value = log_level
        return self

    def build(self):
        """返回带 message.create 的最小 client。"""
        return _FakeApiClient()


class _FakeApiClient:
    """模拟 SDK 的 `client.im.v1.message.create` 调用对象。"""

    class _Im:
        """模拟 `client.im`。"""

        class _V1:
            """模拟 `client.im.v1`。"""

            class _Message:
                """模拟 `client.im.v1.message`。"""

                @staticmethod
                def create(request):
                    """记录发送请求并返回成功响应。"""
                    _FakeLarkModule.sent_payload = {
                        "receive_id_type": request.receive_id_type,
                        "receive_id": request.request_body.receive_id,
                        "msg_type": request.request_body.msg_type,
                        "content": request.request_body.content,
                    }
                    return _FakeMessageResponse()

            def __init__(self) -> None:
                """挂载 message service。"""
                self.message = self._Message()

        def __init__(self) -> None:
            """挂载 v1 service。"""
            self.v1 = self._V1()

    def __init__(self) -> None:
        """挂载 im service。"""
        self.im = self._Im()


class _FakeCreateMessageRequestBuilder:
    """模拟消息请求 builder。"""

    def __init__(self) -> None:
        """初始化请求对象。"""
        self.request = type("Req", (), {"receive_id_type": "", "request_body": None})()

    def receive_id_type(self, receive_id_type: str):
        """记录 receive_id_type。"""
        self.request.receive_id_type = receive_id_type
        return self

    def request_body(self, request_body):
        """记录请求体。"""
        self.request.request_body = request_body
        return self

    def build(self):
        """返回最终请求对象。"""
        return self.request


class _FakeCreateMessageRequestBodyBuilder:
    """模拟消息请求体 builder。"""

    def __init__(self) -> None:
        """初始化请求体对象。"""
        self.body = type("Body", (), {"receive_id": "", "msg_type": "", "content": ""})()

    def receive_id(self, receive_id: str):
        """记录 chat_id。"""
        self.body.receive_id = receive_id
        return self

    def msg_type(self, msg_type: str):
        """记录消息类型。"""
        self.body.msg_type = msg_type
        return self

    def content(self, content: str):
        """记录消息内容。"""
        self.body.content = content
        return self

    def build(self):
        """返回最终请求体。"""
        return self.body


class _FakeMessageResponse:
    """模拟成功的发送消息响应。"""

    def __init__(self) -> None:
        """构造成功响应。"""
        self.code = 0
        self.msg = "ok"
        self.data = type("RespData", (), {"message_id": "om_reply"})()


class _FakeWsFrameClient:
    """模拟 SDK ws.Client 中 data frame 处理依赖的最小对象。"""

    def __init__(self, event_handler) -> None:
        """保存 dispatcher 和响应帧。"""
        self._event_handler = event_handler
        self.sent_payloads: list[bytes] = []

    def _combine(self, message_id: str, sum_value: int, seq: int, payload: bytes) -> bytes:
        """测试中不做分片合并，直接返回 payload。"""
        del message_id, sum_value, seq
        return payload

    async def _write_message(self, data: bytes) -> None:
        """记录待发送回飞书的响应 frame。"""
        self.sent_payloads.append(data)


class _FakeRawDispatcher:
    """模拟 SDK dispatcher 的原始 payload 分发入口。"""

    def __init__(self, captured: list[dict[str, object]]) -> None:
        """保存捕获列表。"""
        self.captured = captured

    def do_without_validation(self, payload: bytes) -> dict[str, object]:
        """解析原始 payload 并返回卡片回调 ack。"""
        event = json.loads(payload.decode("utf-8"))
        self.captured.append(event)
        return {"toast": {"type": "success", "content": "ok"}}


class _FailingRawDispatcher:
    """确保旧版 card frame 测试不会误走 SDK typed dispatcher。"""

    def do_without_validation(self, payload: bytes) -> dict[str, object]:
        """旧版卡片帧如果进入这里，说明兼容链路失效。"""
        del payload
        raise AssertionError("legacy card frame should bypass SDK dispatcher")


class _FakeMessageType(Enum):
    """模拟 SDK WebSocket 消息类型枚举。"""

    EVENT = "event"
    CARD = "card"


class _FakeWsResponse:
    """模拟 SDK WebSocket 响应对象。"""

    def __init__(self, code: int, data: bytes | None = None) -> None:
        """保存响应码和可选 callback data。"""
        self.code = code
        self.data = data


class _FakeWsJSON:
    """模拟 SDK JSON 工具。"""

    @staticmethod
    def marshal(value) -> str:
        """把响应对象和普通对象转换为 JSON。"""
        if isinstance(value, _FakeWsResponse):
            data = value.data.decode("utf-8") if value.data else ""
            return json.dumps({"code": value.code, "data": data})
        return json.dumps(value, ensure_ascii=False)


class _FakeFrameHeader:
    """模拟 SDK protobuf header 对象。"""

    def __init__(self) -> None:
        """初始化空 header。"""
        self.key = ""
        self.value = ""


class _FakeFrameHeaders(list):
    """模拟 SDK protobuf headers 容器。"""

    def add(self) -> _FakeFrameHeader:
        """追加并返回一个 header。"""
        header = _FakeFrameHeader()
        self.append(header)
        return header


class _FakeWsFrame:
    """模拟 SDK WebSocket data frame。"""

    def __init__(self) -> None:
        """初始化 headers 和 payload。"""
        self.headers = _FakeFrameHeaders()
        self.payload = b""

    def SerializeToString(self) -> bytes:
        """测试中直接把响应 payload 作为发送内容。"""
        return self.payload


class _FakeWsLogger:
    """模拟 SDK logger。"""

    @staticmethod
    def error(*args) -> None:
        """测试中忽略错误日志。"""
        del args


def _install_fake_ws_sdk_module():
    """安装一个临时 SDK ws.client 模块，供 card frame patch 测试导入。"""
    root_name = "fake_lark_for_card_frame"
    root = ModuleType(root_name)
    root.__name__ = root_name
    ws = ModuleType(root_name + ".ws")
    client = ModuleType(root_name + ".ws.client")
    _populate_fake_ws_client_module(client)
    sys.modules[root_name] = root
    sys.modules[root_name + ".ws"] = ws
    sys.modules[root_name + ".ws.client"] = client

    def _cleanup() -> None:
        for name in (root_name + ".ws.client", root_name + ".ws", root_name):
            sys.modules.pop(name, None)

    return root, _cleanup


def _populate_fake_ws_client_module(module: ModuleType) -> None:
    """填充 card frame patch 所需的 SDK ws.client 字段。"""
    module.MessageType = _FakeMessageType
    module.Response = _FakeWsResponse
    module.JSON = _FakeWsJSON
    module.UTF_8 = "utf-8"
    module.HEADER_MESSAGE_ID = "message_id"
    module.HEADER_TRACE_ID = "trace_id"
    module.HEADER_SUM = "sum"
    module.HEADER_SEQ = "seq"
    module.HEADER_TYPE = "type"
    module.HEADER_BIZ_RT = "biz_rt"
    module.logger = _FakeWsLogger()
    module._get_by_key = _get_fake_header_by_key


def _get_fake_header_by_key(headers: list[_FakeFrameHeader], key: str) -> str:
    """按 key 读取 fake frame header。"""
    for header in headers:
        if header.key == key:
            return header.value
    raise KeyError(key)


def _build_ws_data_frame(message_type: str, payload: dict[str, object]):
    """构造 SDK WebSocket data frame 测试对象。"""
    frame = _FakeWsFrame()
    _add_frame_header(frame, "message_id", "msg_card")
    _add_frame_header(frame, "trace_id", "trace_card")
    _add_frame_header(frame, "sum", "1")
    _add_frame_header(frame, "seq", "1")
    _add_frame_header(frame, "type", message_type)
    frame.payload = json.dumps(payload).encode("utf-8")
    return frame


def _add_frame_header(frame, key: str, value: str) -> None:
    """向测试 frame 追加一个 header。"""
    header = frame.headers.add()
    header.key = key
    header.value = value


def _card_action_payload() -> dict[str, object]:
    """构造飞书卡片按钮回调 payload。"""
    return {
        "schema": "2.0",
        "header": {
            "event_id": "evt_card",
            "event_type": "card.action.trigger",
            "tenant_key": "tenant_demo",
            "token": "",
        },
        "event": {
            "operator": {"open_id": "ou_owner"},
            "context": {"open_chat_id": "oc_owner"},
            "action": {
                "value": {
                    "dutyflow_action": "approval_decision",
                    "approval_id": "approval_001",
                    "resume_token": "resume_001",
                    "decision_result": "approved",
                }
            },
        },
    }


def _legacy_card_action_payload() -> dict[str, object]:
    """构造旧版飞书卡片按钮回调 payload。"""
    return {
        "uuid": "uuid_card",
        "type": "card.action.trigger_v1",
        "tenant_key": "tenant_demo",
        "open_id": "ou_owner",
        "open_chat_id": "oc_owner",
        "open_message_id": "om_card",
        "action": {
            "value": {
                "dutyflow_action": "approval_decision",
                "approval_id": "approval_001",
                "resume_token": "resume_001",
                "decision_result": "approved",
            }
        },
    }


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
