# 本文件验证 /context clear 和 /context compress CLI 命令的行为。

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dutyflow.agent.core_loop import AgentLoop, ChatDebugSession  # noqa: E402
from dutyflow.agent.state import AgentContentBlock, AgentState, create_initial_agent_state  # noqa: E402
from dutyflow.agent.tools.registry import ToolRegistry  # noqa: E402
from dutyflow.cli.main import CliConsole  # noqa: E402
from dutyflow.context.runtime_context import RuntimeContextManager  # noqa: E402


# ---------------------------------------------------------------------------
# 辅助工厂
# ---------------------------------------------------------------------------

def _make_loop(tmp_dir: Path) -> AgentLoop:
    """构造最小 AgentLoop，不依赖真实模型。"""
    model_client = MagicMock()
    registry = ToolRegistry()
    return AgentLoop(model_client=model_client, registry=registry, cwd=tmp_dir)


def _make_session_with_state(tmp_dir: Path) -> ChatDebugSession:
    """构造已有 AgentState 的 ChatDebugSession。"""
    loop = _make_loop(tmp_dir)
    session = ChatDebugSession(loop)
    session.state = create_initial_agent_state("q_ctx", "hello")
    # 运行一次投影，填充 latest_working_set 和 latest_budget_report
    loop.runtime_context_manager.project_state_for_model(session.state)
    return session


# ---------------------------------------------------------------------------
# RuntimeContextManager.reset() 单元测试
# ---------------------------------------------------------------------------

class TestRuntimeContextManagerReset(unittest.TestCase):
    """验证 RuntimeContextManager.reset() 清空所有缓存状态。"""

    def test_reset_clears_working_set(self) -> None:
        """reset 后 latest_working_set 应为 None。"""
        state = create_initial_agent_state("q_r1", "hello")
        manager = RuntimeContextManager()
        manager.project_state_for_model(state)
        self.assertIsNotNone(manager.latest_working_set)
        manager.reset()
        self.assertIsNone(manager.latest_working_set)

    def test_reset_clears_budget_report(self) -> None:
        """reset 后 latest_budget_report 应为 None。"""
        state = create_initial_agent_state("q_r2", "hello")
        manager = RuntimeContextManager()
        manager.project_state_for_model(state)
        self.assertIsNotNone(manager.latest_budget_report)
        manager.reset()
        self.assertIsNone(manager.latest_budget_report)

    def test_reset_clears_state_delta(self) -> None:
        """reset 后 latest_state_delta 应为 None。"""
        state = create_initial_agent_state("q_r3", "hello")
        manager = RuntimeContextManager()
        manager.project_state_for_model(state)
        self.assertIsNotNone(manager.latest_state_delta)
        manager.reset()
        self.assertIsNone(manager.latest_state_delta)

    def test_reset_clears_health_check(self) -> None:
        """reset 后 latest_health_check 应为 None。"""
        state = create_initial_agent_state("q_r4", "hello")
        manager = RuntimeContextManager()
        manager.project_state_for_model(state)
        self.assertIsNotNone(manager.latest_health_check)
        manager.reset()
        self.assertIsNone(manager.latest_health_check)

    def test_reset_clears_compression_journal_keys(self) -> None:
        """reset 后内部去重 key 集合应清空。"""
        manager = RuntimeContextManager()
        manager._compression_journal_keys.add("dedupe_key_1")
        manager.reset()
        self.assertEqual(len(manager._compression_journal_keys), 0)

    def test_reset_preserves_budget_estimator(self) -> None:
        """reset 不应影响 budget_estimator 实例。"""
        manager = RuntimeContextManager()
        estimator_before = manager.budget_estimator
        manager.reset()
        self.assertIs(manager.budget_estimator, estimator_before)

    def test_reset_is_idempotent(self) -> None:
        """连续两次 reset 不应抛出异常。"""
        manager = RuntimeContextManager()
        manager.reset()
        manager.reset()


# ---------------------------------------------------------------------------
# /context clear 命令测试
# ---------------------------------------------------------------------------

class TestContextClearCommand(unittest.TestCase):
    """验证 /context clear 通过 CliConsole 正确清空运行时上下文。"""

    def test_clear_returns_ok_when_session_exists(self) -> None:
        """/context clear 在持续会话存在时应返回 ok。"""
        with tempfile.TemporaryDirectory() as tmp:
            app = _FakeApp(Path(tmp))
            cli = CliConsole(app)
            result_text = cli.handle_command("/context clear")
        result = json.loads(result_text)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["action"], "cleared")

    def test_clear_resets_manager_working_set(self) -> None:
        """/context clear 后，manager 的 latest_working_set 应为 None。"""
        with tempfile.TemporaryDirectory() as tmp:
            app = _FakeApp(Path(tmp))
            session = app.session
            cli = CliConsole(app)
            cli.handle_command("/context clear")
        self.assertIsNone(session.loop.runtime_context_manager.latest_working_set)

    def test_clear_no_session_returns_empty(self) -> None:
        """/context clear 在没有持续会话时应返回 empty。"""
        cli = CliConsole(_FakeAppNoSession())
        result = json.loads(cli.handle_command("/context clear"))
        self.assertEqual(result["status"], "empty")

    def test_context_help_lists_commands(self) -> None:
        """/context help 应列出 clear 和 compress 命令。"""
        cli = CliConsole(_FakeAppNoSession())
        text = cli.handle_command("/context help")
        self.assertIn("clear", text)
        self.assertIn("compress", text)

    def test_unsupported_context_subcommand_returns_error(self) -> None:
        """不支持的 /context 子命令应返回 Unsupported 提示。"""
        cli = CliConsole(_FakeAppNoSession())
        result = cli.handle_command("/context unknown_cmd")
        self.assertIn("Unsupported", result)


# ---------------------------------------------------------------------------
# /context compress 命令测试
# ---------------------------------------------------------------------------

class TestContextCompressCommand(unittest.TestCase):
    """验证 /context compress 通过 CliConsole 调用 LLM 压缩接口。"""

    def test_compress_no_session_returns_empty(self) -> None:
        """/context compress 没有上下文时应返回 empty。"""
        cli = CliConsole(_FakeAppNoSession())
        result = json.loads(cli.handle_command("/context compress"))
        self.assertEqual(result["status"], "empty")

    def test_compress_no_state_returns_empty(self) -> None:
        """/context compress 在持续会话但 state=None 时应返回 empty。"""
        with tempfile.TemporaryDirectory() as tmp:
            session = ChatDebugSession(_make_loop(Path(tmp)))
            app = _FakeApp(Path(tmp), session_override=session)
            result = json.loads(CliConsole(app).handle_command("/context compress"))
        self.assertEqual(result["status"], "empty")

    def test_compress_calls_phase_summary_service(self) -> None:
        """/context compress 应调用 phase_summary_service.maybe_create_summary。"""
        with tempfile.TemporaryDirectory() as tmp:
            session = _make_session_with_state(Path(tmp))
            mock_trigger = MagicMock()
            mock_trigger.reason = "manual_compress"
            mock_trigger.mode = "manual"
            mock_trigger.to_dict.return_value = {"reason": "manual_compress"}
            session.loop.phase_summary_service = MagicMock()
            session.loop.phase_summary_service.maybe_create_summary.return_value = (mock_trigger, None)
            app = _FakeApp(Path(tmp), session_override=session)
            result = json.loads(CliConsole(app).handle_command("/context compress"))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["action"], "no_summary")
        session.loop.phase_summary_service.maybe_create_summary.assert_called_once()
        call_kwargs = session.loop.phase_summary_service.maybe_create_summary.call_args.kwargs
        self.assertEqual(call_kwargs["forced_reason"], "manual_compress")

    def test_compress_returns_record_path_when_summary_generated(self) -> None:
        """/context compress 生成摘要时应返回 record_path 和 summary_id。"""
        with tempfile.TemporaryDirectory() as tmp:
            session = _make_session_with_state(Path(tmp))
            mock_trigger = MagicMock()
            mock_trigger.reason = "manual_compress"
            mock_trigger.mode = "manual"
            mock_trigger.to_dict.return_value = {"reason": "manual_compress"}
            mock_record = MagicMock()
            mock_record.relative_path = "data/contexts/ctx_test.md"
            mock_record.summary_id = "sum_abc"
            session.loop.phase_summary_service = MagicMock()
            session.loop.phase_summary_service.maybe_create_summary.return_value = (mock_trigger, mock_record)
            app = _FakeApp(Path(tmp), session_override=session)
            result = json.loads(CliConsole(app).handle_command("/context compress"))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["action"], "compressed")
        self.assertEqual(result["payload"]["record_path"], "data/contexts/ctx_test.md")
        self.assertEqual(result["payload"]["summary_id"], "sum_abc")


# ---------------------------------------------------------------------------
# 辅助 Fake App
# ---------------------------------------------------------------------------

class _FakeApp:
    """最小 App，注入持续会话供 /context 命令测试使用。"""

    def __init__(self, tmp_dir: Path, session_override: ChatDebugSession | None = None) -> None:
        if session_override is not None:
            self.session = session_override
        else:
            self.session = _make_session_with_state(tmp_dir)

    # HealthCheckProvider stubs
    def health_check(self) -> str:
        return "ok"

    def submit_chat_debug_task(self, user_text: str) -> str:
        return "{}"

    def get_chat_debug_status(self) -> str:
        return "{}"

    def get_latest_chat_debug(self) -> str:
        return "{}"

    def get_agent_state_debug(self) -> str:
        return "{}"

    def run_feishu_fixture_debug(self, user_text: str) -> str:
        return "{}"

    def get_feishu_status_debug(self) -> str:
        return "{}"

    def start_feishu_listener_debug(self) -> str:
        return "{}"

    def get_latest_feishu_debug(self) -> str:
        return "{}"

    def start_feishu_doctor_debug(self) -> str:
        return "{}"

    def get_feishu_doctor_debug(self) -> str:
        return "{}"

    def clear_context_debug(self) -> str:
        """清空持续会话的运行时上下文投影缓存。"""
        self.session.loop.runtime_context_manager.reset()
        return json.dumps({"status": "ok", "action": "cleared", "detail": "runtime context projection cache cleared", "payload": {}})

    def compress_context_debug(self) -> str:
        """触发持续会话的手动 LLM 阶段摘要压缩。"""
        if self.session.state is None:
            return json.dumps({"status": "empty", "action": "no_state", "detail": "no context state; run /chat run first", "payload": {}})
        loop = self.session.loop
        manager = loop.runtime_context_manager
        try:
            projected_state = manager.project_state_for_model(self.session.state)
        except Exception as exc:  # noqa: BLE001
            return json.dumps({"status": "error", "action": "projection_failed", "detail": str(exc), "payload": {}})
        working_set = manager.latest_working_set
        if working_set is None:
            return json.dumps({"status": "error", "action": "no_working_set", "detail": "no working set", "payload": {}})
        try:
            trigger, record = loop.phase_summary_service.maybe_create_summary(
                model_client=loop.model_client,
                state=self.session.state,
                projected_messages=projected_state.messages,
                working_set=working_set,
                delta=manager.latest_state_delta,
                budget=manager.latest_budget_report,
                forced_reason="manual_compress",
            )
        except Exception as exc:  # noqa: BLE001
            return json.dumps({"status": "error", "action": "compress_failed", "detail": str(exc), "payload": {}})
        if record is None:
            return json.dumps({
                "status": "ok",
                "action": "no_summary",
                "detail": f"trigger={trigger.reason} mode={trigger.mode}",
                "payload": {"trigger": trigger.to_dict()},
            })
        return json.dumps({
            "status": "ok",
            "action": "compressed",
            "detail": f"phase summary generated: {record.relative_path}",
            "payload": {
                "trigger": trigger.to_dict(),
                "record_path": record.relative_path,
                "summary_id": record.summary_id,
            },
        })


class _FakeAppNoSession:
    """最小 App，不持有持续会话供空状态测试使用。"""

    def health_check(self) -> str:
        return "ok"

    def submit_chat_debug_task(self, user_text: str) -> str:
        return "{}"

    def get_chat_debug_status(self) -> str:
        return "{}"

    def get_latest_chat_debug(self) -> str:
        return "{}"

    def get_agent_state_debug(self) -> str:
        return "{}"

    def run_feishu_fixture_debug(self, user_text: str) -> str:
        return "{}"

    def get_feishu_status_debug(self) -> str:
        return "{}"

    def start_feishu_listener_debug(self) -> str:
        return "{}"

    def get_latest_feishu_debug(self) -> str:
        return "{}"

    def start_feishu_doctor_debug(self) -> str:
        return "{}"

    def get_feishu_doctor_debug(self) -> str:
        return "{}"

    def clear_context_debug(self) -> str:
        return json.dumps({"status": "empty", "action": "no_session", "detail": "no chat debug session; run /chat run first", "payload": {}})

    def compress_context_debug(self) -> str:
        return json.dumps({"status": "empty", "action": "no_state", "detail": "no context state; run /chat run first", "payload": {}})


def _self_test() -> None:
    """运行本文件单元测试。"""
    suite = unittest.defaultTestLoader.loadTestsFromName(__name__)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    _self_test()
