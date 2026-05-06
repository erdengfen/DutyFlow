# 本文件验证 Step 0 应用入口、CLI 入口和基础目录骨架。

from pathlib import Path
import io
import json
from types import SimpleNamespace
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dutyflow.app import DutyFlowApp
from dutyflow.feishu.scope_registry import (  # noqa: E402
    DRIVE_FOLDER_SCOPE,
    GROUP_CHAT_SCOPE,
    GROUP_MESSAGE_COLLECTOR,
    USER_DOCUMENT_COLLECTOR,
    FeishuScopeRecord,
    FeishuScopeRegistry,
)


class TestAppEntry(unittest.TestCase):
    """验证 DutyFlow Step 0 的入口迁移和健康检查。"""

    def test_health_check_reports_required_dirs(self) -> None:
        """健康检查应返回基础目录存在状态。"""
        app = DutyFlowApp(PROJECT_ROOT)
        status = app.health_check()
        self.assertEqual(status.status, "ok")
        self.assertTrue(status.data_dir_exists)
        self.assertTrue(status.skills_dir_exists)
        self.assertTrue(status.test_dir_exists)
        self.assertTrue(status.agent_control_state_exists)

    def test_cli_health_command_uses_app(self) -> None:
        """CLI 的 /health 命令应通过应用实例获取健康状态。"""
        app = DutyFlowApp(PROJECT_ROOT)
        output = app.cli.handle_command("/health")
        self.assertIn("status=ok", output)
        self.assertIn("app_entry=src/dutyflow/app.py", output)
        self.assertIn("agent_control_state_exists=True", output)

    def test_no_interactive_keeps_script_check_available(self) -> None:
        """--no-interactive 应保留启动后立即退出的脚本检查能力。"""
        app = DutyFlowApp(PROJECT_ROOT)
        with patch.object(app, "_bootstrap_background_services") as bootstrap:
            with patch("sys.stdout", new_callable=io.StringIO):
                self.assertEqual(app.run(("--no-interactive",)), 0)
        bootstrap.assert_called_once()

    def test_run_bootstraps_background_services_before_cli(self) -> None:
        """正常启动应先拉起后台服务，再进入 CLI。"""
        app = DutyFlowApp(PROJECT_ROOT)
        with patch.object(app, "_bootstrap_background_services") as bootstrap:
            with patch.object(app.cli, "start", return_value=0) as cli_start:
                self.assertEqual(app.run(()), 0)
        bootstrap.assert_called_once()
        cli_start.assert_called_once_with(interactive=True)

    def test_bootstrap_background_services_starts_runtime_tasks_and_feishu(self) -> None:
        """后台服务启动应同时拉起 runtime、任务执行面、调度器和飞书监听。"""
        app = DutyFlowApp(PROJECT_ROOT)
        runtime = _FakeRuntimeService()
        background_worker = _FakeBackgroundTaskWorker()
        scheduler = _FakeTaskSchedulerService()
        ingress = _FakeIngressService()
        with patch.object(app, "_ensure_runtime_layout"):
            with patch.object(app, "_get_or_create_runtime_service", return_value=runtime):
                with patch.object(app, "_get_or_create_background_task_worker", return_value=background_worker):
                    with patch.object(app, "_get_or_create_task_scheduler_service", return_value=scheduler):
                        with patch.object(app, "_get_or_create_feishu_ingress_service", return_value=ingress):
                            app._bootstrap_background_services()
        self.assertTrue(runtime.started)
        self.assertTrue(background_worker.started)
        self.assertTrue(scheduler.started)
        self.assertTrue(ingress.started)

    def test_health_mode_does_not_bootstrap_background_services(self) -> None:
        """健康检查模式不应提前启动正式 runtime 和飞书监听。"""
        app = DutyFlowApp(PROJECT_ROOT)
        with patch.object(app, "_bootstrap_background_services") as bootstrap:
            with patch("sys.stdout", new_callable=io.StringIO):
                self.assertEqual(app.run(("--no-interactive",)), 0)
        bootstrap.assert_called_once()
        app = DutyFlowApp(PROJECT_ROOT)
        with patch.object(app, "_bootstrap_background_services") as bootstrap:
            with patch("sys.stdout", new_callable=io.StringIO):
                self.assertEqual(app.run(("--health",)), 0)
        bootstrap.assert_not_called()

    def test_cli_permission_prompt_uses_enter_as_approve(self) -> None:
        """CLI 审批提示应允许用户直接按 Enter 放行。"""
        app = DutyFlowApp(PROJECT_ROOT)
        with patch("builtins.input", return_value=""):
            with patch("sys.stdout", new_callable=io.StringIO) as stdout:
                approved = app._prompt_cli_permission("send_message", "sensitive tool", {"text": "hello"})
        self.assertTrue(approved)
        self.assertIn("Permission Required", stdout.getvalue())

    def test_cli_permission_prompt_allows_explicit_reject(self) -> None:
        """CLI 审批提示输入 no 时应拒绝执行。"""
        app = DutyFlowApp(PROJECT_ROOT)
        with patch("builtins.input", return_value="no"):
            self.assertFalse(app._prompt_cli_permission("send_message", "sensitive tool", {"text": "hello"}))

    def test_chat_debug_status_is_empty_before_worker_start(self) -> None:
        """未提交任务前，/chat 状态应明确提示 worker 尚未启动。"""
        app = DutyFlowApp(PROJECT_ROOT)
        payload = json.loads(app.get_chat_debug_status())
        self.assertEqual(payload["status"], "empty")
        self.assertEqual(payload["action"], "no_worker")

    def test_agent_state_debug_is_empty_before_runtime_loop_start(self) -> None:
        """正式 runtime loop 尚未创建时，AgentState 调试视图应明确返回 empty。"""
        app = DutyFlowApp(PROJECT_ROOT)
        payload = json.loads(app.get_agent_state_debug())
        self.assertEqual(payload["status"], "empty")
        self.assertEqual(payload["action"], "no_runtime_loop")

    def test_feishu_dm_debug_runs_collector_with_explicit_window(self) -> None:
        """应用层 /feishu dm 调试入口应按显式窗口调用 collector。"""
        app = DutyFlowApp(PROJECT_ROOT)
        fake_collector = _FakeDirectMessageCollector()
        with patch("dutyflow.app.FeishuUserClient.from_oauth_manager", return_value=object()):
            with patch("dutyflow.app.DirectMessageCollector", return_value=fake_collector):
                payload = json.loads(
                    app.run_feishu_dm_debug("oc_1 1778039900 1778040100")
                )

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["action"], "dm_collect")
        self.assertEqual(payload["payload"]["chat_id"], "oc_1")
        self.assertEqual(payload["payload"]["start_time"], 1778039900)
        self.assertEqual(payload["payload"]["end_time"], 1778040100)
        self.assertEqual(fake_collector.collect_args["chat_id"], "oc_1")
        self.assertTrue(fake_collector.collect_args["save_raw"])

    def test_feishu_dm_debug_requires_registered_chat_id(self) -> None:
        """未显式传 chat_id 且未完成绑定时，应提示先提供 p2p scope。"""
        app = DutyFlowApp(PROJECT_ROOT)
        with patch("dutyflow.app.load_env_config") as load_config:
            load_config.return_value = SimpleNamespace(
                feishu_owner_report_chat_id="",
                log_dir=Path("data/logs"),
            )
            payload = json.loads(app.run_feishu_dm_debug(""))

        self.assertEqual(payload["status"], "error")
        self.assertEqual(payload["action"], "missing_chat_id")
        self.assertIn("/feishu dm", payload["detail"])

    def test_feishu_dm_debug_treats_trailing_colon_number_as_lookback(self) -> None:
        """尾随中英文冒号的数字参数应按 lookback 解析，而不是误当 chat_id。"""
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        app = DutyFlowApp(Path(temp_dir.name))
        fake_collector = _FakeDirectMessageCollector()
        with patch("dutyflow.app.load_env_config") as load_config:
            load_config.return_value = SimpleNamespace(
                feishu_tenant_key="tenant_1",
                feishu_owner_open_id="ou_1",
                feishu_owner_user_id="uid_1",
                feishu_owner_report_chat_id="oc_owner",
                log_dir=Path("data/logs"),
            )
            with patch("dutyflow.app._now_unix_seconds", return_value=1778069714):
                with patch("dutyflow.app.FeishuUserClient.from_oauth_manager", return_value=object()):
                    with patch("dutyflow.app.DirectMessageCollector", return_value=fake_collector):
                        payload = json.loads(app.run_feishu_dm_debug("3600："))

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["payload"]["chat_id"], "oc_owner")
        self.assertEqual(payload["payload"]["start_time"], 1778066114)
        self.assertEqual(payload["payload"]["end_time"], 1778069714)
        self.assertEqual(fake_collector.collect_args["chat_id"], "oc_owner")

    def test_feishu_gm_debug_runs_enabled_group_collector(self) -> None:
        """应用层 /feishu gm 调试入口应只消费 enabled group_chat scope。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            app = DutyFlowApp(root)
            _enable_group_message_scope(root, "oc_group")
            fake_collector = _FakeGroupMessageCollector()
            config = SimpleNamespace(
                feishu_tenant_key="tenant_1",
                feishu_owner_open_id="ou_1",
                log_dir=Path("data/logs"),
            )
            with patch("dutyflow.app.load_env_config", return_value=config):
                with patch("dutyflow.app.FeishuUserClient.from_oauth_manager", return_value=object()):
                    with patch("dutyflow.app.GroupMessageCollector", return_value=fake_collector):
                        payload = json.loads(app.run_feishu_gm_debug("1778039900 1778040100"))

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["action"], "gm_collect")
        self.assertEqual(payload["payload"]["start_time"], 1778039900)
        self.assertEqual(payload["payload"]["end_time"], 1778040100)
        self.assertEqual(payload["payload"]["scope_count"], 1)
        self.assertEqual(fake_collector.collect_args["start_time"], 1778039900)
        self.assertTrue(fake_collector.collect_args["save_raw"])

    def test_feishu_gm_debug_reports_empty_without_enabled_group_scope(self) -> None:
        """未批准群 scope 时，/feishu gm 应提示先 discover 和 approve。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            app = DutyFlowApp(Path(temp_dir))
            config = SimpleNamespace(
                feishu_tenant_key="tenant_1",
                feishu_owner_open_id="ou_1",
                log_dir=Path("data/logs"),
            )
            with patch("dutyflow.app.load_env_config", return_value=config):
                payload = json.loads(app.run_feishu_gm_debug("3600"))

        self.assertEqual(payload["status"], "empty")
        self.assertEqual(payload["action"], "gm_collect")
        self.assertIn("/feishu discover groups", payload["detail"])

    def test_feishu_docs_debug_discovers_root_candidate(self) -> None:
        """应用层 /feishu docs discover root 应写入 root folder candidate。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            app = DutyFlowApp(root)
            fake_collector = _FakeUserDocumentCollector()
            config = SimpleNamespace(
                feishu_tenant_key="tenant_1",
                feishu_owner_open_id="ou_1",
                log_dir=Path("data/logs"),
            )
            with patch("dutyflow.app.load_env_config", return_value=config):
                with patch.object(app, "_create_user_document_collector", return_value=fake_collector):
                    payload = json.loads(app.run_feishu_docs_debug("discover root"))

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["action"], "docs_discover_root")
        self.assertEqual(payload["payload"]["root_folder_token"], "fld_root")
        self.assertTrue(fake_collector.discover_root_called)

    def test_feishu_docs_debug_runs_enabled_folder_collector(self) -> None:
        """应用层 /feishu docs 应只消费 enabled drive_folder scope。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            app = DutyFlowApp(root)
            _enable_user_document_scope(root, "fld_root")
            fake_collector = _FakeUserDocumentCollector()
            config = SimpleNamespace(
                feishu_tenant_key="tenant_1",
                feishu_owner_open_id="ou_1",
                log_dir=Path("data/logs"),
            )
            with patch("dutyflow.app.load_env_config", return_value=config):
                with patch.object(app, "_create_user_document_collector", return_value=fake_collector):
                    payload = json.loads(app.run_feishu_docs_debug(""))

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["action"], "docs_collect")
        self.assertEqual(payload["payload"]["scope_count"], 1)
        self.assertTrue(fake_collector.collect_enabled_called)

    def test_feishu_docs_debug_reports_empty_without_enabled_folder_scope(self) -> None:
        """未批准 root folder 时，/feishu docs 应提示先 discover root 和 approve。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            app = DutyFlowApp(Path(temp_dir))
            config = SimpleNamespace(
                feishu_tenant_key="tenant_1",
                feishu_owner_open_id="ou_1",
                log_dir=Path("data/logs"),
            )
            with patch("dutyflow.app.load_env_config", return_value=config):
                payload = json.loads(app.run_feishu_docs_debug(""))

        self.assertEqual(payload["status"], "empty")
        self.assertEqual(payload["action"], "docs_collect")
        self.assertIn("/feishu docs discover root", payload["detail"])

    def test_feishu_scopes_debug_lists_seeded_owner_scope(self) -> None:
        """Scope Registry 调试入口应能 seed 并列出 owner p2p scope。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            app = DutyFlowApp(Path(temp_dir))
            with patch("dutyflow.app.load_env_config") as load_config:
                load_config.return_value = SimpleNamespace(
                    feishu_tenant_key="tenant_1",
                    feishu_owner_open_id="ou_1",
                    feishu_owner_user_id="uid_1",
                    feishu_owner_report_chat_id="oc_owner",
                )

                payload = json.loads(app.run_feishu_scopes_debug(""))

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["action"], "scopes")
        self.assertEqual(payload["payload"]["scopes"][0]["scope_id"], "oc_owner")
        self.assertEqual(payload["payload"]["scopes"][0]["status"], "enabled")

    def test_feishu_dm_default_scope_respects_registry_disabled_status(self) -> None:
        """默认 /feishu dm 不应绕过 registry 中已禁用的 p2p scope。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            app = DutyFlowApp(Path(temp_dir))
            config = SimpleNamespace(
                feishu_tenant_key="tenant_1",
                feishu_owner_open_id="ou_1",
                feishu_owner_user_id="uid_1",
                feishu_owner_report_chat_id="oc_owner",
            )
            with patch("dutyflow.app.load_env_config", return_value=config):
                app.run_feishu_scopes_debug("")
                app.disable_feishu_scope_debug("oc_owner")
                payload = json.loads(app.run_feishu_dm_debug("3600"))

        self.assertEqual(payload["status"], "error")
        self.assertEqual(payload["action"], "missing_chat_id")

    def test_submit_chat_debug_task_eventually_produces_latest_result(self) -> None:
        """提交非阻塞 /chat 任务后，应能轮询拿到最近结果。"""
        app = DutyFlowApp(PROJECT_ROOT)
        with patch.object(app, "create_chat_debug_session", return_value=_FakeChatSession()):
            accepted = json.loads(app.submit_chat_debug_task("ping"))
            latest = self._wait_for_chat_debug_result(app)
        self.assertEqual(accepted["action"], "accepted")
        self.assertEqual(latest["status"], "ok")
        self.assertEqual(latest["action"], "completed")
        self.assertIn('"final_text": "pong: ping"', latest["payload"]["result_text"])

    def _wait_for_chat_debug_result(self, app: DutyFlowApp) -> dict[str, object]:
        """轮询等待后台 /chat 任务完成，避免测试直接依赖固定 sleep。"""
        deadline = time.time() + 1.0
        while time.time() < deadline:
            payload = json.loads(app.get_latest_chat_debug())
            if payload["action"] == "completed":
                return payload
            time.sleep(0.02)
        raise AssertionError("chat debug worker did not produce latest result in time")


class _FakeRuntimeService:
    """模拟可启动的 runtime service。"""

    def __init__(self) -> None:
        """记录是否已被启动。"""
        self.started = False

    def start(self):
        """模拟 runtime worker 启动。"""
        self.started = True
        return object()


class _FakeBackgroundTaskWorker:
    """模拟可启动的后台任务 worker。"""

    def __init__(self) -> None:
        """记录是否已被启动。"""
        self.started = False

    def start(self):
        """模拟后台任务 worker 启动。"""
        self.started = True
        return object()


class _FakeTaskSchedulerService:
    """模拟可启动的任务调度器。"""

    def __init__(self) -> None:
        """记录是否已被启动。"""
        self.started = False

    def start(self):
        """模拟任务调度器启动。"""
        self.started = True
        return object()


class _FakeIngressService:
    """模拟可启动监听的飞书接入服务。"""

    def __init__(self) -> None:
        """记录是否已被启动。"""
        self.started = False

    def start_long_connection(self):
        """模拟长连接启动。"""
        self.started = True
        return object()


class _FakeDirectMessageCollector:
    """模拟 direct_message_collector，记录应用层传入参数。"""

    def __init__(self) -> None:
        """初始化调用记录。"""
        self.collect_args: dict[str, object] = {}

    def collect(self, chat_id: str, **kwargs: object) -> object:
        """记录 collector 参数并返回成功结果。"""
        self.collect_args = {"chat_id": chat_id, **kwargs}
        return SimpleNamespace(
            ok=True,
            status="ok",
            items_written=1,
            record_paths=("data/ambient_context/direct_message/2026-05-06/dm_om_1.md",),
            cursor="1778040000000",
            next_cursor="1778040000",
            has_more=False,
            next_page_token="",
            sync_state_path="data/feishu/sync_state/direct_message_collector/oc_1.md",
            stopped_reason="",
            detail="",
        )


class _FakeGroupMessageCollector:
    """模拟 group_message_collector，记录应用层传入参数。"""

    def __init__(self) -> None:
        """初始化调用记录。"""
        self.collect_args: dict[str, object] = {}

    def collect_enabled_scopes(self, config: object, **kwargs: object) -> tuple[object, ...]:
        """记录 enabled scope 批量采集参数并返回成功结果。"""
        self.collect_args = {"config": config, **kwargs}
        return (
            SimpleNamespace(
                ok=True,
                status="ok",
                chat_id="oc_group",
                items_written=1,
                record_paths=("data/ambient_context/group_message/2026-05-06/gm_om_1.md",),
                cursor="1778040000000",
                next_cursor="1778040000",
                has_more=False,
                next_page_token="",
                sync_state_path="data/feishu/sync_state/group_message_collector/oc_group.md",
                stopped_reason="",
                detail="",
            ),
        )


class _FakeUserDocumentCollector:
    """模拟 user_document_collector，记录应用层调用。"""

    def __init__(self) -> None:
        """初始化调用记录。"""
        self.discover_root_called = False
        self.collect_enabled_called = False

    def discover_root(self, config: object, **kwargs: object) -> object:
        """模拟 root folder 发现成功。"""
        self.discover_root_called = True
        record = FeishuScopeRecord(
            account_id="tenant_1_ou_1",
            scope_type=DRIVE_FOLDER_SCOPE,
            scope_id="fld_root",
            collector_names=(USER_DOCUMENT_COLLECTOR,),
            discovered_from="oauth_drive_root",
        )
        return SimpleNamespace(
            ok=True,
            status="ok",
            root_folder_token="fld_root",
            scope_record=record,
            detail="",
        )

    def collect_enabled_scopes(self, config: object, **kwargs: object) -> tuple[object, ...]:
        """模拟 enabled folder 批量采集成功。"""
        self.collect_enabled_called = True
        return (
            SimpleNamespace(
                ok=True,
                status="ok",
                scope_id="fld_root",
                scope_type=DRIVE_FOLDER_SCOPE,
                items_written=1,
                candidate_scopes_written=1,
                record_paths=("data/ambient_context/user_document/2026-05-07/ud_docx_doxcn_1.md",),
                cursor="1778040000",
                next_cursor="",
                has_more=False,
                next_page_token="",
                sync_state_path="data/feishu/sync_state/user_document_collector/fld_root.md",
                stopped_reason="",
                detail="",
            ),
        )


def _enable_group_message_scope(root: Path, chat_id: str) -> None:
    """写入并启用一个 group_chat scope，供应用层 CLI 调试测试使用。"""
    registry = FeishuScopeRegistry(root)
    record = FeishuScopeRecord(
        account_id="tenant_1_ou_1",
        scope_type=GROUP_CHAT_SCOPE,
        scope_id=chat_id,
        collector_names=(GROUP_MESSAGE_COLLECTOR,),
        discovered_from="manual_add",
    )
    registry.upsert_candidate(record)
    registry.approve_scope(record.account_id, record.scope_type, record.scope_id)
    registry.enable_scope(record.account_id, record.scope_type, record.scope_id)


def _enable_user_document_scope(root: Path, folder_token: str) -> None:
    """写入并启用一个 drive_folder scope，供应用层 CLI 调试测试使用。"""
    registry = FeishuScopeRegistry(root)
    record = FeishuScopeRecord(
        account_id="tenant_1_ou_1",
        scope_type=DRIVE_FOLDER_SCOPE,
        scope_id=folder_token,
        collector_names=(USER_DOCUMENT_COLLECTOR,),
        discovered_from="manual_add",
    )
    registry.upsert_candidate(record)
    registry.approve_scope(record.account_id, record.scope_type, record.scope_id)
    registry.enable_scope(record.account_id, record.scope_type, record.scope_id)


class _FakeChatSession:
    """模拟旧 /chat loop 仍可提供的最小调试会话。"""

    def run_turn(self, user_text: str) -> object:
        """返回具备 to_debug_text 的最小结果对象。"""
        return _FakeChatResult(user_text)


class _FakeChatResult:
    """为应用测试提供最小 chat 调试结果。"""

    def __init__(self, user_text: str) -> None:
        """保存本轮用户输入。"""
        self.user_text = user_text

    def to_debug_text(self) -> str:
        """返回测试用调试文本。"""
        return f'{{"final_text": "pong: {self.user_text}"}}'


def _self_test() -> None:
    """运行本文件的单元测试。"""
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestAppEntry)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    _self_test()
