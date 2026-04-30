# 本文件负责后台任务结果 Markdown 的占位创建、读取和更新。

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import sys

if __package__ in {None, ""}:
    _SRC_ROOT = Path(__file__).resolve().parents[2]
    if str(_SRC_ROOT) not in sys.path:
        sys.path.insert(0, str(_SRC_ROOT))

from dutyflow.storage.file_store import FileStore
from dutyflow.storage.markdown_store import MarkdownDocument, MarkdownStore
from dutyflow.tasks.task_state import TaskRecord


@dataclass(frozen=True)
class TaskResultRecord:
    """表示一条后台任务结果记录，独立于任务状态文件保存可回推内容。"""

    path: Path
    result_id: str
    task_id: str
    status: str
    task_status: str
    source_task_file: str
    created_at: str
    updated_at: str
    summary: str
    user_visible_final_text: str
    stop_reason: str
    tool_result_count: str
    query_id: str
    raw_result: str


class TaskResultStore:
    """封装 `data/tasks/results/result_<task_id>.md` 的读写。"""

    def __init__(
        self,
        project_root: Path,
        *,
        markdown_store: MarkdownStore | None = None,
    ) -> None:
        """绑定工作区并准备任务结果目录。"""
        self.project_root = Path(project_root).resolve()
        self.markdown_store = markdown_store or MarkdownStore(FileStore(self.project_root))
        self.results_dir = self.project_root / "data" / "tasks" / "results"
        self.markdown_store.file_store.ensure_dir(self.results_dir)

    def create_placeholder(self, task: TaskRecord) -> TaskResultRecord:
        """为任务创建结果占位；已存在时直接返回现有记录。"""
        existing = self.read_result(task.task_id)
        if existing is not None:
            return existing
        now = _now_iso()
        record = TaskResultRecord(
            path=_build_result_path(self.results_dir, task.task_id),
            result_id=_build_result_id(task.task_id),
            task_id=task.task_id,
            status="placeholder",
            task_status=task.status,
            source_task_file=_relative_path(self.project_root, task.path),
            created_at=now,
            updated_at=now,
            summary="等待后台 subagent 执行。",
            user_visible_final_text="",
            stop_reason="",
            tool_result_count="0",
            query_id="",
            raw_result="",
        )
        self._write_record(record)
        return record

    def mark_running(self, task: TaskRecord, *, query_id: str) -> TaskResultRecord:
        """把任务结果文件标记为后台 subagent 正在处理。"""
        return self.update_result(
            task,
            status="running",
            summary="后台 subagent 已开始执行。",
            user_visible_final_text="",
            stop_reason="",
            tool_result_count=0,
            query_id=query_id,
            raw_result="",
        )

    def update_result(
        self,
        task: TaskRecord,
        *,
        status: str,
        summary: str,
        user_visible_final_text: str,
        stop_reason: str,
        tool_result_count: int,
        query_id: str,
        raw_result: str,
    ) -> TaskResultRecord:
        """按后台 subagent 执行结果更新任务结果 Markdown。"""
        existing = self.create_placeholder(task)
        record = TaskResultRecord(
            path=existing.path,
            result_id=existing.result_id,
            task_id=task.task_id,
            status=status.strip(),
            task_status=task.status,
            source_task_file=_relative_path(self.project_root, task.path),
            created_at=existing.created_at,
            updated_at=_now_iso(),
            summary=summary.strip(),
            user_visible_final_text=user_visible_final_text.strip(),
            stop_reason=stop_reason.strip(),
            tool_result_count=str(tool_result_count),
            query_id=query_id.strip(),
            raw_result=raw_result.strip(),
        )
        self._write_record(record)
        return record

    def read_result(self, task_id: str) -> TaskResultRecord | None:
        """按任务 ID 读取对应结果记录。"""
        path = _build_result_path(self.results_dir, task_id)
        if not self.markdown_store.exists(path):
            return None
        return self._read_record(path)

    def _write_record(self, record: TaskResultRecord) -> None:
        """把结果记录渲染并写入 Markdown。"""
        document = MarkdownDocument(frontmatter=_build_frontmatter(record), body=_build_body(record))
        self.markdown_store.write_document(record.path, document)

    def _read_record(self, path: Path) -> TaskResultRecord:
        """从已落盘 Markdown 重建任务结果记录。"""
        document = self.markdown_store.read_document(path)
        metadata = _parse_key_value_section(self.markdown_store.extract_section(path, "Execution Metadata"))
        return TaskResultRecord(
            path=path,
            result_id=document.frontmatter.get("id", ""),
            task_id=document.frontmatter.get("task_id", ""),
            status=document.frontmatter.get("status", ""),
            task_status=document.frontmatter.get("task_status", ""),
            source_task_file=document.frontmatter.get("source_task_file", ""),
            created_at=document.frontmatter.get("created_at", ""),
            updated_at=document.frontmatter.get("updated_at", ""),
            summary=self.markdown_store.extract_section(path, "Summary"),
            user_visible_final_text=self.markdown_store.extract_section(path, "User Visible Final Text"),
            stop_reason=metadata.get("stop_reason", ""),
            tool_result_count=metadata.get("tool_result_count", ""),
            query_id=metadata.get("query_id", ""),
            raw_result=self.markdown_store.extract_section(path, "Raw Result"),
        )


def _build_frontmatter(record: TaskResultRecord) -> dict[str, str]:
    """构造任务结果 frontmatter，便于后续按任务 ID 检索。"""
    return {
        "schema": "dutyflow.task_result.v1",
        "id": record.result_id,
        "task_id": record.task_id,
        "status": record.status,
        "task_status": record.task_status,
        "source_task_file": record.source_task_file,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


def _build_body(record: TaskResultRecord) -> str:
    """渲染人工可读的任务结果正文。"""
    return "\n".join(
        (
            f"# Task Result {record.result_id}",
            "",
            "## Summary",
            "",
            record.summary,
            "",
            "## User Visible Final Text",
            "",
            record.user_visible_final_text,
            "",
            "## Execution Metadata",
            "",
            f"- stop_reason: {record.stop_reason}",
            f"- tool_result_count: {record.tool_result_count}",
            f"- query_id: {record.query_id}",
            "",
            "## Raw Result",
            "",
            record.raw_result,
            "",
        )
    )


def _build_result_id(task_id: str) -> str:
    """生成与任务 ID 一一对应的结果 ID。"""
    return f"result_{task_id}"


def _build_result_path(results_dir: Path, task_id: str) -> Path:
    """生成任务结果 Markdown 路径。"""
    return results_dir / f"{_build_result_id(task_id)}.md"


def _relative_path(project_root: Path, path: Path) -> str:
    """把绝对路径转换成项目内相对路径。"""
    try:
        return str(path.relative_to(project_root))
    except ValueError:
        return str(path)


def _now_iso() -> str:
    """返回当前本地时区 ISO 时间字符串。"""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _parse_key_value_section(section_text: str) -> dict[str, str]:
    """解析 `- key: value` 格式的正文 section。"""
    parsed: dict[str, str] = {}
    for raw_line in section_text.splitlines():
        line = raw_line.strip()
        if not line.startswith("- ") or ":" not in line:
            continue
        key, value = line[2:].split(":", 1)
        parsed[key.strip()] = value.strip()
    return parsed


def _self_test() -> None:
    """验证任务结果占位和更新可以稳定读回。"""
    import tempfile

    from dutyflow.tasks.task_state import TaskStore

    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        task = TaskStore(root).create_task(title="self test", task_id="task_selftest")
        store = TaskResultStore(root)
        placeholder = store.create_placeholder(task)
        updated = store.update_result(
            task,
            status="completed",
            summary="done",
            user_visible_final_text="用户可见结果",
            stop_reason="stop",
            tool_result_count=0,
            query_id="query_selftest",
            raw_result="raw",
        )
        loaded = store.read_result(task.task_id)
    assert placeholder.status == "placeholder"
    assert updated.status == "completed"
    assert loaded is not None
    assert loaded.user_visible_final_text == "用户可见结果"


if __name__ == "__main__":
    _self_test()
    print("dutyflow task result store self-test passed")
