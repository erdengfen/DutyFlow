# 本文件验证 ToolExecutor 的校验、分批、并发、错误封装和结果回写。

from pathlib import Path
import sys
import threading
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dutyflow.agent.context import ToolUseContext  # noqa: E402
from dutyflow.agent.executor import ToolExecutor  # noqa: E402
from dutyflow.agent.registry import ToolRegistry  # noqa: E402
from dutyflow.agent.router import ToolRoute, ToolRouter  # noqa: E402
from dutyflow.agent.state import (  # noqa: E402
    AgentContentBlock,
    append_assistant_message,
    append_tool_results,
    create_initial_agent_state,
)
from dutyflow.agent.tools import ToolCall, ToolResultEnvelope, ToolSpec  # noqa: E402


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
    registry.register(ToolSpec("fail_tool", "Fail tool."), _fail_handler)
    registry.register(_spec("slow_echo", True), _slow_handler(barrier))
    return registry


def _spec(name: str, safe: bool) -> ToolSpec:
    """构造带 text 必填字段的测试工具定义。"""
    return ToolSpec(name, "Echo text.", {"required": ["text"]}, is_concurrency_safe=safe)


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


def _execute(registry: ToolRegistry, calls, tool_content=None) -> tuple[ToolResultEnvelope, ...]:
    """执行一组测试工具调用。"""
    routes = ToolRouter(registry).route_many(tuple(calls))
    return ToolExecutor(registry, max_workers=4).execute_routes(routes, _context(registry, tool_content=tool_content))


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


def _self_test() -> None:
    """运行本文件单元测试。"""
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestAgentExecutor)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    _self_test()
