# 本文件负责定义恢复事件、恢复决策和恢复 scope 的纯内存结构。

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

RECOVERY_SCOPE_TYPES = frozenset({"turn", "tool_call", "task"})
RECOVERY_STATUSES = frozenset({"active", "waiting", "scheduled", "resolved", "exhausted"})
RECOVERY_STRATEGIES = frozenset(
    {"retry_now", "retry_later", "wait_approval", "degrade", "manual_review", "abort"}
)
RECOVERY_FAILURE_KINDS = frozenset(
    {
        "model_transport_error",
        "model_max_tokens",
        "context_overflow",
        "tool_timeout",
        "tool_transient_error",
        "tool_retry_exhausted",
        "tool_side_effect_uncertain",
        "permission_denied",
        "approval_waiting",
        "approval_rejected",
        "feedback_delivery_failed",
        "persistence_write_failed",
    }
)
RECOVERY_INTERRUPTION_REASONS = frozenset(
    {
        "wait_next_retry_window",
        "waiting_approval",
        "waiting_external_callback",
        "waiting_schedule",
        "waiting_manual_review",
        "context_compaction_pending",
        "runtime_restart_pending",
        "user_pause",
    }
)
RECOVERY_RESUME_POINTS = frozenset(
    {
        "before_model_call",
        "before_tool_execute",
        "after_tool_result",
        "after_approval",
        "before_feedback",
    }
)


def _now() -> str:
    """返回 UTC ISO 时间，用于恢复状态更新时间。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class RecoveryEvent:
    """表示一次需要恢复决策的失败或挂起事件。"""

    scope_type: str
    scope_id: str
    failure_kind: str
    attempt_count: int = 0
    max_attempts: int = 0
    error_message: str = ""
    tool_name: str = ""
    retryable: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """校验恢复事件的稳定字段。"""
        if self.scope_type not in RECOVERY_SCOPE_TYPES:
            raise ValueError(f"Unknown RecoveryEvent.scope_type: {self.scope_type}")
        if not self.scope_id:
            raise ValueError("RecoveryEvent.scope_id is required")
        if self.failure_kind not in RECOVERY_FAILURE_KINDS:
            raise ValueError(f"Unknown RecoveryEvent.failure_kind: {self.failure_kind}")
        if self.attempt_count < 0:
            raise ValueError("RecoveryEvent.attempt_count must be >= 0")
        if self.max_attempts < 0:
            raise ValueError("RecoveryEvent.max_attempts must be >= 0")


@dataclass(frozen=True)
class RecoveryDecision:
    """表示恢复层对当前事件给出的稳定策略。"""

    strategy: str
    reason: str
    interruption_reason: str = ""
    resume_point: str = ""
    next_retry_at: str = ""
    should_pause: bool = False

    def __post_init__(self) -> None:
        """校验恢复决策的字段取值。"""
        if self.strategy not in RECOVERY_STRATEGIES:
            raise ValueError(f"Unknown RecoveryDecision.strategy: {self.strategy}")
        if not self.reason:
            raise ValueError("RecoveryDecision.reason is required")
        if self.interruption_reason and self.interruption_reason not in RECOVERY_INTERRUPTION_REASONS:
            raise ValueError(
                "Unknown RecoveryDecision.interruption_reason: " + self.interruption_reason
            )
        if self.resume_point and self.resume_point not in RECOVERY_RESUME_POINTS:
            raise ValueError("Unknown RecoveryDecision.resume_point: " + self.resume_point)


@dataclass(frozen=True)
class RecoveryScope:
    """表示一个可挂起、可 restart、可继续的恢复对象。"""

    recovery_id: str
    scope_type: str
    scope_id: str
    status: str
    failure_kind: str
    interruption_reason: str = ""
    strategy: str = "manual_review"
    attempt_count: int = 0
    max_attempts: int = 0
    next_retry_at: str = ""
    resume_point: str = ""
    resume_payload: Mapping[str, Any] = field(default_factory=dict)
    last_error: str = ""
    updated_at: str = field(default_factory=_now)

    def __post_init__(self) -> None:
        """校验恢复 scope 的稳定字段。"""
        if not self.recovery_id:
            raise ValueError("RecoveryScope.recovery_id is required")
        if self.scope_type not in RECOVERY_SCOPE_TYPES:
            raise ValueError(f"Unknown RecoveryScope.scope_type: {self.scope_type}")
        if not self.scope_id:
            raise ValueError("RecoveryScope.scope_id is required")
        if self.status not in RECOVERY_STATUSES:
            raise ValueError(f"Unknown RecoveryScope.status: {self.status}")
        if self.failure_kind not in RECOVERY_FAILURE_KINDS:
            raise ValueError(f"Unknown RecoveryScope.failure_kind: {self.failure_kind}")
        if self.interruption_reason and self.interruption_reason not in RECOVERY_INTERRUPTION_REASONS:
            raise ValueError(
                "Unknown RecoveryScope.interruption_reason: " + self.interruption_reason
            )
        if self.strategy not in RECOVERY_STRATEGIES:
            raise ValueError(f"Unknown RecoveryScope.strategy: {self.strategy}")
        if self.attempt_count < 0 or self.max_attempts < 0:
            raise ValueError("RecoveryScope attempt fields must be >= 0")
        if self.resume_point and self.resume_point not in RECOVERY_RESUME_POINTS:
            raise ValueError("Unknown RecoveryScope.resume_point: " + self.resume_point)


@dataclass(frozen=True)
class RecoveryRestartDescriptor:
    """表示当前进程内可观察的挂起 / restart 描述。"""

    recovery_id: str
    resume_token: str
    scope_type: str
    scope_id: str
    status: str
    interruption_reason: str
    resume_point: str
    restart_action: str
    can_restart_now: bool
    next_retry_at: str = ""

    def __post_init__(self) -> None:
        """校验 restart 描述中的稳定字段。"""
        if not self.recovery_id:
            raise ValueError("RecoveryRestartDescriptor.recovery_id is required")
        if not self.resume_token:
            raise ValueError("RecoveryRestartDescriptor.resume_token is required")
        if self.scope_type not in RECOVERY_SCOPE_TYPES:
            raise ValueError("Unknown RecoveryRestartDescriptor.scope_type: " + self.scope_type)
        if not self.scope_id:
            raise ValueError("RecoveryRestartDescriptor.scope_id is required")
        if self.status not in {"waiting", "scheduled"}:
            raise ValueError("RecoveryRestartDescriptor.status must be waiting or scheduled")
        if self.interruption_reason and self.interruption_reason not in RECOVERY_INTERRUPTION_REASONS:
            raise ValueError(
                "Unknown RecoveryRestartDescriptor.interruption_reason: " + self.interruption_reason
            )
        if self.resume_point and self.resume_point not in RECOVERY_RESUME_POINTS:
            raise ValueError("Unknown RecoveryRestartDescriptor.resume_point: " + self.resume_point)
        if not self.restart_action:
            raise ValueError("RecoveryRestartDescriptor.restart_action is required")


class RecoveryManager:
    """根据恢复事件产出第一版稳定恢复策略。"""

    def __init__(self, retry_later_delay_seconds: int = 5) -> None:
        """初始化恢复管理器的最小时间窗口配置。"""
        # 关键开关：进入 retry_later 的恢复 scope 默认延后 5 秒，避免当前进程内立即重放。
        self.retry_later_delay_seconds = max(1, retry_later_delay_seconds)

    def decide(self, event: RecoveryEvent) -> RecoveryDecision:
        """为恢复事件生成恢复策略。"""
        if event.failure_kind == "model_max_tokens":
            return RecoveryDecision(
                strategy="retry_now",
                reason="model output was truncated and should continue immediately",
                resume_point="before_model_call",
                should_pause=False,
            )
        if event.failure_kind == "approval_waiting":
            return RecoveryDecision(
                strategy="wait_approval",
                reason="approval is required before the task can continue",
                interruption_reason="waiting_approval",
                resume_point="after_approval",
                should_pause=True,
            )
        if event.failure_kind in {"approval_rejected", "permission_denied"}:
            return RecoveryDecision(
                strategy="abort",
                reason="permission flow rejected further execution",
                interruption_reason="waiting_manual_review",
                resume_point="before_feedback",
                should_pause=True,
            )
        if event.failure_kind in {"tool_retry_exhausted", "tool_side_effect_uncertain"}:
            return RecoveryDecision(
                strategy="manual_review",
                reason="tool can no longer continue safely without human review",
                interruption_reason="waiting_manual_review",
                resume_point="before_feedback",
                should_pause=True,
            )
        if event.failure_kind == "context_overflow":
            return RecoveryDecision(
                strategy="degrade",
                reason="context should be compacted before retrying",
                interruption_reason="context_compaction_pending",
                resume_point="before_model_call",
                should_pause=True,
            )
        if self._should_retry_later(event):
            return RecoveryDecision(
                strategy="retry_later",
                reason="retryable failure can be retried in a later window",
                interruption_reason="wait_next_retry_window",
                resume_point=self._resume_point_for_event(event),
                should_pause=True,
            )
        return RecoveryDecision(
            strategy="retry_now",
            reason="current event can continue immediately in-process",
            resume_point=self._resume_point_for_event(event),
            should_pause=False,
        )

    def create_scope(
        self,
        recovery_id: str,
        event: RecoveryEvent,
        decision: RecoveryDecision,
        next_retry_at: str = "",
    ) -> RecoveryScope:
        """根据恢复事件和决策构造可回写的 RecoveryScope。"""
        status = self._status_for_decision(decision)
        return RecoveryScope(
            recovery_id=recovery_id,
            scope_type=event.scope_type,
            scope_id=event.scope_id,
            status=status,
            failure_kind=event.failure_kind,
            interruption_reason=decision.interruption_reason,
            strategy=decision.strategy,
            attempt_count=event.attempt_count,
            max_attempts=event.max_attempts,
            next_retry_at=next_retry_at or decision.next_retry_at or self._default_next_retry_at(decision),
            resume_point=decision.resume_point,
            resume_payload=dict(event.metadata),
            last_error=event.error_message,
        )

    def describe_restart(self, scope: RecoveryScope) -> RecoveryRestartDescriptor | None:
        """把挂起中的恢复 scope 转换为当前进程内的 restart 描述。"""
        if scope.status not in {"waiting", "scheduled"}:
            return None
        return RecoveryRestartDescriptor(
            recovery_id=scope.recovery_id,
            resume_token=self.resume_token(scope),
            scope_type=scope.scope_type,
            scope_id=scope.scope_id,
            status=scope.status,
            interruption_reason=scope.interruption_reason,
            resume_point=scope.resume_point,
            restart_action=self._restart_action(scope),
            can_restart_now=self._can_restart_now(scope),
            next_retry_at=scope.next_retry_at,
        )

    def collect_restart_descriptions(
        self,
        scopes: Sequence[RecoveryScope],
    ) -> tuple[RecoveryRestartDescriptor, ...]:
        """收集所有当前进程内可观察的挂起 / restart 描述。"""
        descriptions: list[RecoveryRestartDescriptor] = []
        for scope in scopes:
            description = self.describe_restart(scope)
            if description is not None:
                descriptions.append(description)
        return tuple(descriptions)

    def resolve_resume_token(
        self,
        scopes: Sequence[RecoveryScope],
        resume_token: str,
    ) -> RecoveryScope | None:
        """根据当前进程内的 resume_token 查找对应恢复 scope。"""
        for scope in scopes:
            if self.resume_token(scope) == resume_token:
                return scope
        return None

    def resume_token(self, scope: RecoveryScope) -> str:
        """返回当前进程内用于 restart 关联的稳定 token。"""
        return "resume_" + scope.recovery_id

    def _should_retry_later(self, event: RecoveryEvent) -> bool:
        """判断当前事件是否应该进入稍后重试。"""
        if event.failure_kind not in {"model_transport_error", "tool_timeout", "tool_transient_error"}:
            return False
        if not event.retryable:
            return False
        if event.max_attempts <= 0:
            return False
        return event.attempt_count >= event.max_attempts

    def _resume_point_for_event(self, event: RecoveryEvent) -> str:
        """根据事件类型返回默认恢复点。"""
        if event.scope_type == "turn":
            return "before_model_call"
        if event.scope_type == "tool_call":
            return "before_tool_execute"
        return "before_feedback"

    def _status_for_decision(self, decision: RecoveryDecision) -> str:
        """根据恢复决策推导恢复 scope 的初始状态。"""
        if decision.strategy == "retry_later":
            return "scheduled"
        if decision.should_pause:
            return "waiting"
        return "active"

    def _default_next_retry_at(self, decision: RecoveryDecision) -> str:
        """为 retry_later 生成当前进程内的最小下一次重启时间。"""
        if decision.strategy != "retry_later":
            return ""
        return (datetime.now(timezone.utc) + timedelta(seconds=self.retry_later_delay_seconds)).isoformat(
            timespec="seconds"
        )

    def _restart_action(self, scope: RecoveryScope) -> str:
        """根据恢复点和失败类型生成当前 restart 描述动作。"""
        if scope.resume_point == "after_approval":
            return "resume_after_approval"
        if scope.failure_kind == "context_overflow":
            return "compact_then_retry"
        if scope.resume_point == "before_tool_execute":
            return "restart_tool_call"
        if scope.resume_point == "before_model_call":
            return "restart_model_call"
        return "resume_before_feedback"

    def _can_restart_now(self, scope: RecoveryScope) -> bool:
        """判断当前进程内该恢复 scope 是否已到可 restart 时机。"""
        if scope.status != "scheduled":
            return False
        if not scope.next_retry_at:
            return True
        try:
            scheduled_at = datetime.fromisoformat(scope.next_retry_at)
        except ValueError:
            return False
        now = datetime.now(timezone.utc)
        if scheduled_at.tzinfo is None:
            scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)
        return scheduled_at <= now


def _self_test() -> None:
    """验证 approval_waiting 会进入 wait_approval。"""
    event = RecoveryEvent(
        scope_type="tool_call",
        scope_id="tool_1",
        failure_kind="approval_waiting",
    )
    decision = RecoveryManager().decide(event)
    assert decision.strategy == "wait_approval"
    scope = RecoveryManager().create_scope("rec_1", event, decision)
    assert scope.status == "waiting"
    description = RecoveryManager().describe_restart(scope)
    assert description is not None
    assert description.restart_action == "resume_after_approval"


if __name__ == "__main__":
    _self_test()
    print("dutyflow recovery self-test passed")
