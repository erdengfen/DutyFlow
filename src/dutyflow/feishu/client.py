# 本文件负责飞书接入层的客户端骨架，统一封装长连接、资源获取和发送消息入口。

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass, field
import http
import importlib
import json
import os
from pathlib import Path
import sys
import threading
import time
import uuid
from typing import Any, Callable, Mapping, Protocol

if __package__ in {None, ""}:
    _SRC_ROOT = Path(__file__).resolve().parents[2]
    if str(_SRC_ROOT) not in sys.path:
        sys.path.insert(0, str(_SRC_ROOT))

from dutyflow.config.env import EnvConfig, validate_feishu_ingress_config

_UNHANDLED_WS_MESSAGE = object()


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

    def latest_status(self) -> FeishuClientResult | None:
        """返回连接器最近一次可观测状态。"""


class FeishuClient:
    """统一封装接入层对飞书客户端能力的访问。"""

    def __init__(
        self,
        config: EnvConfig,
        connector: LongConnectionConnector | None = None,
        sdk_module: Any | None = None,
    ) -> None:
        """绑定配置和可选连接器，默认走懒加载 SDK 连接器。"""
        self.config = config
        self.connector = connector
        self.sdk_module = sdk_module
        self.latest_listener_result: FeishuClientResult | None = None

    def connect_long_connection(
        self,
        event_handler: Callable[[Mapping[str, Any]], object],
    ) -> FeishuClientResult:
        """按当前配置启动飞书长连接骨架。"""
        mode = self.config.feishu_event_mode
        if mode != "long_connection":
            self.latest_listener_result = FeishuClientResult(
                ok=False,
                status="disabled",
                detail=f"feishu long connection disabled in event mode: {mode}",
            )
            return self.latest_listener_result
        validation = validate_feishu_ingress_config(self.config)
        if not validation.ok:
            self.latest_listener_result = FeishuClientResult(
                ok=False,
                status="invalid_config",
                detail=validation.message(),
            )
            return self.latest_listener_result
        if self.connector is None:
            self.connector = _build_default_connector_for_config(self.config, self.sdk_module)
        self.latest_listener_result = self.connector.connect(event_handler)
        return self.latest_listener_result

    def get_listener_status(self) -> FeishuClientResult | None:
        """返回最近一次监听器状态，优先使用连接器内部最新状态。"""
        if self.connector is not None:
            getter = getattr(self.connector, "latest_status", None)
            if callable(getter):
                latest_status = getter()
                if latest_status is not None:
                    self.latest_listener_result = latest_status
        return self.latest_listener_result

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
        """使用应用身份向指定 chat_id 发送一条最小文本消息。"""
        return self._send_message_payload(
            chat_id,
            msg_type,
            json.dumps({"text": content}, ensure_ascii=False),
        )

    def send_interactive_card(
        self,
        chat_id: str,
        card_content: Mapping[str, Any],
    ) -> FeishuClientResult:
        """使用应用身份向指定 chat_id 发送一张交互式卡片。"""
        return self._send_message_payload(
            chat_id,
            "interactive",
            json.dumps(dict(card_content), ensure_ascii=False),
        )

    def _send_message_payload(
        self,
        chat_id: str,
        msg_type: str,
        content_payload: str,
    ) -> FeishuClientResult:
        """按指定 msg_type 和已序列化 content 调用飞书消息发送接口。"""
        try:
            lark = self.sdk_module or _import_lark_sdk()
        except ImportError:
            return FeishuClientResult(
                ok=False,
                status="sdk_missing",
                detail="install lark_oapi to enable Feishu send_message",
                payload={"chat_id": chat_id, "msg_type": msg_type},
            )
        try:
            request_types = _resolve_message_request_types(lark)
            request = (
                request_types["request"]
                .builder()
                .receive_id_type("chat_id")
                .request_body(
                    request_types["request_body"]
                    .builder()
                    .receive_id(chat_id)
                    .msg_type(msg_type)
                    .content(content_payload)
                    .build()
                )
                .build()
            )
            client = _build_api_client(lark, self.config)
            response = client.im.v1.message.create(request)
        except Exception as exc:  # noqa: BLE001
            return FeishuClientResult(
                ok=False,
                status="send_error",
                detail=str(exc),
                payload={"chat_id": chat_id, "msg_type": msg_type},
            )
        response_code = getattr(response, "code", -1)
        if response_code not in {0, None}:
            return FeishuClientResult(
                ok=False,
                status="send_failed",
                detail=str(getattr(response, "msg", "feishu send_message failed")),
                payload={"chat_id": chat_id, "msg_type": msg_type, "code": response_code},
            )
        message_id = ""
        response_data = getattr(response, "data", None)
        if response_data is not None:
            message_id = str(getattr(response_data, "message_id", "") or "")
        return FeishuClientResult(
            ok=True,
            status="sent",
            detail="feishu message sent",
            payload={"chat_id": chat_id, "msg_type": msg_type, "message_id": message_id},
        )


class _SdkMissingConnector:
    """在本地未安装官方 SDK 时返回明确说明。"""

    def __init__(self) -> None:
        """保存最近一次固定错误状态。"""
        self._latest_result: FeishuClientResult | None = None

    def connect(
        self,
        event_handler: Callable[[Mapping[str, Any]], object],
    ) -> FeishuClientResult:
        """返回清晰占位结果，提示需要安装官方 SDK。"""
        del event_handler
        self._latest_result = FeishuClientResult(
            ok=False,
            status="sdk_missing",
            detail="install lark_oapi to enable Feishu long connection integration",
        )
        return self._latest_result

    def latest_status(self) -> FeishuClientResult | None:
        """返回最近一次固定状态。"""
        return self._latest_result


class _SdkLongConnectionConnector:
    """使用官方 Python SDK 的长连接客户端接收原始飞书事件。"""

    def __init__(self, config: EnvConfig, lark_module: Any) -> None:
        """绑定运行配置和已导入的官方 SDK 模块。"""
        self.config = config
        self.lark = lark_module
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._latest_result: FeishuClientResult | None = None
        self._listener_id = uuid.uuid4().hex
        self._raw_event_count = 0
        self._last_raw_event_summary: dict[str, Any] = {}
        self._ws_client: Any | None = None

    def connect(
        self,
        event_handler: Callable[[Mapping[str, Any]], object],
    ) -> FeishuClientResult:
        """按官方 sample 启动长连接，并把消息事件桥接为原始字典。"""
        with self._lock:
            if self._thread and self._thread.is_alive():
                self._latest_result = FeishuClientResult(
                    ok=True,
                    status="already_running",
                    detail="feishu long connection listener is already running",
                    payload=self._build_runtime_payload(),
                )
                return self._latest_result
            dispatcher = self._build_dispatcher(event_handler)
            event_bridge = _SdkEventHandlerBridge(
                dispatcher,
                self._record_raw_event_summary,
                event_handler,
            )
            thread = threading.Thread(
                target=self._run_client,
                args=(event_bridge,),
                name="dutyflow-feishu-listener",
                daemon=True,
            )
            thread.start()
            self._thread = thread
        thread.join(timeout=0.5)
        if not thread.is_alive():
            if self._latest_result is not None:
                return self._latest_result
            self._latest_result = FeishuClientResult(
                ok=False,
                status="listener_stopped",
                detail="feishu long connection listener exited immediately",
            )
            return self._latest_result
        self._latest_result = FeishuClientResult(
            ok=True,
            status="listener_started",
            detail="feishu long connection listener started in background",
            payload=self._build_runtime_payload(),
        )
        return self._latest_result

    def _build_dispatcher(
        self,
        event_handler: Callable[[Mapping[str, Any]], object],
    ) -> object:
        """构造 SDK 事件分发器，注册消息事件和审批卡片按钮回调。"""
        builder = self.lark.EventDispatcherHandler.builder(
            self.config.feishu_event_encrypt_key,
            self.config.feishu_event_verify_token,
        )
        builder = builder.register_p2_im_message_receive_v1(self._build_message_handler(event_handler))
        card_register = getattr(builder, "register_p2_card_action_trigger", None)
        if callable(card_register):
            builder = card_register(self._build_card_action_handler(event_handler))
        return builder.build()

    def _build_message_handler(
        self,
        event_handler: Callable[[Mapping[str, Any]], object],
    ) -> Callable[[object], None]:
        """把 SDK typed event 转回原始字典，再交给接入层。"""

        def _handle_message(data: object) -> None:
            raw_event = _marshal_sdk_event(self.lark, data)
            event_handler(raw_event)

        return _handle_message

    def _build_card_action_handler(
        self,
        event_handler: Callable[[Mapping[str, Any]], object],
    ) -> Callable[[object], object]:
        """把 SDK 卡片按钮回调转回原始字典，并返回飞书需要的快速响应。"""

        def _handle_card_action(data: object) -> object:
            raw_event = _marshal_sdk_event(self.lark, data)
            handler_result = event_handler(raw_event)
            return _build_card_action_response(self.lark, handler_result)

        return _handle_card_action

    def _run_client(self, dispatcher: object) -> None:
        """在线程内阻塞运行 SDK WebSocket 客户端。"""
        thread_loop = _prepare_sdk_loop_for_thread(self.lark)
        try:
            client = self.lark.ws.Client(
                self.config.feishu_app_id,
                self.config.feishu_app_secret,
                event_handler=dispatcher,
                log_level=_map_lark_log_level(self.lark, self.config.log_level),
            )
            _install_card_frame_handler_patch(client, self.lark)
            self._ws_client = client
            client.start()
            self._latest_result = FeishuClientResult(
                ok=False,
                status="listener_stopped",
                detail="feishu long connection listener stopped",
                payload=self._build_runtime_payload(),
            )
        except Exception as exc:  # noqa: BLE001
            self._latest_result = FeishuClientResult(
                ok=False,
                status="listener_error",
                detail=str(exc),
                payload=self._build_runtime_payload(),
            )
        finally:
            _close_sdk_loop_for_thread(thread_loop)

    def latest_status(self) -> FeishuClientResult | None:
        """返回监听器最近一次可观测状态。"""
        if self._latest_result is None:
            return None
        return FeishuClientResult(
            ok=self._latest_result.ok,
            status=self._latest_result.status,
            detail=self._latest_result.detail,
            payload={**self._latest_result.payload, **self._build_runtime_payload()},
        )

    def _record_raw_event_summary(self, summary: Mapping[str, Any]) -> None:
        """记录最近一条原始事件摘要，供 doctor 模式观察。"""
        self._raw_event_count += 1
        self._last_raw_event_summary = dict(summary)

    def _build_runtime_payload(self) -> dict[str, Any]:
        """构造当前监听实例的运行时诊断信息。"""
        ws_client = self._ws_client
        conn_url = str(getattr(ws_client, "_conn_url", "") or "")
        service_id = str(getattr(ws_client, "_service_id", "") or "")
        conn_id = str(getattr(ws_client, "_conn_id", "") or "")
        return {
            "listener_id": self._listener_id,
            "pid": os.getpid(),
            "thread_name": self._thread.name if self._thread is not None else "",
            "thread_alive": bool(self._thread and self._thread.is_alive()),
            "card_frame_patch_installed": bool(
                ws_client and getattr(ws_client, "_dutyflow_card_frame_patch", False)
            ),
            "raw_event_count": self._raw_event_count,
            "last_raw_event_summary": dict(self._last_raw_event_summary),
            "ws_connected": bool(ws_client and getattr(ws_client, "_conn", None) is not None),
            "conn_id": conn_id,
            "service_id": service_id,
            "conn_url": conn_url,
        }


def _build_default_connector_for_config(
    config: EnvConfig,
    sdk_module: Any | None = None,
) -> LongConnectionConnector:
    """按当前环境和配置返回默认长连接连接器。"""
    try:
        lark = sdk_module or _import_lark_sdk()
    except ImportError:
        return _SdkMissingConnector()
    return _SdkLongConnectionConnector(config, lark)


def _import_lark_sdk() -> Any:
    """延迟导入官方 SDK，避免在未安装环境中阻塞其它功能。"""
    import lark_oapi as lark  # type: ignore

    return lark


def _build_api_client(lark_module: Any, config: EnvConfig) -> Any:
    """构造官方 SDK 的应用 API 客户端。"""
    return (
        lark_module.Client.builder()
        .app_id(config.feishu_app_id)
        .app_secret(config.feishu_app_secret)
        .log_level(_map_lark_log_level(lark_module, config.log_level))
        .build()
    )


def _resolve_message_request_types(lark_module: Any) -> dict[str, Any]:
    """解析当前 SDK 版本中发送消息请求类的实际导出位置。"""
    top_level_request = getattr(lark_module, "CreateMessageRequest", None)
    top_level_body = getattr(lark_module, "CreateMessageRequestBody", None)
    if top_level_request is not None and top_level_body is not None:
        return {"request": top_level_request, "request_body": top_level_body}
    try:
        im_v1_module = importlib.import_module(f"{lark_module.__name__}.api.im.v1")
    except Exception as exc:  # noqa: BLE001
        raise AttributeError("failed to resolve im.v1 request classes") from exc
    request_class = getattr(im_v1_module, "CreateMessageRequest", None)
    request_body_class = getattr(im_v1_module, "CreateMessageRequestBody", None)
    if request_class is None or request_body_class is None:
        raise AttributeError("CreateMessageRequest types are unavailable in current lark_oapi")
    return {"request": request_class, "request_body": request_body_class}


def _build_card_action_response(lark_module: Any, handler_result: object) -> object:
    """把接入层 ack 转换为 SDK 可返回的卡片回调响应。"""
    payload = _normalize_card_action_ack(handler_result)
    try:
        module = importlib.import_module(
            f"{lark_module.__name__}.event.callback.model.p2_card_action_trigger"
        )
        response_class = getattr(module, "P2CardActionTriggerResponse", None)
    except Exception:  # noqa: BLE001
        response_class = None
    if response_class is None:
        return payload
    return _instantiate_card_action_response(response_class, payload)


def _install_card_frame_handler_patch(client: Any, lark_module: Any) -> None:
    """修正当前 SDK 忽略 WebSocket card 帧的问题，使卡片按钮可进入 dispatcher。"""
    if getattr(client, "_dutyflow_card_frame_patch", False):
        return
    try:
        sdk_ws_client = importlib.import_module(f"{lark_module.__name__}.ws.client")
    except Exception:  # noqa: BLE001
        return

    async def _handle_data_frame(frame: Any) -> None:
        await _handle_data_frame_with_card_support(client, frame, sdk_ws_client)

    setattr(client, "_handle_data_frame", _handle_data_frame)
    setattr(client, "_dutyflow_card_frame_patch", True)


async def _handle_data_frame_with_card_support(client: Any, frame: Any, sdk_ws_client: Any) -> None:
    """按 SDK 原逻辑处理 event 帧，并额外把 card 帧交给卡片回调 dispatcher。"""
    headers = _read_ws_frame_headers(frame, sdk_ws_client)
    payload = _resolve_ws_payload(client, frame.payload, headers)
    if payload is None:
        return
    message_type = sdk_ws_client.MessageType(headers["type"])
    response = sdk_ws_client.Response(code=http.HTTPStatus.OK)
    try:
        started_at = _now_milliseconds()
        result = _dispatch_ws_payload(client, message_type, payload, sdk_ws_client)
        if result is _UNHANDLED_WS_MESSAGE:
            return
        _append_biz_runtime_header(frame, sdk_ws_client, started_at)
        if result is not None:
            response.data = base64.b64encode(
                sdk_ws_client.JSON.marshal(result).encode(sdk_ws_client.UTF_8)
            )
    except Exception as exc:  # noqa: BLE001
        _log_ws_handler_error(sdk_ws_client, headers, message_type, exc)
        response = sdk_ws_client.Response(code=http.HTTPStatus.INTERNAL_SERVER_ERROR)
    frame.payload = sdk_ws_client.JSON.marshal(response).encode(sdk_ws_client.UTF_8)
    await client._write_message(frame.SerializeToString())


def _read_ws_frame_headers(frame: Any, sdk_ws_client: Any) -> dict[str, str]:
    """读取 SDK WebSocket data frame 的必要 header。"""
    headers = frame.headers
    return {
        "message_id": sdk_ws_client._get_by_key(headers, sdk_ws_client.HEADER_MESSAGE_ID),
        "trace_id": sdk_ws_client._get_by_key(headers, sdk_ws_client.HEADER_TRACE_ID),
        "sum": sdk_ws_client._get_by_key(headers, sdk_ws_client.HEADER_SUM),
        "seq": sdk_ws_client._get_by_key(headers, sdk_ws_client.HEADER_SEQ),
        "type": sdk_ws_client._get_by_key(headers, sdk_ws_client.HEADER_TYPE),
    }


def _resolve_ws_payload(client: Any, payload: bytes, headers: Mapping[str, str]) -> bytes | None:
    """处理 SDK WebSocket 分片 payload，未收齐时返回空。"""
    if int(headers["sum"]) <= 1:
        return payload
    return client._combine(
        headers["message_id"],
        int(headers["sum"]),
        int(headers["seq"]),
        payload,
    )


def _dispatch_ws_payload(client: Any, message_type: Any, payload: bytes, sdk_ws_client: Any) -> object:
    """把普通 event 交给 SDK dispatcher，卡片回调直接交给接入层。"""
    if message_type == sdk_ws_client.MessageType.EVENT:
        if _is_card_action_payload(payload):
            return _dispatch_card_payload(client, payload)
        return client._event_handler.do_without_validation(payload)
    if message_type == sdk_ws_client.MessageType.CARD:
        return _dispatch_card_payload(client, payload)
    return _UNHANDLED_WS_MESSAGE


def _dispatch_card_payload(client: Any, payload: bytes) -> object:
    """卡片回调绕过 SDK typed dispatcher，避免 SDK 时间戳反序列化异常。"""
    card_handler = getattr(client._event_handler, "do_card_without_validation", None)
    if callable(card_handler):
        return card_handler(payload)
    return client._event_handler.do_without_validation(payload)


def _is_card_action_payload(payload: bytes) -> bool:
    """判断原始 payload 是否为新旧版飞书卡片按钮回调。"""
    parsed = _parse_raw_payload(payload)
    header = parsed.get("header", {}) if isinstance(parsed.get("header"), Mapping) else {}
    event_type = str(header.get("event_type", "") or parsed.get("type", "") or "")
    return event_type in {"card.action.trigger", "card.action.trigger_v1"}


def _append_biz_runtime_header(frame: Any, sdk_ws_client: Any, started_at: int) -> None:
    """向响应 frame 追加飞书要求的业务处理耗时 header。"""
    header = frame.headers.add()
    header.key = sdk_ws_client.HEADER_BIZ_RT
    header.value = str(_now_milliseconds() - started_at)


def _log_ws_handler_error(
    sdk_ws_client: Any,
    headers: Mapping[str, str],
    message_type: Any,
    exc: Exception,
) -> None:
    """复用 SDK logger 记录 WebSocket 事件或卡片回调处理失败。"""
    sdk_ws_client.logger.error(
        "handle message failed, message_type: %s, message_id: %s, trace_id: %s, err: %s",
        getattr(message_type, "value", str(message_type)),
        headers.get("message_id", ""),
        headers.get("trace_id", ""),
        exc,
    )


def _now_milliseconds() -> int:
    """返回当前毫秒时间戳，用于 WebSocket 响应耗时统计。"""
    return int(round(time.time() * 1000))


def _normalize_card_action_ack(handler_result: object) -> dict[str, Any]:
    """从接入层返回值中提取飞书卡片回调响应字段。"""
    if isinstance(handler_result, Mapping):
        toast = handler_result.get("toast")
        if isinstance(toast, Mapping):
            return {"toast": dict(toast)}
    return {"toast": {"type": "info", "content": "已收到审批操作。"}}


def _instantiate_card_action_response(response_class: Any, payload: Mapping[str, Any]) -> object:
    """兼容不同 SDK 响应类构造方式，失败时回退为字典。"""
    builder = getattr(response_class, "builder", None)
    if callable(builder):
        built = _build_card_response_with_builder(builder(), payload)
        if built is not None:
            return built
    for args in ((dict(payload),), ()):
        try:
            return response_class(*args)
        except Exception:  # noqa: BLE001
            continue
    return dict(payload)


def _build_card_response_with_builder(builder: Any, payload: Mapping[str, Any]) -> object | None:
    """优先使用 SDK builder 写入 toast。"""
    toast = payload.get("toast")
    if isinstance(toast, Mapping):
        toast_method = getattr(builder, "toast", None)
        if callable(toast_method):
            builder = toast_method(dict(toast))
    build_method = getattr(builder, "build", None)
    if callable(build_method):
        return build_method()
    return None


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


class _SdkEventHandlerBridge:
    """在 SDK 分发前打印原始事件摘要，帮助定位消息是否进入本地进程。"""

    def __init__(
        self,
        dispatcher: object,
        summary_recorder: Callable[[Mapping[str, Any]], None],
        raw_event_handler: Callable[[Mapping[str, Any]], object],
    ) -> None:
        """绑定官方 dispatcher，保留原始事件分发能力。"""
        self.dispatcher = dispatcher
        self.summary_recorder = summary_recorder
        self.raw_event_handler = raw_event_handler

    def do_without_validation(self, payload: bytes) -> Any:
        """先打印原始事件帧摘要，再交给 SDK dispatcher。"""
        summary = _build_raw_payload_summary(payload)
        self.summary_recorder(summary)
        _print_raw_event_summary(summary)
        try:
            return self.dispatcher.do_without_validation(payload)
        except Exception as exc:  # noqa: BLE001
            error_payload = {
                "status": "error",
                "action": "dispatch_failed",
                "message": str(exc),
                "raw_summary": summary,
            }
            print(json.dumps(error_payload, ensure_ascii=False, indent=2), flush=True)
            raise

    def do_card_without_validation(self, payload: bytes) -> object:
        """长连接卡片回调直接走原始接入层，兼容新版和旧版卡片结构。"""
        summary = _build_raw_payload_summary(payload)
        self.summary_recorder(summary)
        _print_raw_event_summary(summary)
        raw_event = _parse_raw_payload(payload)
        return self.raw_event_handler(raw_event)


def _build_raw_payload_summary(payload: bytes) -> dict[str, Any]:
    """从原始 WebSocket 事件中抽取最小调试字段，避免直接刷整包。"""
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        return {"payload_preview": "<non-utf8-payload>"}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {"payload_preview": text[:240]}
    if not isinstance(parsed, Mapping):
        return {"payload_preview": text[:240]}
    header = parsed.get("header", {}) if isinstance(parsed.get("header"), Mapping) else {}
    event = parsed.get("event", {}) if isinstance(parsed.get("event"), Mapping) else {}
    message = event.get("message", {}) if isinstance(event.get("message"), Mapping) else {}
    context = event.get("context", {}) if isinstance(event.get("context"), Mapping) else {}
    sender = event.get("sender", {}) if isinstance(event.get("sender"), Mapping) else {}
    sender_id = sender.get("sender_id", {}) if isinstance(sender.get("sender_id"), Mapping) else {}
    operator = event.get("operator", {}) if isinstance(event.get("operator"), Mapping) else {}
    operator_id = operator.get("operator_id", {}) if isinstance(operator.get("operator_id"), Mapping) else {}
    return {
        "event_id": str(header.get("event_id", "") or parsed.get("uuid", "") or ""),
        "event_type": str(header.get("event_type", "") or parsed.get("type", "") or ""),
        "tenant_key": str(header.get("tenant_key", "") or parsed.get("tenant_key", "") or ""),
        "chat_id": str(
            message.get("chat_id", "")
            or context.get("open_chat_id", "")
            or parsed.get("open_chat_id", "")
            or ""
        ),
        "chat_type": str(message.get("chat_type", "") or ""),
        "sender_open_id": str(
            sender_id.get("open_id", "")
            or operator.get("open_id", "")
            or operator_id.get("open_id", "")
            or parsed.get("open_id", "")
            or ""
        ),
    }


def _print_raw_event_summary(summary: Mapping[str, Any]) -> None:
    """在 CLI 中打印飞书原始帧摘要。"""
    print(
        "\n[Feishu] raw event frame received\n"
        + json.dumps(dict(summary), ensure_ascii=False, indent=2),
        flush=True,
    )


def _parse_raw_payload(payload: bytes) -> dict[str, Any]:
    """把 WebSocket 原始 payload 转成字典，失败时保留文本预览。"""
    try:
        parsed = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"payload_preview": repr(payload[:240])}
    if isinstance(parsed, Mapping):
        return dict(parsed)
    return {"payload_preview": str(parsed)[:240]}


def _prepare_sdk_loop_for_thread(lark_module: Any) -> asyncio.AbstractEventLoop:
    """为监听线程创建独立事件循环，并覆盖 SDK 默认全局 loop。"""
    thread_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(thread_loop)
    ws_client_module = getattr(getattr(lark_module, "ws", None), "client", None)
    if ws_client_module is not None:
        setattr(ws_client_module, "loop", thread_loop)
    return thread_loop


def _close_sdk_loop_for_thread(thread_loop: asyncio.AbstractEventLoop) -> None:
    """在监听线程退出时关闭事件循环，避免残留资源。"""
    try:
        if not thread_loop.is_closed():
            thread_loop.close()
    except Exception:  # noqa: BLE001
        return


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
