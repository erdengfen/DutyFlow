# 本文件负责飞书用户面 collector 的单轮预算控制，限制页数、条数、正文大小和失败退避。

from __future__ import annotations

from dataclasses import dataclass, replace

# 关键开关：单个 collector 每轮默认最多请求 3 页，避免主动感知误触发大规模全量拉取。
DEFAULT_MAX_PAGES_PER_RUN = 3
# 关键开关：单个 collector 每轮默认最多处理 50 条，限制一次同步对本地和飞书 API 的压力。
DEFAULT_MAX_ITEMS_PER_RUN = 50
# 关键开关：单个资源正文默认最多读取 20000 字符，避免大文档撑爆后续上下文。
DEFAULT_MAX_CONTENT_CHARS = 20000
# 关键开关：单次飞书用户面请求默认超时 15 秒，避免 collector 长时间阻塞。
DEFAULT_REQUEST_TIMEOUT_SECONDS = 15.0
# 关键开关：瞬时失败默认最多重试 2 次，权限错误和认证错误不走普通重试。
DEFAULT_MAX_RETRIES = 2
# 关键开关：失败后默认从 2 秒开始退避，降低连续请求失败时的 API 压力。
DEFAULT_BASE_BACKOFF_SECONDS = 2.0
# 关键开关：失败退避默认最多 60 秒，防止单轮同步被过长 sleep 拖住。
DEFAULT_MAX_BACKOFF_SECONDS = 60.0

_NON_RETRYABLE_STATUSES = {
    "permission_denied",
    "not_found",
    "token_missing",
    "reauth_required",
}
_RETRYABLE_STATUSES = {
    "timeout",
    "transient_error",
    "rate_limited",
    "api_error",
}


@dataclass(frozen=True)
class CollectorBudget:
    """描述单个 collector 单轮同步的预算边界。"""

    collector_name: str
    max_pages_per_run: int = DEFAULT_MAX_PAGES_PER_RUN
    max_items_per_run: int = DEFAULT_MAX_ITEMS_PER_RUN
    max_content_chars: int = DEFAULT_MAX_CONTENT_CHARS
    request_timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_SECONDS
    max_retries: int = DEFAULT_MAX_RETRIES
    base_backoff_seconds: float = DEFAULT_BASE_BACKOFF_SECONDS
    max_backoff_seconds: float = DEFAULT_MAX_BACKOFF_SECONDS

    def __post_init__(self) -> None:
        """校验预算字段，避免 collector 带着无效上限运行。"""
        if not self.collector_name:
            raise ValueError("CollectorBudget.collector_name is required")
        _require_positive_int("max_pages_per_run", self.max_pages_per_run)
        _require_positive_int("max_items_per_run", self.max_items_per_run)
        _require_positive_int("max_content_chars", self.max_content_chars)
        _require_positive_float("request_timeout_seconds", self.request_timeout_seconds)
        _require_non_negative_int("max_retries", self.max_retries)
        _require_positive_float("base_backoff_seconds", self.base_backoff_seconds)
        _require_positive_float("max_backoff_seconds", self.max_backoff_seconds)


@dataclass(frozen=True)
class CollectorBudgetUsage:
    """记录 collector 当前轮次已经消耗的预算。"""

    pages_used: int = 0
    items_used: int = 0
    content_chars_used: int = 0
    stopped_reason: str = ""


class CollectorBudgetGuard:
    """围绕 CollectorBudget 提供运行时预算判断和用量累计。"""

    def __init__(
        self,
        budget: CollectorBudget,
        usage: CollectorBudgetUsage | None = None,
    ) -> None:
        """绑定预算和可选初始用量。"""
        self.budget = budget
        self.usage = usage or CollectorBudgetUsage()

    def can_request_next_page(self) -> bool:
        """判断当前轮次是否还能继续请求下一页。"""
        if self.usage.pages_used < self.budget.max_pages_per_run:
            return True
        self._stop_once("max_pages_per_run")
        return False

    def record_page(self) -> bool:
        """记录已经请求一页；超过预算时返回 False。"""
        if not self.can_request_next_page():
            return False
        self.usage = replace(self.usage, pages_used=self.usage.pages_used + 1)
        return True

    def can_accept_item(self) -> bool:
        """判断当前轮次是否还能继续处理下一条资源。"""
        if self.usage.items_used < self.budget.max_items_per_run:
            return True
        self._stop_once("max_items_per_run")
        return False

    def record_item(self) -> bool:
        """记录已经处理一条资源；超过预算时返回 False。"""
        if not self.can_accept_item():
            return False
        self.usage = replace(self.usage, items_used=self.usage.items_used + 1)
        return True

    def trim_content(self, content: str) -> str:
        """按单资源正文上限裁剪内容，并累计实际保留字符数。"""
        trimmed = content[: self.budget.max_content_chars]
        next_chars = self.usage.content_chars_used + len(trimmed)
        self.usage = replace(self.usage, content_chars_used=next_chars)
        return trimmed

    def should_retry_failure(self, status: str, failure_count: int) -> bool:
        """判断指定失败是否还能进入普通重试路径。"""
        if status in _NON_RETRYABLE_STATUSES:
            return False
        if status not in _RETRYABLE_STATUSES:
            return False
        if failure_count <= 0:
            return False
        return failure_count <= self.budget.max_retries

    def backoff_seconds_for_failure(self, status: str, failure_count: int) -> float:
        """返回失败后的退避秒数；不可重试失败返回 0。"""
        if not self.should_retry_failure(status, failure_count):
            return 0.0
        exponent = max(0, failure_count - 1)
        backoff = self.budget.base_backoff_seconds * (2**exponent)
        return min(backoff, self.budget.max_backoff_seconds)

    def snapshot(self) -> CollectorBudgetUsage:
        """返回当前用量快照，避免调用方直接依赖内部对象可变性。"""
        return replace(self.usage)

    def _stop_once(self, reason: str) -> None:
        """只在首次触达预算边界时记录停止原因。"""
        if not self.usage.stopped_reason:
            self.usage = replace(self.usage, stopped_reason=reason)


def _require_positive_int(name: str, value: int) -> None:
    """校验正整数预算字段。"""
    if not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")


def _require_non_negative_int(name: str, value: int) -> None:
    """校验非负整数预算字段。"""
    if not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")


def _require_positive_float(name: str, value: float) -> None:
    """校验正数秒数预算字段。"""
    if not isinstance(value, (int, float)) or value <= 0:
        raise ValueError(f"{name} must be a positive number")


def _self_test() -> None:
    """验证页数上限、正文裁剪和权限错误不重试。"""
    budget = CollectorBudget(
        collector_name="self_test",
        max_pages_per_run=1,
        max_items_per_run=1,
        max_content_chars=3,
    )
    guard = CollectorBudgetGuard(budget)
    assert guard.record_page()
    assert not guard.can_request_next_page()
    assert guard.trim_content("abcdef") == "abc"
    assert not guard.should_retry_failure("permission_denied", 1)


if __name__ == "__main__":
    _self_test()
    print("dutyflow feishu collector budget self-test passed")
