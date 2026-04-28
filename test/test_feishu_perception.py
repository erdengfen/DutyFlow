# 本文件验证感知记录层的标准化提取、Markdown 落盘和 loop 输入读取接口。

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dutyflow.feishu.events import FeishuEventAdapter  # noqa: E402
from dutyflow.perception.store import PerceptionRecordService  # noqa: E402


class TestFeishuPerception(unittest.TestCase):
    """验证感知记录层只做确定性提取，并向后续 loop 暴露标准输入。"""

    def test_text_message_creates_perceived_record_and_loop_input(self) -> None:
        """文本消息应生成 p2p_text 感知记录，并可反向构造 loop 输入。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            service = PerceptionRecordService(root)
            envelope, raw_event_path = _create_source_record(root, "hello")
            record = service.create_record(envelope, raw_event_path)
            loop_input = service.build_loop_input(message_id=envelope.message_id)
            self.assertEqual(record.trigger_kind, "p2p_text")
            self.assertEqual(record.contact_lookup_hint, "feishu_open_id=ou_fixture_sender")
            self.assertIsNotNone(loop_input)
            self.assertEqual(loop_input["trigger_kind"], "p2p_text")
            self.assertEqual(loop_input["source_event_id"], "evt_fixture_source")

    def test_file_message_builds_parse_target_and_attachment_kind(self) -> None:
        """文件消息应抽取 parse target，并标记 file 附件类型。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            service = PerceptionRecordService(root)
            envelope, raw_event_path = _create_source_record(
                root,
                "report.pdf",
                message_type="file",
                content_payload={
                    "file_key": "file_demo_key",
                    "file_name": "report.pdf",
                },
            )
            record = service.create_record(envelope, raw_event_path)
            self.assertTrue(record.has_attachment)
            self.assertEqual(record.trigger_kind, "p2p_file")
            self.assertEqual(record.attachment_kinds, ("file",))
            self.assertEqual(record.parse_targets[0].file_key, "file_demo_key")
            self.assertEqual(record.parse_targets[0].required_tool, "fetch_feishu_message_resource")

    def test_group_at_bot_message_maps_to_group_trigger(self) -> None:
        """群聊 @Bot 消息应映射为 group_at_bot 前缀的触发类型。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            service = PerceptionRecordService(root)
            envelope, raw_event_path = _create_source_record(
                root,
                "@bot test",
                chat_type="group",
                mentions_bot=True,
            )
            record = service.create_record(envelope, raw_event_path)
            self.assertEqual(record.trigger_kind, "group_at_bot_text")
            self.assertTrue(record.mentions_bot)
            self.assertEqual(record.entities[-1].kind, "mention")


def _create_source_record(
    root: Path,
    text: str,
    *,
    chat_type: str = "p2p",
    message_type: str = "text",
    content_payload: dict[str, str] | None = None,
    mentions_bot: bool = False,
):
    """生成感知层测试所需的最小原始事件和上游事件记录路径。"""
    adapter = FeishuEventAdapter()
    raw_event = adapter.create_local_fixture_event(
        text,
        chat_type=chat_type,
        message_type=message_type,
        content_payload=content_payload,
        mentions_bot=mentions_bot,
    )
    envelope = adapter.build_event_envelope(raw_event, received_at="2026-04-28T12:00:00+08:00")
    raw_event_path = root / "data" / "events" / "evt_fixture_source.md"
    raw_event_path.parent.mkdir(parents=True, exist_ok=True)
    raw_event_path.write_text("fixture source", encoding="utf-8")
    return envelope, raw_event_path


def _self_test() -> None:
    """运行本文件单元测试。"""
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestFeishuPerception)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    _self_test()
