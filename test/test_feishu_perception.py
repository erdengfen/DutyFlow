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

    def test_feishu_docx_url_creates_feishu_doc_parse_target(self) -> None:
        """消息文本中的飞书 docx URL 应生成 feishu_docx 解析目标并直接提取 doc_token。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            service = PerceptionRecordService(root)
            doc_url = "https://company.feishu.cn/docx/doxcnAbcDefGhi"
            envelope, raw_event_path = _create_source_record(root, f"请看这个文档 {doc_url}")
            record = service.create_record(envelope, raw_event_path)
            self.assertTrue(record.has_attachment)
            self.assertEqual(record.trigger_kind, "p2p_feishu_doc")
            feishu_targets = [t for t in record.parse_targets if t.target_type.startswith("feishu_")]
            self.assertEqual(len(feishu_targets), 1)
            self.assertEqual(feishu_targets[0].target_type, "feishu_docx")
            self.assertEqual(feishu_targets[0].file_key, "doxcnAbcDefGhi")
            self.assertEqual(feishu_targets[0].required_tool, "feishu_read_doc")
            self.assertEqual(feishu_targets[0].url, doc_url)

    def test_feishu_sheet_url_creates_feishu_sheet_parse_target(self) -> None:
        """消息文本中的飞书 sheet URL 应生成 feishu_sheet 类型目标，工具指向 feishu_get_file_meta。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            service = PerceptionRecordService(root)
            sheet_url = "https://company.feishu.cn/sheets/shtcnXyzAbc123"
            envelope, raw_event_path = _create_source_record(root, f"表格链接 {sheet_url}")
            record = service.create_record(envelope, raw_event_path)
            feishu_targets = [t for t in record.parse_targets if t.target_type.startswith("feishu_")]
            self.assertEqual(len(feishu_targets), 1)
            self.assertEqual(feishu_targets[0].target_type, "feishu_sheet")
            self.assertEqual(feishu_targets[0].file_key, "shtcnXyzAbc123")
            self.assertEqual(feishu_targets[0].required_tool, "feishu_get_file_meta")

    def test_non_feishu_url_creates_generic_link_target(self) -> None:
        """非飞书 URL 应继续生成 link 类型解析目标，required_tool 为 parse_web_link。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            service = PerceptionRecordService(root)
            envelope, raw_event_path = _create_source_record(
                root, "参考资料 https://www.google.com/search?q=test"
            )
            record = service.create_record(envelope, raw_event_path)
            link_targets = [t for t in record.parse_targets if t.target_type == "link"]
            self.assertEqual(len(link_targets), 1)
            self.assertEqual(link_targets[0].required_tool, "parse_web_link")
            feishu_targets = [t for t in record.parse_targets if t.target_type.startswith("feishu_")]
            self.assertEqual(len(feishu_targets), 0)

    def test_feishu_url_token_in_loop_input_parse_targets(self) -> None:
        """loop_input 的 parse_targets 应包含已提取的 file_key，供 Agent 直接调用工具。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            service = PerceptionRecordService(root)
            doc_url = "https://abc.feishu.cn/docx/FvFadXXXXXXXXXXX"
            envelope, raw_event_path = _create_source_record(root, doc_url)
            record = service.create_record(envelope, raw_event_path)
            loop_input = service.build_loop_input(record_id=record.record_id)
            self.assertIsNotNone(loop_input)
            targets = loop_input["parse_targets"]
            self.assertTrue(
                any(t["file_key"] == "FvFadXXXXXXXXXXX" for t in targets),
                "loop_input parse_targets 中应有提取到的 doc_token",
            )

    def test_feishu_drive_file_url_creates_feishu_file_target(self) -> None:
        """飞书 drive/file URL 应生成 feishu_file 类型目标，工具指向 feishu_get_file_meta。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            service = PerceptionRecordService(root)
            drive_url = "https://company.feishu.cn/drive/file/boxcnAbcXyz123456"
            envelope, raw_event_path = _create_source_record(root, drive_url)
            record = service.create_record(envelope, raw_event_path)
            feishu_targets = [t for t in record.parse_targets if t.target_type.startswith("feishu_")]
            self.assertEqual(len(feishu_targets), 1)
            self.assertEqual(feishu_targets[0].target_type, "feishu_file")
            self.assertEqual(feishu_targets[0].file_key, "boxcnAbcXyz123456")
            self.assertEqual(feishu_targets[0].required_tool, "feishu_get_file_meta")

    def test_multiline_text_roundtrip_preserves_full_raw_text(self) -> None:
        """多行飞书文本写入感知记录后，回读给 loop 时不应只剩第一行。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            service = PerceptionRecordService(root)
            text = "场景：\n核心项目群有人发消息\n请判断这是不是高优先级"
            envelope, raw_event_path = _create_source_record(root, text)
            record = service.create_record(envelope, raw_event_path)
            loop_input = service.build_loop_input(record_id=record.record_id)
            self.assertIsNotNone(loop_input)
            self.assertEqual(loop_input["raw_text"], text)
            self.assertIn("请判断这是不是高优先级", loop_input["content_preview"])


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
    """运行本文件全部单元测试，覆盖原有感知记录和飞书 URL 解析两组用例。"""
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestFeishuPerception)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    _self_test()
