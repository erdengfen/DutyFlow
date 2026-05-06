# 本文件负责系统预制总结任务的创建和去重，供主动感知调度层按固定间隔调用。

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    _SRC_ROOT = Path(__file__).resolve().parents[2]
    if str(_SRC_ROOT) not in sys.path:
        sys.path.insert(0, str(_SRC_ROOT))

from dutyflow.feishu.ambient_context import AmbientContextScanQuery, AmbientContextStore
from dutyflow.storage.file_store import FileStore
from dutyflow.storage.markdown_store import MarkdownDocument, MarkdownStore
from dutyflow.tasks.task_state import TaskStore

# 关键开关：总结任务回溯最近 24 小时的 ambient_context，避免单次总结范围过宽。
SUMMARY_LOOKBACK_HOURS = 24
# 关键开关：同类总结任务最短间隔 20 小时，避免频繁重复创建同类总结。
SUMMARY_COOLDOWN_HOURS = 20
# 关键开关：每次 tick 最多创建 4 条总结任务，防止 tick 单次创建太多任务堆积。
MAX_SUMMARY_TASKS_PER_TICK = 4
# 关键开关：单条总结任务上下文引用最多携带 30 个 ambient record_id，避免 resume_payload 膨胀。
MAX_CONTEXT_REFS_PER_SUMMARY = 30

_WATERMARK_SCHEMA = "dutyflow.summary_task_watermark.v1"
_WATERMARK_PATH = Path("data/state/summary_task_watermark.md")

_SUMMARY_CONFIGS: dict[str, dict[str, Any]] = {
    "dm_summary": {
        "title": "私聊摘要任务",
        "source_types": ("direct_message",),
        "goal": (
            "分析最近 {lookback_hours} 小时内的私聊消息 ambient_context 记录，"
            "识别需要提醒 owner 的重要事项、待回复消息和时间承诺，"
            "输出结构化事项清单（重要、待回复、有截止时间）。"
        ),
        "success_criteria": (
            "输出包含：需要立即关注的消息列表、待回复消息列表、"
            "有明确时间承诺的事项列表；无信息时返回【近期无重要私聊】。"
        ),
        "resolved_tools": "read_context_ref",
    },
    "group_summary": {
        "title": "群聊摘要任务",
        "source_types": ("group_message",),
        "goal": (
            "分析最近 {lookback_hours} 小时内的群聊消息 ambient_context 记录，"
            "识别与 owner 相关的 @提及、待跟进任务、风险和关键决策，"
            "输出结构化摘要，区分需要 owner 回应和只需知晓的内容。"
        ),
        "success_criteria": (
            "输出包含：owner 被 @ 的消息、群内提到 owner 负责的任务、"
            "风险或需决策的议题；无信息时返回【近期无重要群聊消息】。"
        ),
        "resolved_tools": "read_context_ref",
    },
    "doc_summary": {
        "title": "文档线索摘要任务",
        "source_types": ("user_document",),
        "goal": (
            "分析最近 {lookback_hours} 小时内的云盘文档线索 ambient_context 记录，"
            "识别新增或修改的文档、可疑待办候选和需要正文补读的 docx 文档，"
            "对重要文档可调用 feishu_read_doc 补读正文摘要。"
        ),
        "success_criteria": (
            "输出包含：新增或修改文档清单、建议补读正文的 docx 列表、"
            "可疑待办；无信息时返回【近期无新增文档线索】。"
        ),
        "resolved_tools": "read_context_ref,feishu_read_doc",
    },
    "daily_summary": {
        "title": "每日综合摘要任务",
        "source_types": ("direct_message", "group_message", "user_document"),
        "goal": (
            "汇总最近 {lookback_hours} 小时内的私聊、群聊、云盘文档线索 ambient_context 记录，"
            "形成当日事项清单，区分需立即处理、定时跟进、已完成和无需处理的事项，"
            "并指出重要风险和时间承诺。"
        ),
        "success_criteria": (
            "输出一份完整的当日事项清单，涵盖私聊、群聊、文档三个来源，"
            "按优先级排序并标注来源；无信息时返回【今日无待处理事项】。"
        ),
        "resolved_tools": "read_context_ref,feishu_read_doc",
    },
}


@dataclass(frozen=True)
class SummaryTaskCreateResult:
    """表示单条系统预制总结任务的创建结果。"""

    ok: bool
    summary_type: str
    task_id: str
    task_file: str
    context_ref_count: int
    skipped_reason: str
    detail: str


@dataclass(frozen=True)
class SummaryTaskIntakeResult:
    """表示一次 tick 中总结任务创建批次结果。"""

    ok: bool
    tasks_created: int
    results: tuple[SummaryTaskCreateResult, ...]
    detail: str


class SummaryTaskIntakeService:
    """按预设间隔为各类总结任务创建后台队列条目，供 BackgroundTaskWorker 执行。"""

    def __init__(
        self,
        project_root: Path,
        *,
        task_store: TaskStore | None = None,
        ambient_store: AmbientContextStore | None = None,
    ) -> None:
        """绑定工作区，延迟初始化 task_store 和 ambient_store。"""
        self.project_root = Path(project_root).resolve()
        self.task_store = task_store or TaskStore(self.project_root)
        self.ambient_store = ambient_store or AmbientContextStore(self.project_root)
        self._markdown_store = MarkdownStore(FileStore(self.project_root))

    def create_due_summary_tasks(
        self,
        *,
        summary_types: tuple[str, ...] | None = None,
        lookback_hours: int = SUMMARY_LOOKBACK_HOURS,
        cooldown_hours: int = SUMMARY_COOLDOWN_HOURS,
        max_tasks: int = MAX_SUMMARY_TASKS_PER_TICK,
    ) -> SummaryTaskIntakeResult:
        """为冷却期已过的总结类型创建后台任务，返回本次创建结果。"""
        types = summary_types or tuple(_SUMMARY_CONFIGS.keys())
        watermarks = self._read_watermarks()
        results: list[SummaryTaskCreateResult] = []
        created = 0

        for summary_type in types:
            if created >= max_tasks:
                break
            if summary_type not in _SUMMARY_CONFIGS:
                continue
            last_created = watermarks.get(summary_type, "")
            if not _cooldown_expired(last_created, cooldown_hours):
                results.append(SummaryTaskCreateResult(
                    ok=False,
                    summary_type=summary_type,
                    task_id="",
                    task_file="",
                    context_ref_count=0,
                    skipped_reason="cooldown_active",
                    detail=f"last created: {last_created}",
                ))
                continue
            result = self._create_summary_task(summary_type, lookback_hours)
            results.append(result)
            if result.ok:
                watermarks = {**watermarks, summary_type: _now_iso()}
                self._write_watermarks(watermarks)
                created += 1

        return SummaryTaskIntakeResult(
            ok=True,
            tasks_created=created,
            results=tuple(results),
            detail=f"created {created} summary tasks",
        )

    def get_last_created_at(self, summary_type: str) -> str:
        """返回指定总结类型上次创建时间，供 CLI 或状态检查使用。"""
        return self._read_watermarks().get(summary_type, "")

    def _create_summary_task(
        self,
        summary_type: str,
        lookback_hours: int,
    ) -> SummaryTaskCreateResult:
        """扫描 ambient_context，构造任务参数并落盘任务记录。"""
        config = _SUMMARY_CONFIGS[summary_type]
        source_types: tuple[str, ...] = config["source_types"]
        context_refs = self._collect_context_refs(source_types, lookback_hours)
        goal = config["goal"].format(lookback_hours=lookback_hours)
        success_criteria = config["success_criteria"]
        resolved_tools = str(config["resolved_tools"])
        resume_payload = _build_resume_payload(
            goal=goal,
            success_criteria=success_criteria,
            context_refs=context_refs,
        )
        try:
            record = self.task_store.create_task(
                title=str(config["title"]),
                status="queued",
                run_mode="async_now",
                resolved_tools=resolved_tools,
                summary=f"系统预制总结任务：{config['title']}，回溯 {lookback_hours} 小时",
                resume_payload=resume_payload,
                source_id=f"summary_task_intake:{summary_type}",
                decision_trace=f"由 SummaryTaskIntakeService 按预设间隔自动创建（{summary_type}）",
                next_action="由 BackgroundTaskWorker 执行，结果通过飞书回推 owner。",
            )
            task_file = _relative_path(self.project_root, record.path)
            return SummaryTaskCreateResult(
                ok=True,
                summary_type=summary_type,
                task_id=record.task_id,
                task_file=task_file,
                context_ref_count=len(context_refs),
                skipped_reason="",
                detail="ok",
            )
        except Exception as exc:  # noqa: BLE001
            return SummaryTaskCreateResult(
                ok=False,
                summary_type=summary_type,
                task_id="",
                task_file="",
                context_ref_count=len(context_refs),
                skipped_reason="create_failed",
                detail=str(exc),
            )

    def _collect_context_refs(
        self,
        source_types: tuple[str, ...],
        lookback_hours: int,
    ) -> tuple[str, ...]:
        """从 ambient_context 扫描最近 N 小时的记录 ID 作为任务上下文引用。"""
        created_after = _hours_ago_iso(lookback_hours)
        record_ids: list[str] = []
        for source_type in source_types:
            query = AmbientContextScanQuery(
                source_type=source_type,
                created_after=created_after,
                limit=MAX_CONTEXT_REFS_PER_SUMMARY,
            )
            try:
                records = self.ambient_store.scan_records(query)
                record_ids.extend(record.record_id for record in records)
            except Exception:  # noqa: BLE001
                pass
        seen: set[str] = set()
        unique: list[str] = []
        for rid in record_ids:
            if rid not in seen:
                seen.add(rid)
                unique.append(rid)
        return tuple(unique[:MAX_CONTEXT_REFS_PER_SUMMARY])

    def _read_watermarks(self) -> dict[str, str]:
        """读取各总结类型的上次创建时间水位线。"""
        path = self._markdown_store.file_store.resolve(_WATERMARK_PATH)
        if not self._markdown_store.exists(path):
            return {}
        try:
            doc = self._markdown_store.read_document(path)
            fm = doc.frontmatter
            if fm.get("schema") != _WATERMARK_SCHEMA:
                return {}
            return {
                key: str(value)
                for key, value in fm.items()
                if key not in {"schema", "updated_at"} and value
            }
        except Exception:  # noqa: BLE001
            return {}

    def _write_watermarks(self, watermarks: dict[str, str]) -> None:
        """持久化各总结类型的上次创建时间水位线。"""
        fm: dict[str, str] = {"schema": _WATERMARK_SCHEMA, "updated_at": _now_iso()}
        fm.update({key: value for key, value in watermarks.items() if value})
        doc = MarkdownDocument(frontmatter=fm, body="# Summary Task Watermarks\n")
        self._markdown_store.write_document(_WATERMARK_PATH, doc)


def _build_resume_payload(
    *,
    goal: str,
    success_criteria: str,
    context_refs: tuple[str, ...],
) -> str:
    """把总结任务参数序列化为单行 resume_payload 字符串。"""
    goal_text = goal.replace("\n", " ").strip()
    success_text = success_criteria.replace("\n", " ").strip()
    refs_text = ",".join(context_refs)
    return f"goal={goal_text}; success_criteria={success_text}; context_refs={refs_text}"


def _cooldown_expired(last_created_at: str, cooldown_hours: int) -> bool:
    """判断指定总结类型的冷却期是否已过。"""
    if not last_created_at:
        return True
    try:
        last_dt = datetime.fromisoformat(last_created_at)
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return True
    return (_now_dt() - last_dt) >= timedelta(hours=cooldown_hours)


def _hours_ago_iso(hours: int) -> str:
    """返回 N 小时前的 UTC ISO 时间字符串。"""
    return (_now_dt() - timedelta(hours=hours)).isoformat(timespec="seconds")


def _relative_path(root: Path, path: Path | str) -> str:
    """返回工作区相对路径字符串。"""
    try:
        return str(Path(path).resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def _now_iso() -> str:
    """返回当前 UTC ISO 时间字符串。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _now_dt() -> datetime:
    """返回当前 UTC datetime。"""
    return datetime.now(timezone.utc)


def _self_test() -> None:
    """验证 SummaryTaskIntakeService 可在空 ambient store 下创建总结任务。"""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        service = SummaryTaskIntakeService(root)
        result = service.create_due_summary_tasks(lookback_hours=1)

    assert result.ok
    assert result.tasks_created == 4
    for r in result.results:
        assert r.ok, f"task creation failed: {r.summary_type} {r.detail}"


if __name__ == "__main__":
    _self_test()
    print("dutyflow feishu summary_task_intake self-test passed")
