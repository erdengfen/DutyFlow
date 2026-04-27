# 本文件验证 Step 5 飞书接入层的最小规范化、去重和事件落盘链路。

from __future__ import annotations

from pathlib import Path
import io
import sys
import tempfile
import unittest
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dutyflow.config.env import load_env_config  # noqa: E402
from dutyflow.feishu.client import FeishuClientResult  # noqa: E402
from dutyflow.feishu.events import FeishuEventAdapter  # noqa: E402
from dutyflow.feishu.runtime import FeishuIngressService  # noqa: E402


class FakeConnector:
    """模拟可注入的长连接连接器，避免测试依赖真实 SDK。"""

    def __init__(self, raw_event: dict[str, object]) -> None:
        """保存准备推送给接入层的原始事件。"""
        self.raw_event = raw_event
        self.connected = False

    def connect(self, event_handler) -> FeishuClientResult:
        """模拟连接建立后立即投递一条原始事件。"""
        self.connected = True
        event_handler(self.raw_event)
        return FeishuClientResult(ok=True, status="connected", detail="fake connector delivered one event")


class TestFeishuEvents(unittest.TestCase):
    """验证 Step 5 飞书接入层当前已落地的最小能力。"""

    def test_adapter_normalizes_fixture_event(self) -> None:
        """fixture 事件应被规范化为最小统一视图。"""
        adapter = FeishuEventAdapter()
        raw_event = adapter.create_local_fixture_event("hello", chat_type="group", mentions_bot=True)
        envelope = adapter.build_event_envelope(raw_event)
        self.assertEqual(envelope.event_type, "im.message.receive_v1")
        self.assertTrue(envelope.is_group_at_bot())
        self.assertEqual(envelope.content_preview, "hello")
        self.assertEqual(envelope.message_text, "hello")

    def test_ingress_service_persists_event_and_deduplicates_message_id(self) -> None:
        """接入层应写入事件 Markdown，并对重复 message_id 去重。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = _write_env(root, event_mode="fixture")
            adapter = FeishuEventAdapter()
            service = FeishuIngressService(root, config, adapter=adapter)
            first = adapter.create_local_fixture_event("first", event_id="evt_a", message_id="msg_same")
            second = adapter.create_local_fixture_event("second", event_id="evt_b", message_id="msg_same")
            first_result = service.handle_raw_event(first)
            second_result = service.handle_raw_event(second)
            self.assertEqual(first_result.action, "accepted")
            self.assertEqual(second_result.action, "duplicate_message")
            event_files = list((root / "data" / "events").glob("evt_*.md"))
            self.assertEqual(len(event_files), 1)
            saved = event_files[0].read_text(encoding="utf-8")
            self.assertIn("schema: dutyflow.event_record.v1", saved)
            self.assertIn("## Raw Payload", saved)
            self.assertIn("installation_scope_id: cli_demo_app:tenant_demo", saved)

    def test_long_connection_uses_injected_connector(self) -> None:
        """长连接骨架应能通过注入连接器把事件送入接入层。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = _write_env(root, event_mode="long_connection")
            adapter = FeishuEventAdapter()
            raw_event = adapter.create_local_fixture_event("hello", event_id="evt_ws", message_id="msg_ws")
            connector = FakeConnector(raw_event)
            service = FeishuIngressService(
                root,
                config,
                adapter=adapter,
                client=_client_with_connector(config, connector),
            )
            with patch("sys.stdout", new_callable=io.StringIO) as stdout:
                result = service.start_long_connection()
            self.assertTrue(result.ok)
            self.assertTrue(connector.connected)
            self.assertEqual(len(list((root / "data" / "events").glob("evt_*.md"))), 1)
            self.assertIn("[Feishu] event received", stdout.getvalue())

    def test_group_message_without_bot_mention_is_ignored(self) -> None:
        """群聊非 @Bot 消息不应进入 Step 5 初版主链。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = _write_env(root, event_mode="fixture")
            adapter = FeishuEventAdapter()
            service = FeishuIngressService(root, config, adapter=adapter)
            raw_event = adapter.create_local_fixture_event("group", chat_type="group", mentions_bot=False)
            result = service.handle_raw_event(raw_event)
            self.assertEqual(result.action, "ignored")

    def test_bootstrap_payload_exposes_owner_candidates_after_p2p_event(self) -> None:
        """owner 相关字段缺失时，应在 p2p 事件后返回可回填候选值。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = _write_env(root, event_mode="long_connection", bootstrap_owner=True)
            adapter = FeishuEventAdapter()
            service = FeishuIngressService(root, config, adapter=adapter)
            raw_event = adapter.create_local_fixture_event(
                "bootstrap",
                tenant_key="tenant_from_event",
                sender_open_id="ou_from_event",
                chat_id="oc_from_event",
            )
            result = service.handle_raw_event(raw_event)
            self.assertEqual(result.action, "accepted")
            self.assertEqual(result.payload["tenant_key_candidate"], "tenant_from_event")
            self.assertEqual(result.payload["owner_open_id_candidate"], "ou_from_event")
            self.assertEqual(result.payload["owner_report_chat_id_candidate"], "oc_from_event")
            self.assertIn("DUTYFLOW_FEISHU_OWNER_OPEN_ID", result.payload["missing_env_keys"])

    def test_bind_command_updates_env_and_sends_confirmation(self) -> None:
        """收到 /bind 时应保存关键配置，并向当前会话发送绑定成功消息。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = _write_env(root, event_mode="long_connection", bootstrap_owner=True)
            adapter = FeishuEventAdapter()
            client = FakeBindClient()
            service = FeishuIngressService(root, config, adapter=adapter, client=client)
            raw_event = adapter.create_local_fixture_event(
                "/bind",
                tenant_key="tenant_bind",
                sender_open_id="ou_bind",
                chat_id="oc_bind",
            )
            result = service.handle_raw_event(raw_event)
            self.assertEqual(result.action, "bind_saved")
            self.assertIn("DUTYFLOW_FEISHU_TENANT_KEY", result.payload["saved_env_keys"])
            self.assertTrue(result.payload["send_message_ok"])
            self.assertEqual(client.sent_chat_id, "oc_bind")
            env_text = (root / ".env").read_text(encoding="utf-8")
            self.assertIn("DUTYFLOW_FEISHU_TENANT_KEY=tenant_bind", env_text)
            self.assertIn("DUTYFLOW_FEISHU_OWNER_OPEN_ID=ou_bind", env_text)
            self.assertIn("DUTYFLOW_FEISHU_OWNER_REPORT_CHAT_ID=oc_bind", env_text)


def _write_env(root: Path, *, event_mode: str, bootstrap_owner: bool = False) -> object:
    """写入最小可用 .env，并返回解析后的配置对象。"""
    tenant_key = "replace-with-feishu-tenant-key" if bootstrap_owner else "tenant_demo"
    owner_open_id = "replace-with-owner-open-id" if bootstrap_owner else "ou_owner"
    owner_report_chat_id = (
        "replace-with-owner-report-chat-id" if bootstrap_owner else "oc_owner"
    )
    content = (
        "DUTYFLOW_MODEL_API_KEY=demo-key\n"
        "DUTYFLOW_MODEL_BASE_URL=https://example.invalid/model\n"
        "DUTYFLOW_MODEL_NAME=demo-model\n"
        "DUTYFLOW_FEISHU_APP_ID=app_demo\n"
        "DUTYFLOW_FEISHU_APP_SECRET=secret_demo\n"
        "DUTYFLOW_FEISHU_EVENT_VERIFY_TOKEN=verify_demo\n"
        "DUTYFLOW_FEISHU_EVENT_ENCRYPT_KEY=encrypt_demo\n"
        "DUTYFLOW_FEISHU_EVENT_MODE="
        f"{event_mode}\n"
        f"DUTYFLOW_FEISHU_TENANT_KEY={tenant_key}\n"
        f"DUTYFLOW_FEISHU_OWNER_OPEN_ID={owner_open_id}\n"
        f"DUTYFLOW_FEISHU_OWNER_REPORT_CHAT_ID={owner_report_chat_id}\n"
        "DUTYFLOW_DATA_DIR=data\n"
        "DUTYFLOW_LOG_DIR=data/logs\n"
    )
    (root / ".env").write_text(content, encoding="utf-8")
    return load_env_config(root)


def _client_with_connector(config, connector):
    """构造注入测试连接器的 FeishuClient。"""
    from dutyflow.feishu.client import FeishuClient  # noqa: WPS433

    return FeishuClient(config, connector=connector)


class FakeBindClient:
    """模拟绑定流程里最小可用的发送消息客户端。"""

    def __init__(self) -> None:
        """记录最近一次发送目标。"""
        self.sent_chat_id = ""
        self.sent_content = ""

    def send_message(self, chat_id: str, content: str, *, msg_type: str = "text"):
        """模拟发送绑定成功消息。"""
        self.sent_chat_id = chat_id
        self.sent_content = content
        return FeishuClientResult(
            ok=True,
            status="sent",
            detail="fake bind confirmation sent",
            payload={"chat_id": chat_id, "msg_type": msg_type, "message_id": "om_bind_reply"},
        )


def _self_test() -> None:
    """运行本文件单元测试。"""
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestFeishuEvents)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    _self_test()
