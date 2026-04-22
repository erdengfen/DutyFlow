# 本文件负责定义 Hook 事件、返回结果和最小 HookRunner 预留接口。

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

HOOK_EVENT_NAMES = frozenset({"SessionStart", "PreToolUse", "PostToolUse"})
HOOK_EXIT_CODES = frozenset({0, 1, 2})


@dataclass(frozen=True)
class HookEvent:
    """表示主链路在固定时机暴露给侧车扩展的标准事件。"""

    name: str
    payload: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """校验 Hook 事件名和最小 payload 结构。"""
        if self.name not in HOOK_EVENT_NAMES:
            raise ValueError(f"Unknown hook event: {self.name}")
        if not isinstance(self.payload, Mapping):
            raise ValueError("HookEvent.payload must be a mapping")


@dataclass(frozen=True)
class HookResult:
    """表示 HookRunner 对单次事件处理的统一返回约定。"""

    exit_code: int = 0
    message: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """校验退出码只允许使用教学版统一约定。"""
        if self.exit_code not in HOOK_EXIT_CODES:
            raise ValueError(f"Unknown hook exit_code: {self.exit_code}")


HookHandler = Callable[[HookEvent], HookResult]


class HookRunner:
    """统一管理 Hook 事件与处理器映射，但当前不接入 AgentLoop。"""

    def __init__(self, hooks: Mapping[str, tuple[HookHandler, ...]] | None = None) -> None:
        """初始化事件到处理器列表的映射。"""
        self._hooks: dict[str, tuple[HookHandler, ...]] = {name: () for name in HOOK_EVENT_NAMES}
        if hooks is None:
            return
        for event_name, handlers in hooks.items():
            for handler in handlers:
                self.register(event_name, handler)

    def register(self, event_name: str, handler: HookHandler) -> None:
        """向指定事件注册一个 Hook 处理器。"""
        if event_name not in HOOK_EVENT_NAMES:
            raise ValueError(f"Unknown hook event: {event_name}")
        self._hooks[event_name] = self._hooks[event_name] + (handler,)

    def handlers_for(self, event_name: str) -> tuple[HookHandler, ...]:
        """返回指定事件当前已注册的处理器列表。"""
        if event_name not in HOOK_EVENT_NAMES:
            raise ValueError(f"Unknown hook event: {event_name}")
        return self._hooks[event_name]

    def run(self, event_name: str, payload: Mapping[str, Any] | None = None) -> HookResult:
        """按事件名执行 Hook，并返回统一结果。"""
        return self.run_event(HookEvent(name=event_name, payload=payload or {}))

    def run_event(self, event: HookEvent) -> HookResult:
        """执行单次 Hook 事件，遇到 block / inject 结果时立即返回。"""
        result = HookResult()
        for handler in self.handlers_for(event.name):
            result = handler(event)
            if result.exit_code in {1, 2}:
                return result
        return result


def _allow_handler(event: HookEvent) -> HookResult:
    """为自测提供最小 continue handler。"""
    return HookResult(exit_code=0, metadata={"event": event.name})


def _inject_handler(event: HookEvent) -> HookResult:
    """为自测提供最小 inject handler。"""
    return HookResult(exit_code=2, message="inject:" + event.name)


def _self_test() -> None:
    """验证 HookRunner 的最小注册与短路行为。"""
    runner = HookRunner()
    runner.register("SessionStart", _allow_handler)
    runner.register("SessionStart", _inject_handler)
    result = runner.run("SessionStart", {"query_id": "query_001"})
    assert result.exit_code == 2
    assert result.message == "inject:SessionStart"


if __name__ == "__main__":
    _self_test()
    print("dutyflow hook runner self-test passed")
