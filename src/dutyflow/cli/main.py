# 本文件负责 DutyFlow 本地开发者 CLI 控制台的命令解析和调试入口。

from __future__ import annotations

import json
from typing import Protocol


class HealthCheckProvider(Protocol):
    """定义 CLI 需要调用的应用健康检查能力。"""

    def health_check(self) -> object:
        """返回应用健康检查结果。"""

    def run_chat_debug(self, user_text: str) -> str:
        """运行 /chat 调试链路并返回可打印结果。"""

    def create_chat_debug_session(self) -> object:
        """创建可持续的 /chat 调试会话。"""


class CliConsole:
    """处理 /... 风格的本地开发者调试命令。"""

    def __init__(self, app: HealthCheckProvider) -> None:
        """绑定应用实例，CLI 不直接绕过应用驱动核心模块。"""
        self.app = app

    def start(self, interactive: bool = True) -> int:
        """启动 CLI 控制台；默认进入持续命令循环。"""
        if not interactive:
            print("DutyFlow CLI ready. Run without --no-interactive to enter command input.")
            return 0
        return self._interactive_loop()

    def handle_command(self, command: str) -> str:
        """解析并执行单条 /... 调试命令。"""
        normalized = command.strip()
        if normalized == "/health":
            return self._format_health()
        if normalized == "/chat" or normalized.startswith("/chat "):
            return self._handle_chat(normalized)
        if normalized in {"/help", "help", ""}:
            return self._help_text()
        return f"Unsupported command: {normalized}"

    def _interactive_loop(self) -> int:
        """运行最小交互循环，供本地调试使用。"""
        print("DutyFlow CLI started. Type /help to list commands, /exit to quit.")
        while True:
            try:
                command = input("DutyFlow> ")
            except (EOFError, KeyboardInterrupt):
                print()
                return 0
            if command.strip() == "/exit":
                return 0
            if command.strip() == "/chat" or command.strip().startswith("/chat "):
                if self._chat_loop(command):
                    return 0
                continue
            print(self.handle_command(command))

    def _chat_loop(self, command: str) -> bool:
        """进入持续 /chat 调试子会话，返回是否退出主程序。"""
        try:
            session = self.app.create_chat_debug_session()
        except Exception as exc:  # noqa: BLE001
            print(f"Chat session failed: {exc}")
            return False
        initial_text = command.strip().removeprefix("/chat").strip()
        print("Chat debug started. Type /back to return, /exit to quit.")
        if initial_text:
            self._run_chat_turn(session, initial_text)
        return self._chat_input_loop(session)

    def _chat_input_loop(self, session: object) -> bool:
        """读取 chat 子会话输入，直到返回主 CLI 或退出。"""
        while True:
            try:
                user_text = input("Chat> ")
            except (EOFError, KeyboardInterrupt):
                print()
                return False
            normalized = user_text.strip()
            if normalized == "/exit":
                return True
            if normalized in {"/back", ""}:
                return False
            if normalized == "/help":
                print(_chat_help_text())
                continue
            if normalized.startswith("/chat "):
                user_text = normalized.removeprefix("/chat").strip()
            if normalized.startswith("/") and not normalized.startswith("/chat "):
                print(f"Unsupported chat command: {normalized}")
                continue
            self._run_chat_turn(session, user_text)

    def _run_chat_turn(self, session: object, user_text: str) -> None:
        """执行 chat 子会话的一轮输入并打印调试结果。"""
        try:
            run_turn = getattr(session, "run_turn")
            result = run_turn(user_text)
            print(result.to_debug_text())
        except Exception as exc:  # noqa: BLE001
            print(_chat_error_text(str(exc)))

    def _format_health(self) -> str:
        """格式化应用健康检查结果。"""
        status = self.app.health_check()
        to_text = getattr(status, "to_text", None)
        if callable(to_text):
            return to_text()
        return str(status)

    def _handle_chat(self, command: str) -> str:
        """执行 CLI /chat 调试命令。"""
        user_text = command.removeprefix("/chat").strip()
        return self.app.run_chat_debug(user_text)

    def _help_text(self) -> str:
        """返回当前 CLI 命令说明。"""
        return (
            "Supported commands:\n"
            "/help - 查看命令\n"
            "/health - 查看健康状态\n"
            "/chat - 进入多轮对话调试，使用 /back 返回主 CLI\n"
            "/chat 用户输入 - 以首条消息进入调试，并持续复用 Agent State\n"
            "/exit - 退出交互控制台"
        )


def _chat_help_text() -> str:
    """返回 Chat 子会话命令说明。"""
    return (
        "Chat commands:\n"
        "/help - 查看 Chat 子会话命令\n"
        "/chat 用户输入 - 在当前 Chat State 中继续一轮\n"
        "/back - 返回主 CLI\n"
        "/exit - 退出程序"
    )


def _chat_error_text(message: str) -> str:
    """格式化 Chat 子会话错误，保持 CLI 不被异常打断。"""
    payload = {
        "error": "chat_turn_failed",
        "message": message,
        "final_text": "",
        "agent_state": {},
        "tool_results": [],
        "stop_reason": "failed",
        "turn_count": 0,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


class _SelfTestApp:
    """为 CLI 自测提供最小健康检查对象。"""

    def health_check(self) -> str:
        """返回自测健康状态。"""
        return "status=ok"

    def run_chat_debug(self, user_text: str) -> str:
        """返回自测 chat 结果。"""
        return f"chat={user_text}"

    def create_chat_debug_session(self) -> object:
        """返回自测 chat 会话。"""
        return _SelfTestChatSession()


class _SelfTestChatSession:
    """为 CLI 子会话自测提供最小对象。"""

    def run_turn(self, user_text: str) -> object:
        """返回带 to_debug_text 的结果对象。"""
        return _SelfTestChatResult(user_text)


class _SelfTestChatResult:
    """提供 CLI 子会话自测输出。"""

    def __init__(self, user_text: str) -> None:
        """保存用户输入。"""
        self.user_text = user_text

    def to_debug_text(self) -> str:
        """返回自测调试文本。"""
        return f"chat_turn={self.user_text}"


def _self_test() -> None:
    """验证 CLI 命令解析的最小行为。"""
    cli = CliConsole(_SelfTestApp())
    assert "status=ok" in cli.handle_command("/health")
    assert "Supported commands" in cli.handle_command("/help")
    assert "chat=ping" in cli.handle_command("/chat ping")


if __name__ == "__main__":
    _self_test()
    print("dutyflow cli self-test passed")
