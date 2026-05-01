# 本文件负责对模型可见 messages 做轻量上下文预算估算。

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from math import ceil

from dutyflow.agent.state import AgentContentBlock, AgentMessage


# 关键估算：非 CJK 文本按约 4 字符 1 token 粗估，服务可视化而非计费。
ASCII_CHARS_PER_TOKEN = 4
# 关键估算：每条 provider message 叠加 4 token 结构开销。
MESSAGE_OVERHEAD_TOKENS = 4
# 关键估算：每个 content block 叠加 2 token 结构开销。
BLOCK_OVERHEAD_TOKENS = 2
# 关键开关：预算报告默认保留 token 最高的 5 个条目用于定位上下文膨胀来源。
DEFAULT_LARGEST_ITEM_LIMIT = 5
# 关键开关：调试预览最多保留 120 字，避免预算报告本身膨胀。
PREVIEW_MAX_CHARS = 120
ESTIMATOR_VERSION = "heuristic_cjk_v1"
CONTEXT_BUDGET_LANES = frozenset(
    {
        "system_instructions",
        "latest_user_input",
        "active_tool_result",
        "tool_receipt",
        "assistant_context",
        "history",
        "unknown",
    }
)


@dataclass(frozen=True)
class ContextBudgetItem:
    """表示单个 message/block 对模型上下文的估算占用。"""

    message_index: int
    block_index: int
    role: str
    block_type: str
    lane: str
    tool_use_id: str
    tool_name: str
    estimated_tokens: int
    chars: int
    preview: str

    def to_dict(self) -> dict[str, object]:
        """返回便于测试、日志和后续可视化消费的字典。"""
        return asdict(self)


@dataclass(frozen=True)
class ContextBudgetLaneUsage:
    """表示某个上下文 lane 的聚合估算占用。"""

    lane: str
    estimated_tokens: int
    chars: int
    message_count: int
    block_count: int

    def to_dict(self) -> dict[str, object]:
        """返回稳定字典。"""
        return asdict(self)


@dataclass(frozen=True)
class ContextBudgetReport:
    """表示一次模型可见 messages 的上下文预算报告。"""

    total_estimated_tokens: int
    total_chars: int
    message_count: int
    block_count: int
    lane_usages: tuple[ContextBudgetLaneUsage, ...]
    largest_items: tuple[ContextBudgetItem, ...]
    estimator_version: str = ESTIMATOR_VERSION

    def to_dict(self) -> dict[str, object]:
        """返回可序列化预算报告。"""
        return {
            "total_estimated_tokens": self.total_estimated_tokens,
            "total_chars": self.total_chars,
            "message_count": self.message_count,
            "block_count": self.block_count,
            "lane_usages": [item.to_dict() for item in self.lane_usages],
            "largest_items": [item.to_dict() for item in self.largest_items],
            "estimator_version": self.estimator_version,
        }


class ContextBudgetEstimator:
    """对投影后的 AgentMessage 序列做确定性 token 预算估算。"""

    def __init__(self, largest_item_limit: int = DEFAULT_LARGEST_ITEM_LIMIT) -> None:
        """设置报告中保留的大条目数量。"""
        if largest_item_limit < 1:
            raise ValueError("largest_item_limit must be >= 1")
        self.largest_item_limit = largest_item_limit

    def estimate_messages(self, messages: tuple[AgentMessage, ...]) -> ContextBudgetReport:
        """估算一组模型可见 messages 的 token 占用。"""
        latest_user_text_index = _latest_user_text_message_index(messages)
        items = _budget_items(messages, latest_user_text_index)
        return ContextBudgetReport(
            total_estimated_tokens=sum(item.estimated_tokens for item in items),
            total_chars=sum(item.chars for item in items),
            message_count=len(messages),
            block_count=len(items),
            lane_usages=_lane_usages(items),
            largest_items=_largest_items(items, self.largest_item_limit),
        )


def estimate_text_tokens(text: str) -> int:
    """按 CJK 和非 CJK 字符比例做轻量 token 粗估。"""
    cjk_count = sum(1 for char in str(text) if _is_cjk_char(char))
    other_count = sum(1 for char in str(text) if not _is_cjk_char(char) and not char.isspace())
    if cjk_count == 0 and other_count == 0:
        return 0
    return cjk_count + ceil(other_count / ASCII_CHARS_PER_TOKEN)


def _budget_items(
    messages: tuple[AgentMessage, ...],
    latest_user_text_index: int,
) -> tuple[ContextBudgetItem, ...]:
    """把 messages 展开为可估算的预算条目。"""
    items: list[ContextBudgetItem] = []
    for message_index, message in enumerate(messages):
        for block_index, block in enumerate(message.content):
            text = _block_visible_text(block)
            lane = _classify_lane(message, block, message_index, latest_user_text_index)
            message_overhead = MESSAGE_OVERHEAD_TOKENS if block_index == 0 else 0
            estimated = estimate_text_tokens(text) + message_overhead + BLOCK_OVERHEAD_TOKENS
            items.append(_build_item(message_index, block_index, message, block, lane, text, estimated))
    return tuple(items)


def _build_item(
    message_index: int,
    block_index: int,
    message: AgentMessage,
    block: AgentContentBlock,
    lane: str,
    text: str,
    estimated_tokens: int,
) -> ContextBudgetItem:
    """构造单个预算条目。"""
    return ContextBudgetItem(
        message_index=message_index,
        block_index=block_index,
        role=message.role,
        block_type=block.type,
        lane=lane,
        tool_use_id=block.tool_use_id,
        tool_name=block.tool_name,
        estimated_tokens=estimated_tokens,
        chars=len(text),
        preview=_preview(text),
    )


def _lane_usages(items: tuple[ContextBudgetItem, ...]) -> tuple[ContextBudgetLaneUsage, ...]:
    """按 lane 聚合预算条目。"""
    usages: list[ContextBudgetLaneUsage] = []
    for lane in _ordered_lanes(items):
        lane_items = tuple(item for item in items if item.lane == lane)
        usages.append(_lane_usage(lane, lane_items))
    return tuple(usages)


def _lane_usage(lane: str, items: tuple[ContextBudgetItem, ...]) -> ContextBudgetLaneUsage:
    """生成单个 lane 的聚合记录。"""
    return ContextBudgetLaneUsage(
        lane=lane,
        estimated_tokens=sum(item.estimated_tokens for item in items),
        chars=sum(item.chars for item in items),
        message_count=len({item.message_index for item in items}),
        block_count=len(items),
    )


def _ordered_lanes(items: tuple[ContextBudgetItem, ...]) -> tuple[str, ...]:
    """按固定优先级输出已有 lane，方便可视化稳定展示。"""
    order = (
        "system_instructions",
        "latest_user_input",
        "active_tool_result",
        "tool_receipt",
        "assistant_context",
        "history",
        "unknown",
    )
    present = {item.lane for item in items}
    return tuple(lane for lane in order if lane in present)


def _largest_items(
    items: tuple[ContextBudgetItem, ...],
    limit: int,
) -> tuple[ContextBudgetItem, ...]:
    """返回估算 token 最高的若干条目。"""
    ranked = sorted(items, key=lambda item: (-item.estimated_tokens, item.message_index, item.block_index))
    return tuple(ranked[:limit])


def _latest_user_text_message_index(messages: tuple[AgentMessage, ...]) -> int:
    """返回最近一条普通用户文本消息的序号。"""
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if message.role == "user" and any(block.type == "text" and block.text for block in message.content):
            return index
    return -1


def _classify_lane(
    message: AgentMessage,
    block: AgentContentBlock,
    message_index: int,
    latest_user_text_index: int,
) -> str:
    """按角色和 block 类型把内容归入预算 lane。"""
    if message.role == "system":
        return "system_instructions"
    if message.role == "assistant":
        return "assistant_context"
    if block.type == "tool_result":
        return "tool_receipt" if _is_tool_receipt_text(block.content) else "active_tool_result"
    if message.role == "user" and message_index == latest_user_text_index:
        return "latest_user_input"
    if message.role == "user":
        return "history"
    return "unknown"


def _block_visible_text(block: AgentContentBlock) -> str:
    """返回一个 block 在模型上下文里的主要可见文本。"""
    if block.type == "text":
        return block.text
    if block.type == "tool_result":
        return block.content
    if block.type == "tool_use":
        return _tool_use_text(block)
    return block.text or block.content


def _tool_use_text(block: AgentContentBlock) -> str:
    """把工具调用的名称和入参渲染成稳定估算文本。"""
    payload = json.dumps(dict(block.tool_input), ensure_ascii=False, sort_keys=True)
    return f"{block.tool_name} {block.tool_use_id} {payload}"


def _preview(text: str) -> str:
    """生成单行短预览。"""
    normalized = " ".join(str(text).split())
    if len(normalized) <= PREVIEW_MAX_CHARS:
        return normalized
    return normalized[: PREVIEW_MAX_CHARS - 3] + "..."


def _is_tool_receipt_text(content: str) -> bool:
    """判断工具结果内容是否已经是 Tool Receipt。"""
    return str(content).strip().startswith("ToolReceipt(")


def _is_cjk_char(char: str) -> bool:
    """判断字符是否属于常见 CJK 区间。"""
    code = ord(char)
    return (
        0x4E00 <= code <= 0x9FFF
        or 0x3400 <= code <= 0x4DBF
        or 0x3040 <= code <= 0x30FF
        or 0xAC00 <= code <= 0xD7AF
    )


def _self_test() -> None:
    """验证预算估算可输出总量和 lane 聚合。"""
    messages = (
        AgentMessage("system", (AgentContentBlock(type="text", text="system rule"),)),
        AgentMessage("user", (AgentContentBlock(type="text", text="请处理任务"),)),
        AgentMessage(
            "user",
            (
                AgentContentBlock(
                    type="tool_result",
                    tool_use_id="tool_1",
                    tool_name="sample_tool",
                    content="ToolReceipt(tool=sample_tool, tool_use_id=tool_1, status=success, summary=ok, ref=x)",
                ),
            ),
        ),
    )
    report = ContextBudgetEstimator().estimate_messages(messages)
    assert report.total_estimated_tokens > 0
    assert any(item.lane == "tool_receipt" for item in report.lane_usages)


if __name__ == "__main__":
    _self_test()
    print("dutyflow context budget self-test passed")
