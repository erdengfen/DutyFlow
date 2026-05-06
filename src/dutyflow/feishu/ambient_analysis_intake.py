# 本文件负责把新增主动感知记录批量封装为正式 runtime 可消费的分析输入，并维护入队水位。

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

if __package__ in {None, ""}:
    _SRC_ROOT = Path(__file__).resolve().parents[2]
    if str(_SRC_ROOT) not in sys.path:
        sys.path.insert(0, str(_SRC_ROOT))

from dutyflow.feishu.ambient_context import (
    AmbientContextPacket,
    AmbientContextScanQuery,
    AmbientContextStore,
)
from dutyflow.storage.file_store import FileStore

# 关键开关：单次入队最多包含的 ambient 记录数，避免 context 过大影响模型输入质量。
MAX_RECORDS_PER_INTAKE = 50
# 关键开关：单次最多派发 packet 数量，避免 runtime queue 在短时间内积压过多分析任务。
MAX_PACKETS_PER_TICK = 5
# 默认分析来源类型列表，按优先级排列。
DEFAULT_SOURCE_TYPES = ("direct_message", "group_message", "user_document")


@dataclass(frozen=True)
class AmbientAnalysisIntakeResult:
    """表示单次入队调用的结果快照。"""

    ok: bool
    status: str
    packets_enqueued: int
    record_ids_sent: tuple[str, ...]
    analysis_ids: tuple[str, ...]
    detail: str


class AmbientAnalysisIntakeService:
    """把新增 ambient_context 批量封装为正式 runtime 分析输入并送入队列。"""

    def __init__(
        self,
        project_root: Path,
        runtime_service: Any,
        *,
        ambient_store: AmbientContextStore | None = None,
        config: Any = None,
    ) -> None:
        """绑定工作区、runtime 队列和可选依赖。"""
        self.project_root = Path(project_root).resolve()
        self.runtime_service = runtime_service
        self.ambient_store = ambient_store or AmbientContextStore(self.project_root)
        self._config = config
        self._watermark_path = (
            self.project_root / "data" / "state" / "ambient_intake_watermark.md"
        )

    def enqueue_new_records(
        self,
        source_types: Sequence[str] | None = None,
    ) -> AmbientAnalysisIntakeResult:
        """扫描各 source_type 的新增记录并批量送入正式 runtime。"""
        types = list(source_types or DEFAULT_SOURCE_TYPES)
        all_analysis_ids: list[str] = []
        all_record_ids: list[str] = []
        packets_enqueued = 0

        for source_type in types:
            if packets_enqueued >= MAX_PACKETS_PER_TICK:
                break
            packet = self._scan_source(source_type)
            if packet is None or packet.record_count == 0:
                continue
            analysis_id = self._enqueue_packet(packet)
            if not analysis_id:
                continue
            all_analysis_ids.append(analysis_id)
            all_record_ids.extend(packet.record_ids)
            self._save_watermark(source_type, packet.time_window_end)
            packets_enqueued += 1

        return AmbientAnalysisIntakeResult(
            ok=True,
            status="ok",
            packets_enqueued=packets_enqueued,
            record_ids_sent=tuple(all_record_ids),
            analysis_ids=tuple(all_analysis_ids),
            detail=f"enqueued {packets_enqueued} packet(s)",
        )

    def get_watermark(self, source_type: str) -> str:
        """返回某 source_type 的当前入队水位时间，供调试和测试使用。"""
        return self._load_watermark(source_type)

    def _scan_source(self, source_type: str) -> AmbientContextPacket | None:
        """按 source_type 扫描水位之后的新增记录，无新增时返回 None。"""
        watermark = self._load_watermark(source_type)
        query = AmbientContextScanQuery(
            source_type=source_type,
            created_after=watermark,
            limit=MAX_RECORDS_PER_INTAKE,
        )
        packet = self.ambient_store.build_context_packet(query)
        if packet.record_count == 0:
            return None
        return packet

    def _enqueue_packet(self, packet: AmbientContextPacket) -> str:
        """把单个 packet 送入 runtime queue，成功返回 analysis_id，失败返回空串。"""
        chat_id = _owner_report_chat_id(self._config)
        loop_input: dict[str, Any] = {
            "perception_id": f"amb_batch_{packet.packet_id}",
            "trigger_kind": "ambient_context_batch",
            "source_type": packet.source_type,
            "chat_id": chat_id,
            "packet": packet.to_payload(),
        }
        try:
            self.runtime_service.enqueue_perception(loop_input)
            return f"amb_batch_{packet.packet_id}"
        except Exception:  # noqa: BLE001
            return ""

    def _load_watermark(self, source_type: str) -> str:
        """读取某 source_type 的最近入队时间水位，未记录时返回空串。"""
        return self._read_watermarks().get(source_type, "")

    def _save_watermark(self, source_type: str, created_at: str) -> None:
        """更新某 source_type 的入队时间水位。"""
        watermarks = self._read_watermarks()
        watermarks[source_type] = created_at
        self._write_watermarks(watermarks)

    def _read_watermarks(self) -> dict[str, str]:
        """从 Markdown 状态文件读取全量水位数据。"""
        if not self._watermark_path.exists():
            return {}
        result: dict[str, str] = {}
        for line in self._watermark_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("- ") and ": " in line:
                key, _, value = line[2:].partition(": ")
                result[key.strip()] = value.strip()
        return result

    def _write_watermarks(self, watermarks: dict[str, str]) -> None:
        """把全量水位写回 Markdown 状态文件。"""
        FileStore(self.project_root).ensure_dir(self._watermark_path.parent)
        lines = ["# ambient intake watermarks", ""]
        lines.extend(f"- {k}: {v}" for k, v in sorted(watermarks.items()))
        lines.append("")
        self._watermark_path.write_text("\n".join(lines), encoding="utf-8")


def _owner_report_chat_id(config: Any) -> str:
    """从配置中取 owner report chat_id，未配置时返回空串。"""
    if config is None:
        return ""
    return str(getattr(config, "feishu_owner_report_chat_id", "") or "")


def _now_iso() -> str:
    """返回当前 UTC ISO 时间字符串。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _self_test() -> None:
    """验证 intake service 可扫描空存储并返回零 packet。"""
    import tempfile

    from dutyflow.feishu.ambient_context import AmbientContextStore

    class _FakeRuntime:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def enqueue_perception(self, loop_input: dict[str, Any]) -> None:
            self.calls.append(loop_input)

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        runtime = _FakeRuntime()
        service = AmbientAnalysisIntakeService(root, runtime)
        result = service.enqueue_new_records()

    assert result.ok
    assert result.packets_enqueued == 0
    assert len(runtime.calls) == 0


if __name__ == "__main__":
    _self_test()
    print("dutyflow ambient_analysis_intake self-test passed")
