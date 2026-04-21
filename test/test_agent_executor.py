# 本文件验证 ToolExecutor 的校验、分批、并发、错误封装和结果回写。

from pathlib import Path
import sys
import threading
import time
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dutyflow.agent.state import (  # noqa: E402
    AgentContentBlock,
    AgentTaskControl,
    append_assistant_message,
    append_tool_results,
    create_initial_agent_state,
)
from dutyflow.agent.tools import ToolCall, ToolResultEnvelope, ToolSpec  # noqa: E402
from dutyflow.agent.tools.context import ToolUseContext  # noqa: E402
from dutyflow.agent.tools.executor import ToolExecutor  # noqa: E402
from dutyflow.agent.tools.registry import ToolRegistry  # noqa: E402
from dutyflow.agent.tools.router import ToolRoute, ToolRouter  # noqa: E402


class TestAgentExecutor(unittest.TestCase):
    """验证工具执行层的运行时约束。"""

    def test_echo_text_uses_shared_tool_content(self) -> None:
        """handler 应通过 ToolUseContext 读取显式共享 tool_content。"""
        registry = _registry()
        call = ToolCall("tool_1", "echo_text", {"text": "hello"}, 0, 0)
        result = _execute(registry, (call,), {"prefix": "ctx:"})[0]
        self.assertTrue(result.ok)
        self.assertEqual(result.content, "ctx:hello")

    def test_handler_exception_is_wrapped(self) -> None:
        """handler 异常必须封装为 error envelope。"""
        registry = _registry()
        call = ToolCall("tool_1", "fail_tool", {}, 0, 0)
        result = _execute(registry, (call,))[0]
        self.assertFalse(result.ok)
        self.assertEqual(result.error_kind, "handler_exception")

    def test_timeout_is_wrapped(self) -> None:
        """超时工具应返回统一 timeout 错误信封。"""
        registry = _registry()
        call = ToolCall("tool_1", "timeout_tool", {"text": "x"}, 0, 0)
        executor = _TestExecutor(registry, max_workers=4)
        result = _execute(registry, (call,), executor=executor)[0]
        self.assertFalse(result.ok)
        self.assertEqual(result.error_kind, "tool_timeout")
        self.assertEqual(result.attempt_count, 4)
        self.assertTrue(result.retryable)
        self.assertTrue(result.retry_exhausted)
        self.assertEqual(len(executor.retry_delays), 3)

    def test_retryable_error_retries_until_success(self) -> None:
        """可重试错误应在统一重试循环内重试并最终成功。"""
        attempts: list[int] = []

        def flaky_handler(tool_call, tool_use_context) -> ToolResultEnvelope:
            attempts.append(1)
            if len(attempts) < 3:
                raise ConnectionError("temporary down")
            return ToolResultEnvelope(tool_call.tool_use_id, tool_call.tool_name, True, "ok")

        registry = ToolRegistry()
        registry.register(_spec("flaky_tool", True), flaky_handler)
        call = ToolCall("tool_1", "flaky_tool", {"text": "x"}, 0, 0)
        executor = _TestExecutor(registry, max_workers=4)
        result = _execute(registry, (call,), executor=executor)[0]
        self.assertTrue(result.ok)
        self.assertEqual(result.content, "ok")
        self.assertEqual(len(attempts), 3)
        self.assertEqual(result.attempt_count, 3)
        self.assertFalse(result.retry_exhausted)
        self.assertEqual(len(executor.retry_delays), 2)
        self.assertLess(executor.retry_delays[0], executor.retry_delays[1])

    def test_invalid_input_does_not_retry(self) -> None:
        """确定性错误不应进入重试循环。"""
        attempts: list[int] = []

        def invalid_handler(tool_call, tool_use_context) -> ToolResultEnvelope:
            attempts.append(1)
            return ToolResultEnvelope(tool_call.tool_use_id, tool_call.tool_name, True, "ok")

        registry = ToolRegistry()
        registry.register(_spec("strict_tool", True), invalid_handler)
        call = ToolCall("tool_1", "strict_tool", {}, 0, 0)
        result = _execute(registry, (call,))[0]
        self.assertFalse(result.ok)
        self.assertEqual(result.error_kind, "invalid_input")
        self.assertEqual(len(attempts), 0)

    def test_non_retryable_handler_exception_returns_immediately(self) -> None:
        """不可重试异常应直接返回，不重复执行。"""
        attempts: list[int] = []

        def bad_handler(tool_call, tool_use_context) -> ToolResultEnvelope:
            attempts.append(1)
            raise RuntimeError("permanent failure")

        registry = ToolRegistry()
        registry.register(_spec("bad_tool", True), bad_handler)
        call = ToolCall("tool_1", "bad_tool", {"text": "x"}, 0, 0)
        result = _execute(registry, (call,))[0]
        self.assertFalse(result.ok)
        self.assertEqual(result.error_kind, "handler_exception")
        self.assertEqual(len(attempts), 1)

    def test_retry_policy_none_blocks_retry(self) -> None:
        """retry_policy=none 的工具即使是暂时性错误也不重试。"""
        attempts: list[int] = []

        def transient_handler(tool_call, tool_use_context) -> ToolResultEnvelope:
            attempts.append(1)
            raise ConnectionError("temporary down")

        registry = ToolRegistry()
        registry.register(
            _spec("none_retry_tool", True, retry_policy="none", idempotency="read_only"),
            transient_handler,
        )
        call = ToolCall("tool_1", "none_retry_tool", {"text": "x"}, 0, 0)
        executor = _TestExecutor(registry, max_workers=4)
        result = _execute(registry, (call,), executor=executor)[0]
        self.assertFalse(result.ok)
        self.assertEqual(result.error_kind, "temporary_transport_error")
        self.assertEqual(result.attempt_count, 1)
        self.assertEqual(len(attempts), 1)
        self.assertEqual(executor.retry_delays, [])

    def test_unsafe_idempotency_blocks_retry(self) -> None:
        """idempotency=unsafe 的工具即使是暂时性错误也不重试。"""
        attempts: list[int] = []

        def transient_handler(tool_call, tool_use_context) -> ToolResultEnvelope:
            attempts.append(1)
            raise ConnectionError("temporary down")

        registry = ToolRegistry()
        registry.register(
            _spec("unsafe_tool", True, retry_policy="transient_only", idempotency="unsafe"),
            transient_handler,
        )
        call = ToolCall("tool_1", "unsafe_tool", {"text": "x"}, 0, 0)
        executor = _TestExecutor(registry, max_workers=4)
        result = _execute(registry, (call,), executor=executor)[0]
        self.assertFalse(result.ok)
        self.assertEqual(result.error_kind, "temporary_transport_error")
        self.assertEqual(result.attempt_count, 1)
        self.assertEqual(len(attempts), 1)
        self.assertEqual(executor.retry_delays, [])

    def test_tool_specific_max_retries_is_honored(self) -> None:
        """工具声明的 max_retries 应覆盖执行层默认重试预算。"""
        attempts: list[int] = []

        def transient_handler(tool_call, tool_use_context) -> ToolResultEnvelope:
            attempts.append(1)
            raise ConnectionError("temporary down")

        registry = ToolRegistry()
        registry.register(
            _spec("single_retry_tool", True, max_retries=1, retry_policy="transient_only"),
            transient_handler,
        )
        call = ToolCall("tool_1", "single_retry_tool", {"text": "x"}, 0, 0)
        executor = _TestExecutor(registry, max_workers=4)
        result = _execute(registry, (call,), executor=executor)[0]
        self.assertFalse(result.ok)
        self.assertEqual(result.attempt_count, 2)
        self.assertTrue(result.retry_exhausted)
        self.assertEqual(len(attempts), 2)
        self.assertEqual(len(executor.retry_delays), 1)

    def test_fallback_degradation_hint_is_reserved_but_not_executed(self) -> None:
        """声明 fallback 候选时应只附加 hint，不自动切换工具。"""
        attempts: list[int] = []

        def transient_handler(tool_call, tool_use_context) -> ToolResultEnvelope:
            attempts.append(1)
            raise ConnectionError("temporary down")

        registry = ToolRegistry()
        registry.register(
            _spec(
                "fallback_candidate_tool",
                True,
                max_retries=1,
                retry_policy="transient_only",
                degradation_mode="fallback",
                fallback_tool_names=("echo_text",),
            ),
            transient_handler,
        )
        call = ToolCall("tool_1", "fallback_candidate_tool", {"text": "x"}, 0, 0)
        executor = _TestExecutor(registry, max_workers=4)
        result = _execute(registry, (call,), executor=executor)[0]
        self.assertFalse(result.ok)
        self.assertEqual(len(attempts), 2)
        self.assertTrue(
            any(
                item.get("strategy") == "fallback_tool"
                and item.get("fallback_tool_names") == ("echo_text",)
                for item in result.context_modifiers
            )
        )

    def test_unsafe_failure_adds_manual_review_hint(self) -> None:
        """非幂等副作用工具失败时应附加人工确认升级建议。"""
        registry = _registry()
        call = ToolCall("tool_1", "fail_tool", {}, 0, 0)
        result = _execute(registry, (call,))[0]
        self.assertFalse(result.ok)
        self.assertTrue(
            any(item.get("reason") == "unsafe_side_effect_or_declared_escalation" for item in result.context_modifiers)
        )

    def test_high_weight_failure_adds_manual_review_hint(self) -> None:
        """高权重任务失败时应附加人工确认升级建议。"""
        registry = _registry()
        state = create_initial_agent_state("query_001", "run")
        state = state.__class__(
            **{
                **state.__dict__,
                "task_control": AgentTaskControl(weight_level="high"),
            }
        )
        call = ToolCall("tool_1", "fail_tool", {}, 0, 0)
        executor = _TestExecutor(registry, max_workers=4)
        routes = ToolRouter(registry).route_many((call,))
        result = executor.execute_routes(routes, _context(registry, state=state))[0]
        self.assertTrue(
            any(item.get("reason") == "high_weight_failure" for item in result.context_modifiers)
        )

    def test_attempt_threshold_adds_manual_review_hint(self) -> None:
        """任务尝试次数过多时应附加人工确认升级建议。"""
        registry = _registry()
        state = create_initial_agent_state("query_001", "run")
        state = state.__class__(
            **{
                **state.__dict__,
                "task_control": AgentTaskControl(attempt_count=3),
            }
        )
        call = ToolCall("tool_1", "fail_tool", {}, 0, 0)
        executor = _TestExecutor(registry, max_workers=4)
        routes = ToolRouter(registry).route_many((call,))
        result = executor.execute_routes(routes, _context(registry, state=state))[0]
        self.assertTrue(
            any(item.get("reason") == "task_attempt_threshold_reached" for item in result.context_modifiers)
        )

    def test_invalid_input_is_wrapped(self) -> None:
        """缺少必填参数应由 executor 封装为 invalid_input。"""
        registry = _registry()
        call = ToolCall("tool_1", "echo_text", {}, 0, 0)
        result = _execute(registry, (call,))[0]
        self.assertFalse(result.ok)
        self.assertEqual(result.error_kind, "invalid_input")

    def test_route_mismatch_is_wrapped(self) -> None:
        """route/spec/call 不一致必须在执行前被拦截。"""
        registry = _registry()
        call = ToolCall("tool_1", "echo_text", {"text": "x"}, 0, 0)
        bad_route = ToolRoute(call, ToolSpec("other", "bad"), "native", True, "concurrent", True)
        context = _context(registry)
        result = ToolExecutor(registry).execute_routes((bad_route,), context)[0]
        self.assertEqual(result.error_kind, "route_mismatch")

    def test_batches_keep_safe_and_exclusive_boundaries(self) -> None:
        """执行层应按并发安全性分批。"""
        registry = _registry()
        calls = (
            ToolCall("tool_1", "echo_text", {"text": "a"}, 0, 0),
            ToolCall("tool_2", "echo_text", {"text": "b"}, 0, 1),
            ToolCall("tool_3", "exclusive_echo", {"text": "c"}, 0, 2),
            ToolCall("tool_4", "echo_text", {"text": "d"}, 0, 3),
        )
        routes = ToolRouter(registry).route_many(calls)
        batches = ToolExecutor(registry).partition_routes(routes)
        self.assertEqual([len(batch.routes) for batch in batches], [2, 1, 1])
        self.assertEqual([batch.is_concurrency_safe for batch in batches], [True, False, True])

    def test_safe_batch_runs_with_real_concurrency(self) -> None:
        """concurrency-safe 批次应使用真实并发执行。"""
        barrier = threading.Barrier(2)
        registry = _registry(barrier)
        calls = (
            ToolCall("tool_1", "slow_echo", {"text": "a"}, 0, 0),
            ToolCall("tool_2", "slow_echo", {"text": "b"}, 0, 1),
        )
        results = _execute(registry, calls)
        self.assertEqual([result.ok for result in results], [True, True])
        self.assertEqual([result.content for result in results], ["a", "b"])

    def test_results_are_sorted_by_call_index(self) -> None:
        """结果必须按 call_index 稳定排序，而不是完成顺序。"""
        registry = _registry()
        calls = (
            ToolCall("tool_2", "echo_text", {"text": "b"}, 0, 2),
            ToolCall("tool_1", "echo_text", {"text": "a"}, 0, 1),
        )
        results = _execute(registry, calls)
        self.assertEqual([result.call_index for result in results], [1, 2])

    def test_executor_does_not_mutate_agent_state_directly(self) -> None:
        """executor 只返回 envelope，不直接修改 Agent State。"""
        registry = _registry()
        state = create_initial_agent_state("query_001", "run")
        context = _context(registry, state)
        call = ToolCall("tool_1", "echo_text", {"text": "hello"}, 0, 0)
        route = ToolRouter(registry).route(call)
        ToolExecutor(registry).execute_routes((route,), context)
        self.assertEqual(state.turn_count, 1)
        self.assertEqual(state.pending_tool_use_ids, ())

    def test_envelopes_write_back_to_agent_state(self) -> None:
        """envelope 转 block 后应能通过 append_tool_results 回写。"""
        registry = _registry()
        state = create_initial_agent_state("query_001", "run")
        block = AgentContentBlock(
            type="tool_use",
            tool_use_id="tool_1",
            tool_name="echo_text",
            tool_input={"text": "hello"},
        )
        state = append_assistant_message(state, (block,))
        call = ToolCall.from_agent_block(block, 1, 0)
        envelopes = _execute(registry, (call,))
        state = append_tool_results(state, tuple(item.to_agent_block() for item in envelopes))
        self.assertEqual(state.turn_count, 2)
        self.assertEqual(state.messages[-1].content[0].content, "hello")


def _registry(barrier: threading.Barrier | None = None) -> ToolRegistry:
    """构造带假工具的测试注册表。"""
    registry = ToolRegistry()
    registry.register(_spec("echo_text", True), _echo_handler)
    registry.register(_spec("exclusive_echo", False), _echo_handler)
    registry.register(
        ToolSpec(
            "fail_tool",
            "Fail tool.",
            max_retries=0,
            retry_policy="none",
            idempotency="unsafe",
            degradation_mode="escalate",
        ),
        _fail_handler,
    )
    registry.register(_spec("slow_echo", True), _slow_handler(barrier))
    registry.register(_spec("timeout_tool", True, timeout_seconds=0.01), _timeout_handler)
    return registry


def _spec(
    name: str,
    safe: bool,
    timeout_seconds: float = 30.0,
    max_retries: int = 3,
    retry_policy: str = "transient_only",
    idempotency: str = "read_only",
    degradation_mode: str = "none",
    fallback_tool_names: tuple[str, ...] = (),
) -> ToolSpec:
    """构造带 text 必填字段的测试工具定义。"""
    return ToolSpec(
        name,
        "Echo text.",
        {"required": ["text"]},
        is_concurrency_safe=safe,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
        retry_policy=retry_policy,
        idempotency=idempotency,
        degradation_mode=degradation_mode,
        fallback_tool_names=fallback_tool_names,
    )


def _context(
    registry: ToolRegistry,
    state=None,
    tool_content=None,
) -> ToolUseContext:
    """构造测试用 ToolUseContext。"""
    state = state or create_initial_agent_state("query_001", "run")
    return ToolUseContext(
        query_id="query_001",
        cwd=PROJECT_ROOT,
        agent_state=state,
        registry=registry,
        tool_content=tool_content or {},
    )


def _execute(
    registry: ToolRegistry,
    calls,
    tool_content=None,
    executor: ToolExecutor | None = None,
) -> tuple[ToolResultEnvelope, ...]:
    """执行一组测试工具调用。"""
    routes = ToolRouter(registry).route_many(tuple(calls))
    actual_executor = executor or ToolExecutor(registry, max_workers=4)
    return actual_executor.execute_routes(routes, _context(registry, tool_content=tool_content))


def _echo_handler(tool_call, tool_use_context) -> ToolResultEnvelope:
    """测试用 echo handler，读取共享 tool_content。"""
    prefix = str(tool_use_context.tool_content.get("prefix", ""))
    return ToolResultEnvelope(
        tool_call.tool_use_id,
        tool_call.tool_name,
        True,
        prefix + str(tool_call.tool_input["text"]),
    )


def _fail_handler(tool_call, tool_use_context) -> ToolResultEnvelope:
    """测试用失败 handler。"""
    raise RuntimeError("fake tool failed")


def _slow_handler(barrier: threading.Barrier | None):
    """构造会等待 barrier 的并发测试 handler。"""

    def handler(tool_call, tool_use_context) -> ToolResultEnvelope:
        """等待另一个工具同时进入后返回。"""
        if barrier is not None:
            barrier.wait(timeout=1)
        return _echo_handler(tool_call, tool_use_context)

    return handler


def _timeout_handler(tool_call, tool_use_context) -> ToolResultEnvelope:
    """测试用超时 handler。"""
    time.sleep(0.05)
    return _echo_handler(tool_call, tool_use_context)


class _TestExecutor(ToolExecutor):
    """测试用执行器，记录退避等待而不真实 sleep。"""

    def __init__(self, registry: ToolRegistry, max_workers: int = 4) -> None:
        """初始化并记录每次重试退避。"""
        super().__init__(registry, max_workers=max_workers)
        self.retry_delays: list[float] = []

    def _sleep_before_retry(self, delay_seconds: float) -> None:
        """记录退避等待，不阻塞测试。"""
        self.retry_delays.append(delay_seconds)


def _self_test() -> None:
    """运行本文件单元测试。"""
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestAgentExecutor)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    _self_test()
