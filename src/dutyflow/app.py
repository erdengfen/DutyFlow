# 本文件负责 DutyFlow 本地单进程应用的启动、生命周期编排和健康检查。

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from dutyflow.cli.main import CliConsole
from dutyflow.config.env import load_env_config
from dutyflow.logging.audit_log import AuditLogger
from dutyflow.storage.file_store import FileStore
from dutyflow.storage.markdown_store import MarkdownDocument, MarkdownStore


@dataclass
class HealthStatus:
    """表示 Step 1 阶段可验证的应用健康状态。"""

    status: str
    app_entry: str
    cli_entry: str
    data_dir_exists: bool
    skills_dir_exists: bool
    test_dir_exists: bool
    agent_control_state_exists: bool
    log_dir_exists: bool

    def to_text(self) -> str:
        """将健康状态转换为 CLI 可读文本。"""
        return (
            f"status={self.status}\n"
            f"app_entry={self.app_entry}\n"
            f"cli_entry={self.cli_entry}\n"
            f"data_dir_exists={self.data_dir_exists}\n"
            f"skills_dir_exists={self.skills_dir_exists}\n"
            f"test_dir_exists={self.test_dir_exists}\n"
            f"agent_control_state_exists={self.agent_control_state_exists}\n"
            f"log_dir_exists={self.log_dir_exists}"
        )


class DutyFlowApp:
    """编排 DutyFlow 本地 Demo 应用的生命周期。"""

    def __init__(self, project_root: Path | None = None) -> None:
        """初始化应用根目录和 CLI 控制台。"""
        self.project_root = project_root or Path.cwd()
        self.cli = CliConsole(self)

    def health_check(self) -> HealthStatus:
        """返回 Step 1 可验证的占位健康检查结果。"""
        self._ensure_runtime_layout()
        data_dir = self.project_root / "data"
        return HealthStatus(
            status="ok",
            app_entry="src/dutyflow/app.py",
            cli_entry="src/dutyflow/cli/main.py",
            data_dir_exists=data_dir.exists(),
            skills_dir_exists=(self.project_root / "skills").exists(),
            test_dir_exists=(self.project_root / "test").exists(),
            agent_control_state_exists=(
                data_dir / "state" / "agent_control_state.md"
            ).exists(),
            log_dir_exists=(data_dir / "logs").exists(),
        )

    def _ensure_runtime_layout(self) -> None:
        """初始化 Step 1 所需的数据目录、Agent 控制状态和日志。"""
        config = load_env_config(self.project_root)
        file_store = FileStore(self.project_root)
        markdown_store = MarkdownStore(file_store)
        self._ensure_data_dirs(file_store, config.data_dir)
        self._ensure_agent_control_state(markdown_store, config.data_dir)
        AuditLogger(markdown_store, config.log_dir).record(
            event_type="health_check",
            note="Step 1 health check initialized runtime layout.",
        )

    def _ensure_data_dirs(self, store: FileStore, data_dir: Path) -> None:
        """创建 Demo 期本地运行所需的基础数据目录。"""
        for relative in (
            data_dir,
            data_dir / "state",
            data_dir / "logs",
            data_dir / "events",
            data_dir / "contexts",
            data_dir / "approvals" / "pending",
            data_dir / "approvals" / "completed",
            data_dir / "tasks",
            data_dir / "reports",
            data_dir / "plans",
        ):
            store.ensure_dir(relative)

    def _ensure_agent_control_state(self, store: MarkdownStore, data_dir: Path) -> None:
        """缺失时创建最小 Agent 控制状态 Markdown 文件。"""
        state_path = data_dir / "state" / "agent_control_state.md"
        if store.exists(state_path):
            return
        document = MarkdownDocument(
            frontmatter={
                "schema": "dutyflow.agent_control_state.v1",
                "id": "agent_control_state_local_user",
                "updated_at": "1970-01-01T00:00:00+00:00",
                "current_model": "",
                "permission_mode": "default",
                "active_task_ids": "",
                "waiting_approval_task_ids": "",
                "last_event_id": "",
            },
            body=(
                "# Agent Control State\n\n"
                "## Runtime\n\n"
                "- status: initialized\n"
                "- current_model:\n"
                "- permission_mode: default\n"
                "- last_event:\n\n"
                "## Task Control\n\n"
                "| task_id | weight_level | attempt_count | approval_status | retry_status | next_action |\n"
                "|---|---|---:|---|---|---|\n\n"
                "## Recovery\n\n"
                "| scope_id | continuation_attempts | compact_attempts | transport_attempts | tool_error_attempts |\n"
                "|---|---:|---:|---:|---:|\n\n"
                "## Notes\n\n"
                "Step 1 initialized placeholder control state.\n"
            ),
        )
        store.write_document(state_path, document)

    def run(self, args: Sequence[str] | None = None) -> int:
        """根据命令参数启动健康检查或 CLI 控制台。"""
        parser = self._build_parser()
        parsed = parser.parse_args(args)
        if parsed.health:
            print(self.health_check().to_text())
            return 0
        return self.cli.start(interactive=parsed.interactive)

    def _build_parser(self) -> argparse.ArgumentParser:
        """构建应用启动参数解析器。"""
        parser = argparse.ArgumentParser(prog="dutyflow")
        parser.add_argument("--health", action="store_true", help="运行健康检查")
        parser.add_argument(
            "--interactive",
            action="store_true",
            help="启动本地 CLI 控制台",
        )
        return parser


def main(args: Sequence[str] | None = None) -> int:
    """提供 uv run dutyflow 使用的程序入口。"""
    app = DutyFlowApp()
    return app.run(args)


def _self_test() -> None:
    """验证应用入口和健康检查的最小行为。"""
    app = DutyFlowApp(Path.cwd())
    status = app.health_check()
    assert status.status == "ok"
    assert status.app_entry == "src/dutyflow/app.py"


if __name__ == "__main__":
    _self_test()
    raise SystemExit(main())
