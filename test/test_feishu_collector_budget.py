# 本文件验证飞书用户面 collector 预算控制的页数、条数、正文裁剪和重试退避。

from __future__ import annotations

from pathlib import Path
import sys
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dutyflow.feishu.collector_budget import (  # noqa: E402
    CollectorBudget,
    CollectorBudgetGuard,
    CollectorBudgetUsage,
)


class TestCollectorBudgetGuard(unittest.TestCase):
    """验证 collector 预算 guard 的核心行为。"""

    def test_page_limit_stops_next_page(self) -> None:
        budget = CollectorBudget(
            collector_name="page_test",
            max_pages_per_run=2,
        )
        guard = CollectorBudgetGuard(budget)

        self.assertTrue(guard.record_page())
        self.assertTrue(guard.record_page())
        self.assertFalse(guard.can_request_next_page())

        snapshot = guard.snapshot()
        self.assertEqual(snapshot.pages_used, 2)
        self.assertEqual(snapshot.stopped_reason, "max_pages_per_run")

    def test_item_limit_stops_next_item(self) -> None:
        budget = CollectorBudget(
            collector_name="item_test",
            max_items_per_run=1,
        )
        guard = CollectorBudgetGuard(budget)

        self.assertTrue(guard.record_item())
        self.assertFalse(guard.can_accept_item())
        self.assertFalse(guard.record_item())

        snapshot = guard.snapshot()
        self.assertEqual(snapshot.items_used, 1)
        self.assertEqual(snapshot.stopped_reason, "max_items_per_run")

    def test_trim_content_respects_single_resource_limit(self) -> None:
        budget = CollectorBudget(
            collector_name="content_test",
            max_content_chars=5,
        )
        guard = CollectorBudgetGuard(budget)

        trimmed = guard.trim_content("abcdefg")

        self.assertEqual(trimmed, "abcde")
        self.assertEqual(guard.snapshot().content_chars_used, 5)

    def test_backoff_grows_and_caps(self) -> None:
        budget = CollectorBudget(
            collector_name="backoff_test",
            max_retries=5,
            base_backoff_seconds=2.0,
            max_backoff_seconds=10.0,
        )
        guard = CollectorBudgetGuard(budget)

        self.assertEqual(guard.backoff_seconds_for_failure("timeout", 1), 2.0)
        self.assertEqual(guard.backoff_seconds_for_failure("timeout", 2), 4.0)
        self.assertEqual(guard.backoff_seconds_for_failure("timeout", 3), 8.0)
        self.assertEqual(guard.backoff_seconds_for_failure("timeout", 4), 10.0)

    def test_permission_error_does_not_retry(self) -> None:
        budget = CollectorBudget(
            collector_name="permission_test",
            max_retries=3,
        )
        guard = CollectorBudgetGuard(budget)

        self.assertFalse(guard.should_retry_failure("permission_denied", 1))
        self.assertEqual(
            guard.backoff_seconds_for_failure("permission_denied", 1),
            0.0,
        )

    def test_retry_stops_after_max_retries(self) -> None:
        budget = CollectorBudget(
            collector_name="retry_test",
            max_retries=2,
        )
        guard = CollectorBudgetGuard(budget)

        self.assertTrue(guard.should_retry_failure("transient_error", 1))
        self.assertTrue(guard.should_retry_failure("transient_error", 2))
        self.assertFalse(guard.should_retry_failure("transient_error", 3))

    def test_initial_usage_is_respected(self) -> None:
        budget = CollectorBudget(
            collector_name="usage_test",
            max_pages_per_run=2,
        )
        usage = CollectorBudgetUsage(pages_used=2)
        guard = CollectorBudgetGuard(budget, usage)

        self.assertFalse(guard.can_request_next_page())
        self.assertEqual(guard.snapshot().stopped_reason, "max_pages_per_run")


def _self_test() -> None:
    """运行本文件所有单元测试。"""
    unittest.main(verbosity=2)


if __name__ == "__main__":
    _self_test()
