# 本文件负责 DutyFlow 本地单进程应用的启动、生命周期编排和健康检查。

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from dutyflow.cli.main import CliConsole


@dataclass
class HealthStatus:
    """表示 Step 0 阶段可验证的应用健康状态。"""

    status: str
    app_entry: str
    cli_entry: str
    data_dir_exists: bool
    skills_dir_exists: bool
    test_dir_exists: bool

    def to_text(self) -> str:
        """将健康状态转换为 CLI 可读文本。"""
        return (
            f"status={self.status}\n"
            f"app_entry={self.app_entry}\n"
            f"cli_entry={self.cli_entry}\n"
            f"data_dir_exists={self.data_dir_exists}\n"
            f"skills_dir_exists={self.skills_dir_exists}\n"
            f"test_dir_exists={self.test_dir_exists}"
        )


class DutyFlowApp:
    """编排 DutyFlow 本地 Demo 应用的生命周期。"""

    def __init__(self, project_root: Path | None = None) -> None:
        """初始化应用根目录和 CLI 控制台。"""
        self.project_root = project_root or Path.cwd()
        self.cli = CliConsole(self)

    def health_check(self) -> HealthStatus:
        """返回 Step 0 可验证的占位健康检查结果。"""
        return HealthStatus(
            status="ok",
            app_entry="src/dutyflow/app.py",
            cli_entry="src/dutyflow/cli/main.py",
            data_dir_exists=(self.project_root / "data").exists(),
            skills_dir_exists=(self.project_root / "skills").exists(),
            test_dir_exists=(self.project_root / "test").exists(),
        )

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
