# 本文件负责 DutyFlow 本地开发者 CLI 控制台的命令解析和调试入口。

from __future__ import annotations

from typing import Protocol


class HealthCheckProvider(Protocol):
    """定义 CLI 需要调用的应用健康检查能力。"""

    def health_check(self) -> object:
        """返回应用健康检查结果。"""


class CliConsole:
    """处理 /... 风格的本地开发者调试命令。"""

    def __init__(self, app: HealthCheckProvider) -> None:
        """绑定应用实例，CLI 不直接绕过应用驱动核心模块。"""
        self.app = app

    def start(self, interactive: bool = False) -> int:
        """启动 CLI 控制台；非交互模式只输出启动占位信息。"""
        if not interactive:
            print("DutyFlow CLI ready. Use --interactive for command input.")
            return 0
        return self._interactive_loop()

    def handle_command(self, command: str) -> str:
        """解析并执行单条 /... 调试命令。"""
        normalized = command.strip()
        if normalized == "/health":
            return self._format_health()
        if normalized in {"/help", "help", ""}:
            return self._help_text()
        return f"Unsupported command: {normalized}"

    def _interactive_loop(self) -> int:
        """运行最小交互循环，供本地调试使用。"""
        print("DutyFlow CLI interactive mode. Type /help or /exit.")
        while True:
            try:
                command = input("> ")
            except (EOFError, KeyboardInterrupt):
                print()
                return 0
            if command.strip() == "/exit":
                return 0
            print(self.handle_command(command))

    def _format_health(self) -> str:
        """格式化应用健康检查结果。"""
        status = self.app.health_check()
        to_text = getattr(status, "to_text", None)
        if callable(to_text):
            return to_text()
        return str(status)

    def _help_text(self) -> str:
        """返回 Step 0 阶段已支持的 CLI 命令说明。"""
        return "Supported commands: /health, /help, /exit"


class _SelfTestApp:
    """为 CLI 自测提供最小健康检查对象。"""

    def health_check(self) -> str:
        """返回自测健康状态。"""
        return "status=ok"


def _self_test() -> None:
    """验证 CLI 命令解析的最小行为。"""
    cli = CliConsole(_SelfTestApp())
    assert "status=ok" in cli.handle_command("/health")
    assert "Supported commands" in cli.handle_command("/help")


if __name__ == "__main__":
    _self_test()
    print("dutyflow cli self-test passed")
