# 本文件负责飞书原始事件的最小规范化，不承担业务解析和权重判断。

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping


@dataclass(frozen=True)
class FeishuEventEnvelope:
    """表示 Step 5 接入层可消费的最小事件视图。"""

    event_id: str
    event_type: str
    tenant_key: str
    app_id: str
    message_id: str
    chat_id: str
    chat_type: str
    message_type: str
    sender_open_id: str
    sender_user_id: str
    sender_union_id: str
    message_text: str
    content_preview: str
    mentions_bot: bool
    mentioned_open_ids: tuple[str, ...]
    received_at: str
    raw_event: dict[str, Any]

    def is_p2p_message(self) -> bool:
        """判断是否为用户与 Bot 的私聊消息。"""
        return self.chat_type == "p2p"

    def is_group_at_bot(self) -> bool:
        """判断是否为群聊中显式 @Bot 的消息。"""
        return self.chat_type in {"group", "topic_group"} and self.mentions_bot

    def is_bind_request(self) -> bool:
        """判断当前消息是否为显式的绑定指令。"""
        return self.is_p2p_message() and self.message_text.strip() == "/bind"


class FeishuEventAdapter:
    """把飞书原始事件转换为接入层统一视图。"""

    def build_event_envelope(
        self,
        raw_event: Mapping[str, Any],
        received_at: str | None = None,
    ) -> FeishuEventEnvelope:
        """构造统一事件包裹对象，供接入层后续去重和落盘。"""
        return self.normalize_raw_event(raw_event, received_at=received_at)

    def normalize_raw_event(
        self,
        raw_event: Mapping[str, Any],
        received_at: str | None = None,
    ) -> FeishuEventEnvelope:
        """抽取 Step 5 所需最小路由字段，不做业务语义判断。"""
        header = _mapping(raw_event.get("header"))
        event = _mapping(raw_event.get("event"))
        message = _mapping(event.get("message"))
        sender = _mapping(event.get("sender"))
        sender_id = _mapping(sender.get("sender_id"))
        mentions = _normalize_mentions(message.get("mentions"))
        return FeishuEventEnvelope(
            event_id=_pick_first_non_empty(header.get("event_id"), raw_event.get("event_id"), raw_event.get("uuid")),
            event_type=_pick_first_non_empty(header.get("event_type"), raw_event.get("type")),
            tenant_key=_pick_first_non_empty(header.get("tenant_key"), raw_event.get("tenant_key")),
            app_id=_pick_first_non_empty(header.get("app_id"), raw_event.get("app_id")),
            message_id=_pick_first_non_empty(message.get("message_id"), raw_event.get("open_message_id")),
            chat_id=_pick_first_non_empty(message.get("chat_id"), raw_event.get("open_chat_id")),
            chat_type=_pick_first_non_empty(message.get("chat_type"), event.get("chat_type")),
            message_type=_pick_first_non_empty(message.get("message_type"), event.get("message_type")),
            sender_open_id=_pick_first_non_empty(sender_id.get("open_id"), raw_event.get("open_id")),
            sender_user_id=_pick_first_non_empty(sender_id.get("user_id"), raw_event.get("user_id")),
            sender_union_id=_as_text(sender_id.get("union_id")),
            message_text=_extract_message_text(message.get("content")),
            content_preview=_extract_content_preview(message.get("content")),
            mentions_bot=_mentions_bot(mentions),
            mentioned_open_ids=_extract_mentioned_open_ids(mentions),
            received_at=received_at or _received_at_from_header(header),
            raw_event=dict(raw_event),
        )

    def create_local_fixture_event(
        self,
        text: str,
        *,
        event_id: str = "evt_fixture_001",
        message_id: str = "om_fixture_001",
        tenant_key: str = "tenant_demo",
        app_id: str = "cli_demo_app",
        chat_id: str = "oc_fixture_chat",
        chat_type: str = "p2p",
        sender_open_id: str = "ou_fixture_sender",
        sender_user_id: str = "user_fixture_sender",
        sender_union_id: str = "union_fixture_sender",
        message_type: str = "text",
        content_payload: Mapping[str, Any] | None = None,
        mentions_bot: bool = False,
    ) -> dict[str, Any]:
        """构造无真实飞书环境下可复用的本地 fixture 事件。"""
        mention_blocks = []
        if mentions_bot:
            mention_blocks.append(
                {
                    "name": "DutyFlow Bot",
                    "id": {
                        "open_id": "ou_bot_fixture",
                        "user_id": "user_bot_fixture",
                        "union_id": "union_bot_fixture",
                    },
                }
            )
        payload = _build_fixture_content_payload(text, message_type, content_payload)
        return {
            "schema": "2.0",
            "header": {
                "event_id": event_id,
                "event_type": "im.message.receive_v1",
                "tenant_key": tenant_key,
                "app_id": app_id,
                "create_time": str(int(datetime.now(timezone.utc).timestamp() * 1000)),
            },
            "event": {
                "sender": {
                    "sender_id": {
                        "open_id": sender_open_id,
                        "user_id": sender_user_id,
                        "union_id": sender_union_id,
                    }
                },
                "message": {
                    "message_id": message_id,
                    "chat_id": chat_id,
                    "chat_type": chat_type,
                    "content": json.dumps(payload, ensure_ascii=False),
                    "message_type": message_type,
                    "mentions": mention_blocks,
                },
                "chat_type": chat_type,
            },
        }


def _mapping(value: object) -> dict[str, Any]:
    """把不确定对象安全转换为字典。"""
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _pick_first_non_empty(*values: object) -> str:
    """返回第一个非空字符串形式的值。"""
    for value in values:
        text = _as_text(value)
        if text:
            return text
    return ""


def _as_text(value: object) -> str:
    """把简单标量安全转换为字符串。"""
    if value is None:
        return ""
    return str(value).strip()


def _normalize_mentions(value: object) -> list[dict[str, Any]]:
    """把消息 mention 列表转换为稳定字典数组。"""
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _mentions_bot(mentions: list[dict[str, Any]]) -> bool:
    """根据 mentions 粗略判断当前消息是否显式 @Bot。"""
    return any(_mapping(item.get("id")) for item in mentions)


def _extract_mentioned_open_ids(mentions: list[dict[str, Any]]) -> tuple[str, ...]:
    """提取消息 mentions 中可稳定复用的 open_id 列表。"""
    values: list[str] = []
    for item in mentions:
        mention_id = _mapping(item.get("id"))
        open_id = _as_text(mention_id.get("open_id"))
        if open_id:
            values.append(open_id)
    return tuple(values)


def _build_fixture_content_payload(
    text: str,
    message_type: str,
    content_payload: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """为本地 fixture 生成与消息类型匹配的最小内容结构。"""
    if content_payload is not None:
        return dict(content_payload)
    if message_type == "file":
        return {
            "file_key": "file_fixture_key",
            "file_name": text or "fixture.txt",
        }
    if message_type == "image":
        return {
            "image_key": "img_fixture_key",
            "image_name": text or "fixture.png",
        }
    return {"text": text}


def _extract_content_preview(raw_content: object) -> str:
    """从消息内容中提取稳定预览文本。"""
    if isinstance(raw_content, Mapping):
        return _preview_from_mapping(dict(raw_content))
    content_text = _as_text(raw_content)
    if not content_text:
        return ""
    try:
        parsed = json.loads(content_text)
    except json.JSONDecodeError:
        return _truncate(content_text)
    if isinstance(parsed, Mapping):
        return _preview_from_mapping(dict(parsed))
    return _truncate(content_text)


def _extract_message_text(raw_content: object) -> str:
    """从飞书消息内容中提取完整文本，用于识别绑定指令。"""
    if isinstance(raw_content, Mapping):
        return _as_text(dict(raw_content).get("text"))
    content_text = _as_text(raw_content)
    if not content_text:
        return ""
    try:
        parsed = json.loads(content_text)
    except json.JSONDecodeError:
        return content_text
    if isinstance(parsed, Mapping):
        return _as_text(dict(parsed).get("text"))
    return content_text


def _preview_from_mapping(content: dict[str, Any]) -> str:
    """优先提取文本字段，缺失时再回退为 JSON 片段。"""
    for key in ("text", "title"):
        text = _as_text(content.get(key))
        if text:
            return _truncate(text)
    return _truncate(json.dumps(content, ensure_ascii=False, sort_keys=True))


def _truncate(text: str, limit: int = 120) -> str:
    """限制预览文本长度，避免接入层保存过长内容。"""
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _received_at_from_header(header: dict[str, Any]) -> str:
    """优先使用飞书 header 时间，缺失时回退到当前时间。"""
    created = _as_text(header.get("create_time"))
    if created.isdigit():
        return _timestamp_to_iso(created)
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _timestamp_to_iso(value: str) -> str:
    """把飞书秒、毫秒、微秒或纳秒时间戳转换为 ISO-8601。"""
    seconds = _timestamp_seconds(value)
    try:
        return datetime.fromtimestamp(seconds, tz=timezone.utc).astimezone().isoformat(
            timespec="seconds"
        )
    except (OverflowError, OSError, ValueError):
        return datetime.now().astimezone().isoformat(timespec="seconds")


def _timestamp_seconds(value: str) -> int:
    """飞书部分回调会传超长时间戳，统一截取前 10 位秒级时间。"""
    if len(value) <= 10:
        return int(value)
    return int(value[:10])


def _self_test() -> None:
    """验证 fixture 事件可被转换为统一包裹对象。"""
    adapter = FeishuEventAdapter()
    raw_event = adapter.create_local_fixture_event("hello", mentions_bot=True, chat_type="group")
    envelope = adapter.build_event_envelope(raw_event)
    assert envelope.event_type == "im.message.receive_v1"
    assert envelope.is_group_at_bot()
    assert envelope.message_type == "text"


if __name__ == "__main__":
    _self_test()
    print("dutyflow feishu events self-test passed")
