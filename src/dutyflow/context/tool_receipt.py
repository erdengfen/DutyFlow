# 本文件负责把工具结果转换为可进入上下文压缩链路的短收据。

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any, Mapping

from dutyflow.agent.state import AgentContentBlock
from dutyflow.agent.tools.types import ToolResultEnvelope
from dutyflow.context.runtime_context import WorkingSet


# 关键开关：Tool Receipt 摘要最多保留 500 字，避免把长工具结果继续塞回运行时上下文。
SUMMARY_MAX_CHARS = 500
# 关键开关：摘要长度低于 20 字时无法承载基本状态说明，因此构造器拒绝更小上限。
MIN_SUMMARY_CHARS = 20
TOOL_RECEIPT_STATUSES = frozenset({"success", "error", "waiting_approval", "rejected", "unknown"})


@dataclass(frozen=True)
class ToolReceipt:
    """表示工具结果在运行时上下文中的短收据。"""

    tool_use_id: str
    tool_name: str
    status: str
    ok: bool
    is_error: bool
    error_kind: str
    summary: str
    full_result_ref: str
    retryable: bool
    retry_exhausted: bool
    attempt_count: int
    attachments: tuple[str, ...] = ()
    context_modifier_types: tuple[str, ...] = ()
    task_id: str = ""
    event_id: str = ""
    approval_ids: tuple[str, ...] = ()
    perception_ids: tuple[str, ...] = ()
    file_paths: tuple[str, ...] = ()
    impacts_current_decision: bool = True

    def __post_init__(self) -> None:
        """校验 Tool Receipt 的必要字段和稳定枚举。"""
        if not self.tool_use_id:
            raise ValueError("ToolReceipt.tool_use_id is required")
        if not self.tool_name:
            raise ValueError("ToolReceipt.tool_name is required")
        if self.status not in TOOL_RECEIPT_STATUSES:
            raise ValueError(f"Unknown ToolReceipt.status: {self.status}")
        if self.attempt_count < 1:
            raise ValueError("ToolReceipt.attempt_count must be >= 1")

    def to_dict(self) -> dict[str, object]:
        """返回可用于测试、日志和后续 journal 的稳定字典。"""
        payload = asdict(self)
        for key in _TOOL_RECEIPT_TUPLE_FIELDS:
            payload[key] = list(payload[key])
        return payload

    def to_context_text(self) -> str:
        """返回适合放入模型上下文的单行收据文本。"""
        return (
            f"ToolReceipt(tool={self.tool_name}, tool_use_id={self.tool_use_id}, "
            f"status={self.status}, summary={self.summary}, ref={self.full_result_ref})"
        )


class ToolReceiptBuilder:
    """从工具结果信封或已回写的 tool_result block 构造 Tool Receipt。"""

    def __init__(self, summary_max_chars: int = SUMMARY_MAX_CHARS) -> None:
        """设置摘要长度上限。"""
        if summary_max_chars < MIN_SUMMARY_CHARS:
            raise ValueError(f"summary_max_chars must be >= {MIN_SUMMARY_CHARS}")
        self.summary_max_chars = summary_max_chars

    def from_envelope(
        self,
        result: ToolResultEnvelope,
        *,
        working_set: WorkingSet | None = None,
        full_result_ref: str = "",
    ) -> ToolReceipt:
        """从 ToolResultEnvelope 构造完整 Tool Receipt。"""
        payload = _json_mapping(result.content)
        attachments = tuple(result.attachments)
        file_paths = _stable_values(attachments + _payload_file_paths(payload))
        status = _status_from_result(result.ok, result.is_error, result.error_kind)
        return ToolReceipt(
            tool_use_id=result.tool_use_id,
            tool_name=result.tool_name,
            status=status,
            ok=result.ok,
            is_error=result.is_error,
            error_kind=result.error_kind,
            summary=_trim_summary(result.content, self.summary_max_chars),
            full_result_ref=full_result_ref or _default_result_ref(result.tool_use_id),
            retryable=result.retryable,
            retry_exhausted=result.retry_exhausted,
            attempt_count=result.attempt_count,
            attachments=attachments,
            context_modifier_types=_context_modifier_types(result.context_modifiers),
            task_id=_task_id(payload, working_set),
            event_id=_event_id(payload, working_set),
            approval_ids=_approval_ids(payload, result.context_modifiers),
            perception_ids=_perception_ids(payload, result.context_modifiers),
            file_paths=file_paths,
            impacts_current_decision=_impacts_current_decision(result.tool_use_id, status, working_set),
        )

    def from_agent_block(
        self,
        block: AgentContentBlock,
        *,
        working_set: WorkingSet | None = None,
        full_result_ref: str = "",
    ) -> ToolReceipt:
        """从已写回 AgentState 的 tool_result block 构造基础 Tool Receipt。"""
        if block.type != "tool_result":
            raise ValueError("ToolReceiptBuilder.from_agent_block requires a tool_result block")
        payload = _json_mapping(block.content)
        status = _status_from_result(not block.is_error, block.is_error, "")
        return ToolReceipt(
            tool_use_id=block.tool_use_id,
            tool_name=block.tool_name,
            status=status,
            ok=not block.is_error,
            is_error=block.is_error,
            error_kind="",
            summary=_trim_summary(block.content, self.summary_max_chars),
            full_result_ref=full_result_ref or _default_result_ref(block.tool_use_id),
            retryable=False,
            retry_exhausted=False,
            attempt_count=1,
            task_id=_task_id(payload, working_set),
            event_id=_event_id(payload, working_set),
            approval_ids=_approval_ids(payload, ()),
            perception_ids=_perception_ids(payload, ()),
            file_paths=_payload_file_paths(payload),
            impacts_current_decision=_impacts_current_decision(block.tool_use_id, status, working_set),
        )


def _trim_summary(content: str, max_chars: int) -> str:
    """把工具结果内容压缩为单行短摘要。"""
    normalized = " ".join(str(content).split())
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max_chars - 3] + "..."


def _status_from_result(ok: bool, is_error: bool, error_kind: str) -> str:
    """根据工具结果字段生成稳定状态。"""
    if error_kind == "approval_waiting":
        return "waiting_approval"
    if error_kind == "approval_rejected":
        return "rejected"
    if is_error or not ok:
        return "error"
    return "success"


def _json_mapping(content: str) -> dict[str, Any]:
    """解析 JSON 对象工具结果；非 JSON 或非对象时返回空字典。"""
    try:
        payload = json.loads(content)
    except (TypeError, ValueError):
        return {}
    if isinstance(payload, Mapping):
        return dict(payload)
    return {}


def _payload_file_paths(payload: Mapping[str, Any]) -> tuple[str, ...]:
    """从工具结果 payload 中提取明显的文件路径字段。"""
    paths: list[str] = []
    for key, value in payload.items():
        if isinstance(value, str) and _is_file_path_key(str(key)):
            paths.append(value)
    return _stable_values(paths)


def _is_file_path_key(key: str) -> bool:
    """判断 payload 字段名是否表示文件路径。"""
    return key == "file_path" or key.endswith("_file") or key.endswith("_path")


def _context_modifier_types(modifiers: tuple[Mapping[str, Any], ...]) -> tuple[str, ...]:
    """提取 context modifier 的类型集合。"""
    return _stable_values(str(item.get("type", "")) for item in modifiers if item.get("type"))


def _task_id(payload: Mapping[str, Any], working_set: WorkingSet | None) -> str:
    """优先从 payload，其次从 WorkingSet 提取任务锚点。"""
    value = str(payload.get("task_id", "")).strip()
    if value:
        return value
    return working_set.current_task_id if working_set else ""


def _event_id(payload: Mapping[str, Any], working_set: WorkingSet | None) -> str:
    """优先从 payload，其次从 WorkingSet 提取事件锚点。"""
    value = str(payload.get("event_id", "") or payload.get("source_event_id", "")).strip()
    if value:
        return value
    return working_set.current_event_id if working_set else ""


def _approval_ids(
    payload: Mapping[str, Any],
    modifiers: tuple[Mapping[str, Any], ...],
) -> tuple[str, ...]:
    """从工具结果和控制提示中提取审批 ID。"""
    values = _id_values(payload, ("approval_id", "approval_ids"))
    values += _modifier_id_values(modifiers, ("approval_id", "approval_ids"))
    return _stable_values(values)


def _perception_ids(
    payload: Mapping[str, Any],
    modifiers: tuple[Mapping[str, Any], ...],
) -> tuple[str, ...]:
    """从工具结果和控制提示中提取感知记录 ID。"""
    values = _id_values(payload, ("perception_id", "perception_ids"))
    values += _modifier_id_values(modifiers, ("perception_id", "perception_ids"))
    return _stable_values(values)


def _id_values(payload: Mapping[str, Any], keys: tuple[str, ...]) -> tuple[str, ...]:
    """从映射中按字段名提取稳定 ID 字符串。"""
    values: list[str] = []
    for key in keys:
        values.extend(_string_values(payload.get(key, "")))
    return tuple(values)


def _modifier_id_values(
    modifiers: tuple[Mapping[str, Any], ...],
    keys: tuple[str, ...],
) -> tuple[str, ...]:
    """从 context modifiers 中提取稳定 ID 字符串。"""
    values: list[str] = []
    for modifier in modifiers:
        values.extend(_id_values(modifier, keys))
    return tuple(values)


def _string_values(value: object) -> tuple[str, ...]:
    """把字符串、列表或元组中的字符串值规范为 tuple。"""
    if isinstance(value, str):
        return (value.strip(),) if value.strip() else ()
    if isinstance(value, (list, tuple)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return ()


def _stable_values(values) -> tuple[str, ...]:
    """按出现顺序去重并丢弃空字符串。"""
    result: list[str] = []
    for value in values:
        normalized = str(value).strip()
        if normalized and normalized not in result:
            result.append(normalized)
    return tuple(result)


def _default_result_ref(tool_use_id: str) -> str:
    """返回当前还未外置到 evidence 时的默认工具结果句柄。"""
    return f"agent_state_tool_result:{tool_use_id}"


def _impacts_current_decision(
    tool_use_id: str,
    status: str,
    working_set: WorkingSet | None,
) -> bool:
    """判断工具收据是否仍影响当前模型决策。"""
    if status != "success" or working_set is None:
        return True
    return tool_use_id in working_set.last_tool_result_ids or tool_use_id in working_set.pending_tool_use_ids


_TOOL_RECEIPT_TUPLE_FIELDS = frozenset(
    {
        "attachments",
        "context_modifier_types",
        "approval_ids",
        "perception_ids",
        "file_paths",
    }
)


def _self_test() -> None:
    """验证工具结果信封可转换成稳定 Tool Receipt。"""
    result = ToolResultEnvelope(
        "tool_1",
        "sample_tool",
        True,
        '{"task_id":"task_1","file_path":"data/result.md"}',
        attachments=("data/result.md",),
    )
    receipt = ToolReceiptBuilder().from_envelope(result)
    assert receipt.status == "success"
    assert receipt.task_id == "task_1"
    assert receipt.file_paths == ("data/result.md",)


if __name__ == "__main__":
    _self_test()
    print("dutyflow tool receipt self-test passed")
