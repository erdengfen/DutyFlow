# 本文件验证 Agent 控制快照会随任务、审批和飞书事件同步更新。

from pathlib import Path
import sys
import tempfile
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dutyflow.agent.control_state_store import AgentControlStateStore  # noqa: E402
from dutyflow.agent.skills import SkillRegistry  # noqa: E402
from dutyflow.agent.tools.registry import create_runtime_tool_registry  # noqa: E402
from dutyflow.approval.approval_request_intake import ApprovalRequestIntakeService  # noqa: E402
from dutyflow.approval.approval_resume_intake import ApprovalResumeIntakeService  # noqa: E402
from dutyflow.config.env import EnvConfig  # noqa: E402
from dutyflow.feishu.events import FeishuEventAdapter  # noqa: E402
from dutyflow.feishu.runtime import FeishuIngressService  # noqa: E402
from dutyflow.storage.file_store import FileStore  # noqa: E402
from dutyflow.storage.markdown_store import MarkdownStore  # noqa: E402
from dutyflow.tasks.background_task_intake import BackgroundTaskIntakeService  # noqa: E402
from dutyflow.tasks.task_state import TaskStore  # noqa: E402


class TestAgentControlStateStore(unittest.TestCase):
    """验证 Step 7 Agent State 可见快照接入任务和审批链。"""

    def test_sync_builds_control_snapshot_from_task_records(self) -> None:
        """控制快照应汇总 active、waiting 和最近事件字段。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            task_store = TaskStore(root)
            task_store.create_task(
                title="高优先级任务",
                task_id="task_active",
                status="queued",
                weight_level="high",
                next_action="等待后台 worker 处理|注意转义",
            )
            task_store.create_task(
                title="等待审批任务",
                task_id="task_waiting",
                status="waiting_approval",
                approval_status="waiting",
            )
            task_store.create_task(
                title="已完成任务",
                task_id="task_done",
                status="completed",
            )
            snapshot = AgentControlStateStore(root, task_store=task_store).sync(
                current_model="model-demo",
                permission_mode="auto",
                last_event_id="evt_001",
            )
            document = MarkdownStore(FileStore(root)).read_document(snapshot.path)
        self.assertEqual(snapshot.status, "waiting_approval")
        self.assertEqual(snapshot.active_task_ids, ("task_active", "task_waiting"))
        self.assertEqual(snapshot.waiting_approval_task_ids, ("task_waiting",))
        self.assertEqual(document.frontmatter["current_model"], "model-demo")
        self.assertEqual(document.frontmatter["permission_mode"], "auto")
        self.assertEqual(document.frontmatter["last_event_id"], "evt_001")
        self.assertEqual(document.frontmatter["active_task_ids"], "task_active,task_waiting")
        self.assertIn(
            "| task_active | queued | high | 0 | none | none | 等待后台 worker 处理/注意转义 |",
            document.body,
        )

    def test_sync_preserves_existing_runtime_fields_when_not_overridden(self) -> None:
        """未传入新模型、权限或事件时应保留已有快照字段。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = AgentControlStateStore(root)
            store.sync(current_model="model-a", permission_mode="default", last_event_id="evt_old")
            snapshot = store.sync()
            document = MarkdownStore(FileStore(root)).read_document(snapshot.path)
        self.assertEqual(snapshot.current_model, "model-a")
        self.assertEqual(snapshot.permission_mode, "default")
        self.assertEqual(snapshot.last_event_id, "evt_old")
        self.assertEqual(document.frontmatter["last_event_id"], "evt_old")

    def test_background_task_intake_syncs_control_state(self) -> None:
        """后台任务入口创建任务后应刷新控制快照。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            service = BackgroundTaskIntakeService(
                root,
                create_runtime_tool_registry(),
                SkillRegistry(root / "skills"),
            )
            result = service.create_async_task(
                {
                    "title": "整理资料",
                    "goal": "整理今天的飞书资料",
                    "success_criteria": "形成可读摘要",
                }
            )
            document = MarkdownStore(FileStore(root)).read_document(
                root / "data" / "state" / "agent_control_state.md"
            )
        self.assertEqual(document.frontmatter["active_task_ids"], result.task_id)
        self.assertIn(f"| {result.task_id} | queued | normal |", document.body)

    def test_approval_request_and_resume_sync_control_state(self) -> None:
        """审批等待和审批通过都应同步刷新 waiting 与 active 任务集合。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            task_store = TaskStore(root)
            task = task_store.create_task(title="写入联系人知识")
            created = ApprovalRequestIntakeService(root, task_store=task_store).create_request(
                {
                    "task_id": task.task_id,
                    "requested_action": "knowledge_write",
                    "risk_level": "high",
                    "request": "需要写入联系人知识。",
                    "reason": "该动作会修改本地知识库。",
                    "risk": "可能写入错误关系。",
                    "original_action_kind": "knowledge_write",
                    "original_tool_name": "add_contact_knowledge",
                    "original_tool_input_preview": "contact_id=contact_001",
                    "expires_at": "2026-05-01T10:00:00+08:00",
                }
            )
            waiting_doc = MarkdownStore(FileStore(root)).read_document(
                root / "data" / "state" / "agent_control_state.md"
            )
            resumed = ApprovalResumeIntakeService(
                root,
                task_store=task_store,
            ).resume_after_decision(
                {
                    "approval_id": created.approval_id,
                    "decision_result": "approved",
                    "decided_by": "user",
                    "resume_token": created.resume_token,
                }
            )
            resumed_doc = MarkdownStore(FileStore(root)).read_document(
                root / "data" / "state" / "agent_control_state.md"
            )
        self.assertEqual(waiting_doc.frontmatter["waiting_approval_task_ids"], task.task_id)
        self.assertEqual(resumed.task_status, "queued")
        self.assertEqual(resumed_doc.frontmatter["waiting_approval_task_ids"], "")
        self.assertEqual(resumed_doc.frontmatter["active_task_ids"], task.task_id)

    def test_feishu_ingress_syncs_last_event_id(self) -> None:
        """飞书接入层处理主链事件后应把 last_event_id 写入控制快照。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            adapter = FeishuEventAdapter()
            service = FeishuIngressService(root, _env_config(), adapter=adapter)
            result = service.handle_raw_event(
                adapter.create_local_fixture_event(
                    "hello",
                    event_id="evt_state_sync",
                    message_id="om_state_sync",
                )
            )
            document = MarkdownStore(FileStore(root)).read_document(
                root / "data" / "state" / "agent_control_state.md"
            )
        self.assertEqual(result.action, "accepted")
        self.assertEqual(document.frontmatter["last_event_id"], "evt_state_sync")


def _env_config() -> EnvConfig:
    """构造测试用最小飞书接入配置。"""
    return EnvConfig(
        model_api_key="",
        model_base_url="",
        model_name="demo-model",
        feishu_app_id="app_demo",
        feishu_app_secret="secret_demo",
        feishu_event_verify_token="verify_demo",
        feishu_event_encrypt_key="encrypt_demo",
        feishu_event_callback_url="",
        feishu_event_mode="fixture",
        feishu_tenant_key="tenant_demo",
        feishu_owner_open_id="ou_owner",
        feishu_owner_report_chat_id="oc_owner",
        feishu_owner_user_id="",
        feishu_owner_union_id="",
        feishu_oauth_redirect_uri="",
        feishu_oauth_default_scopes=[],
        feishu_owner_user_access_token="",
        feishu_owner_user_refresh_token="",
        feishu_owner_user_token_expires_at="",
        data_dir=Path("data"),
        log_dir=Path("data/logs"),
        runtime_env="test",
        log_level="INFO",
        permission_mode="auto",
    )


def _self_test() -> None:
    """运行本文件单元测试。"""
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestAgentControlStateStore)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    _self_test()
