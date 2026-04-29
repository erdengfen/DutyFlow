# 本文件验证统一反馈接口的最小发送与格式化行为。

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dutyflow.config.env import load_env_config  # noqa: E402
from dutyflow.feedback.gateway import FeedbackGateway  # noqa: E402
from dutyflow.feishu.client import FeishuClientResult  # noqa: E402


class TestFeedbackGateway(unittest.TestCase):
    """验证面向正式 loop 的统一回馈出口。"""

    def test_send_text_uses_underlying_client(self) -> None:
        """发送文本时应复用底层飞书客户端并返回统一结果。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = _write_env(root, owner_report_chat_id="oc_owner")
            client = FakeFeedbackClient()
            gateway = FeedbackGateway(config, client=client)
            result = gateway.send_text("oc_target", "hello")
            self.assertTrue(result.ok)
            self.assertEqual(result.status, "sent")
            self.assertEqual(client.sent_chat_id, "oc_target")
            self.assertEqual(client.sent_text, "hello")
            self.assertEqual(result.payload["message_id"], "om_feedback_reply")

    def test_send_owner_text_uses_owner_chat_id(self) -> None:
        """向 owner 发送文本时应使用配置中的默认会话。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = _write_env(root, owner_report_chat_id="oc_owner")
            client = FakeFeedbackClient()
            gateway = FeedbackGateway(config, client=client)
            result = gateway.send_owner_text("owner hello")
            self.assertTrue(result.ok)
            self.assertEqual(client.sent_chat_id, "oc_owner")
            self.assertEqual(client.sent_text, "owner hello")

    def test_send_owner_text_reports_missing_chat(self) -> None:
        """未配置 owner 会话时应返回明确错误，而不是尝试发送。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = _write_env(root, owner_report_chat_id="")
            client = FakeFeedbackClient()
            gateway = FeedbackGateway(config, client=client)
            result = gateway.send_owner_text("owner hello")
            self.assertFalse(result.ok)
            self.assertEqual(result.status, "missing_owner_chat")
            self.assertEqual(client.sent_chat_id, "")

    def test_send_status_update_renders_stable_text(self) -> None:
        """状态回馈应收口到稳定文本格式。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = _write_env(root, owner_report_chat_id="oc_owner")
            client = FakeFeedbackClient()
            gateway = FeedbackGateway(config, client=client)
            result = gateway.send_status_update("oc_target", "处理中", "已进入后台任务")
            self.assertTrue(result.ok)
            self.assertEqual(client.sent_chat_id, "oc_target")
            self.assertEqual(client.sent_text, "【处理中】\n已进入后台任务")

    def test_send_owner_approval_card_uses_fixed_card_shape(self) -> None:
        """审批卡片应通过 owner 会话发送，并携带固定按钮恢复字段。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = _write_env(root, owner_report_chat_id="oc_owner")
            client = FakeFeedbackClient()
            gateway = FeedbackGateway(config, client=client)
            result = gateway.send_owner_approval_card(
                {
                    "approval_id": "approval_001",
                    "task_id": "task_001",
                    "resume_token": "resume_001",
                    "risk_level": "high",
                    "request": "需要写入联系人知识库。",
                }
            )
        self.assertTrue(result.ok)
        self.assertEqual(client.sent_chat_id, "oc_owner")
        self.assertEqual(client.sent_msg_type, "interactive")
        button = client.sent_card["elements"][1]["actions"][0]
        self.assertEqual(button["behaviors"][0]["type"], "callback")
        self.assertEqual(button["behaviors"][0]["value"]["decision_result"], "approved")


class FakeFeedbackClient:
    """模拟统一反馈接口依赖的最小飞书发送客户端。"""

    def __init__(self) -> None:
        """记录最近一次发送行为。"""
        self.sent_chat_id = ""
        self.sent_text = ""
        self.sent_card = {}
        self.sent_msg_type = ""

    def send_message(self, chat_id: str, content: str, *, msg_type: str = "text"):
        """返回稳定的伪发送结果，避免测试依赖真实网络。"""
        self.sent_chat_id = chat_id
        self.sent_text = content
        self.sent_msg_type = msg_type
        return FeishuClientResult(
            ok=True,
            status="sent",
            detail="fake feedback message sent",
            payload={"chat_id": chat_id, "msg_type": msg_type, "message_id": "om_feedback_reply"},
        )

    def send_interactive_card(self, chat_id: str, card_content: dict):
        """返回稳定的伪卡片发送结果。"""
        self.sent_chat_id = chat_id
        self.sent_card = dict(card_content)
        self.sent_msg_type = "interactive"
        return FeishuClientResult(
            ok=True,
            status="sent",
            detail="fake feedback card sent",
            payload={"chat_id": chat_id, "msg_type": "interactive", "message_id": "om_card_reply"},
        )


def _write_env(root: Path, *, owner_report_chat_id: str) -> object:
    """写入反馈网关测试所需的最小配置。"""
    content = (
        "DUTYFLOW_MODEL_API_KEY=demo-key\n"
        "DUTYFLOW_MODEL_BASE_URL=https://example.invalid/model\n"
        "DUTYFLOW_MODEL_NAME=demo-model\n"
        "DUTYFLOW_FEISHU_APP_ID=app_demo\n"
        "DUTYFLOW_FEISHU_APP_SECRET=secret_demo\n"
        "DUTYFLOW_FEISHU_EVENT_VERIFY_TOKEN=verify_demo\n"
        "DUTYFLOW_FEISHU_EVENT_ENCRYPT_KEY=encrypt_demo\n"
        "DUTYFLOW_FEISHU_EVENT_MODE=fixture\n"
        "DUTYFLOW_FEISHU_TENANT_KEY=tenant_demo\n"
        "DUTYFLOW_FEISHU_OWNER_OPEN_ID=ou_owner\n"
        f"DUTYFLOW_FEISHU_OWNER_REPORT_CHAT_ID={owner_report_chat_id}\n"
        "DUTYFLOW_DATA_DIR=data\n"
        "DUTYFLOW_LOG_DIR=data/logs\n"
    )
    (root / ".env").write_text(content, encoding="utf-8")
    return load_env_config(root)


def _self_test() -> None:
    """运行本文件单元测试。"""
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestFeedbackGateway)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    _self_test()
