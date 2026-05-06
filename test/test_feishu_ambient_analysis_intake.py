# 本文件验证 AmbientAnalysisIntakeService 的扫描、水位、打包和入队行为。

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dutyflow.feishu.ambient_analysis_intake import (  # noqa: E402
    DEFAULT_SOURCE_TYPES,
    MAX_PACKETS_PER_TICK,
    AmbientAnalysisIntakeResult,
    AmbientAnalysisIntakeService,
)
from dutyflow.feishu.ambient_context import (  # noqa: E402
    AmbientContextRecord,
    AmbientContextStore,
)


class _FakeRuntime:
    """测试替身：记录被入队的 loop_input。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def enqueue_perception(self, loop_input: dict[str, Any]) -> None:
        self.calls.append(loop_input)


def _write_record(
    store: AmbientContextStore,
    record_id: str,
    source_type: str = "direct_message",
    created_at: str = "2026-05-07T10:00:00+00:00",
) -> None:
    """向 store 写入一条测试 ambient context 记录。"""
    record = AmbientContextRecord(
        record_id=record_id,
        source_type=source_type,
        collector_name="direct_message_collector",
        source_id="om_test",
        sync_scope_id="oc_test",
        created_at=created_at,
        fetched_at=created_at,
        text="hello",
        text_preview="hello",
    )
    store.write(record)


class TestAmbientAnalysisIntakeService(unittest.TestCase):
    """验证 AmbientAnalysisIntakeService 的核心行为。"""

    def test_empty_store_returns_zero_packets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = _FakeRuntime()
            service = AmbientAnalysisIntakeService(root, runtime)

            result = service.enqueue_new_records()

        self.assertTrue(result.ok)
        self.assertEqual(result.packets_enqueued, 0)
        self.assertEqual(len(runtime.calls), 0)

    def test_new_records_are_enqueued(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = AmbientContextStore(root)
            _write_record(store, "dm_rec1", "direct_message")
            runtime = _FakeRuntime()
            service = AmbientAnalysisIntakeService(root, runtime)

            result = service.enqueue_new_records(["direct_message"])

        self.assertTrue(result.ok)
        self.assertEqual(result.packets_enqueued, 1)
        self.assertIn("dm_rec1", result.record_ids_sent)
        self.assertEqual(len(runtime.calls), 1)

    def test_enqueued_packet_has_correct_trigger_kind(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = AmbientContextStore(root)
            _write_record(store, "dm_rec1", "direct_message")
            runtime = _FakeRuntime()
            service = AmbientAnalysisIntakeService(root, runtime)

            service.enqueue_new_records(["direct_message"])

        call = runtime.calls[0]
        self.assertEqual(call["trigger_kind"], "ambient_context_batch")
        self.assertTrue(call["perception_id"].startswith("amb_batch_"))
        self.assertIn("packet", call)

    def test_watermark_prevents_re_enqueue(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = AmbientContextStore(root)
            _write_record(store, "dm_rec1", "direct_message", "2026-05-07T10:00:00+00:00")
            runtime = _FakeRuntime()
            service = AmbientAnalysisIntakeService(root, runtime)

            service.enqueue_new_records(["direct_message"])
            result2 = service.enqueue_new_records(["direct_message"])

        self.assertEqual(result2.packets_enqueued, 0)
        self.assertEqual(len(runtime.calls), 1)

    def test_new_record_after_watermark_is_enqueued(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = AmbientContextStore(root)
            _write_record(store, "dm_rec1", "direct_message", "2026-05-07T10:00:00+00:00")
            runtime = _FakeRuntime()
            service = AmbientAnalysisIntakeService(root, runtime)
            service.enqueue_new_records(["direct_message"])

            _write_record(store, "dm_rec2", "direct_message", "2026-05-07T11:00:00+00:00")
            result2 = service.enqueue_new_records(["direct_message"])

        self.assertEqual(result2.packets_enqueued, 1)
        self.assertIn("dm_rec2", result2.record_ids_sent)
        self.assertEqual(len(runtime.calls), 2)

    def test_multiple_source_types_produce_separate_packets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = AmbientContextStore(root)
            _write_record(store, "dm_rec1", "direct_message", "2026-05-07T10:00:00+00:00")
            _write_record(store, "gm_rec1", "group_message", "2026-05-07T10:00:00+00:00")
            runtime = _FakeRuntime()
            service = AmbientAnalysisIntakeService(root, runtime)

            result = service.enqueue_new_records(["direct_message", "group_message"])

        self.assertEqual(result.packets_enqueued, 2)
        self.assertEqual(len(runtime.calls), 2)
        trigger_kinds = {c["trigger_kind"] for c in runtime.calls}
        self.assertEqual(trigger_kinds, {"ambient_context_batch"})

    def test_max_packets_per_tick_is_respected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = AmbientContextStore(root)
            source_types = [f"type_{i}" for i in range(MAX_PACKETS_PER_TICK + 2)]
            for idx, st in enumerate(source_types):
                _write_record(store, f"rec_{idx}", st, f"2026-05-07T{10 + idx:02d}:00:00+00:00")
            runtime = _FakeRuntime()
            service = AmbientAnalysisIntakeService(root, runtime)

            result = service.enqueue_new_records(source_types)

        self.assertEqual(result.packets_enqueued, MAX_PACKETS_PER_TICK)
        self.assertEqual(len(runtime.calls), MAX_PACKETS_PER_TICK)

    def test_watermark_is_persisted_to_disk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = AmbientContextStore(root)
            _write_record(store, "dm_rec1", "direct_message", "2026-05-07T10:00:00+00:00")
            runtime = _FakeRuntime()
            service = AmbientAnalysisIntakeService(root, runtime)

            service.enqueue_new_records(["direct_message"])
            watermark = service.get_watermark("direct_message")

        self.assertEqual(watermark, "2026-05-07T10:00:00+00:00")

    def test_packet_contains_source_type_and_record_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = AmbientContextStore(root)
            _write_record(store, "dm_rec1", "direct_message", "2026-05-07T10:00:00+00:00")
            runtime = _FakeRuntime()
            service = AmbientAnalysisIntakeService(root, runtime)

            service.enqueue_new_records(["direct_message"])

        packet = runtime.calls[0]["packet"]
        self.assertEqual(packet["source_type"], "direct_message")
        self.assertIn("dm_rec1", packet["record_ids"])
        self.assertEqual(packet["record_count"], 1)

    def test_config_chat_id_is_passed_to_loop_input(self) -> None:
        class _FakeConfig:
            feishu_owner_report_chat_id = "oc_report_1"

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = AmbientContextStore(root)
            _write_record(store, "dm_rec1", "direct_message", "2026-05-07T10:00:00+00:00")
            runtime = _FakeRuntime()
            service = AmbientAnalysisIntakeService(root, runtime, config=_FakeConfig())

            service.enqueue_new_records(["direct_message"])

        self.assertEqual(runtime.calls[0]["chat_id"], "oc_report_1")

    def test_enqueue_failure_does_not_raise(self) -> None:
        class _FailRuntime:
            def enqueue_perception(self, loop_input: dict[str, Any]) -> None:
                raise RuntimeError("queue full")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = AmbientContextStore(root)
            _write_record(store, "dm_rec1", "direct_message", "2026-05-07T10:00:00+00:00")
            service = AmbientAnalysisIntakeService(root, _FailRuntime())

            result = service.enqueue_new_records(["direct_message"])

        self.assertTrue(result.ok)
        self.assertEqual(result.packets_enqueued, 0)


if __name__ == "__main__":
    unittest.main()
