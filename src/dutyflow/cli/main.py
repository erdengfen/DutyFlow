# 本文件负责 DutyFlow 本地开发者 CLI 控制台的命令解析和调试入口。

from __future__ import annotations

from typing import Protocol


class HealthCheckProvider(Protocol):
    """定义 CLI 需要调用的应用健康检查能力。"""

    def health_check(self) -> object:
        """返回应用健康检查结果。"""

    def submit_chat_debug_task(self, user_text: str) -> str:
        """提交一条非阻塞 /chat 调试任务。"""

    def get_chat_debug_status(self) -> str:
        """查看 /chat 调试 worker 状态。"""

    def get_latest_chat_debug(self) -> str:
        """查看最近一条 /chat 调试任务结果。"""

    def run_feishu_fixture_debug(self, user_text: str) -> str:
        """运行本地飞书 fixture 接入调试。"""

    def get_feishu_status_debug(self) -> str:
        """返回当前飞书监听状态。"""

    def start_feishu_listener_debug(self) -> str:
        """保留兼容；当前只返回监听状态。"""

    def get_latest_feishu_debug(self) -> str:
        """返回最近一条飞书接入调试结果。"""

    def start_feishu_doctor_debug(self) -> str:
        """启动飞书接入诊断模式并返回当前监听快照。"""

    def get_feishu_doctor_debug(self) -> str:
        """返回当前飞书监听实例的 doctor 诊断结果。"""


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
        if normalized == "/feishu" or normalized.startswith("/feishu "):
            return self._handle_feishu(normalized)
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
            if command.strip() in {"/feishu doctor", "/feishu doctor listen"}:
                if self._feishu_doctor_loop():
                    return 0
                continue
            print(self.handle_command(command))

    def _format_health(self) -> str:
        """格式化应用健康检查结果。"""
        status = self.app.health_check()
        to_text = getattr(status, "to_text", None)
        if callable(to_text):
            return to_text()
        return str(status)

    def _handle_chat(self, command: str) -> str:
        """执行非阻塞 /chat 调试任务命令。"""
        normalized = command.strip()
        if normalized in {"/chat", "/chat help"}:
            return _chat_help_text()
        if normalized == "/chat status":
            return self.app.get_chat_debug_status()
        if normalized == "/chat latest":
            return self.app.get_latest_chat_debug()
        if normalized.startswith("/chat run "):
            user_text = normalized.removeprefix("/chat run").strip()
            return self.app.submit_chat_debug_task(user_text)
        user_text = normalized.removeprefix("/chat").strip()
        return self.app.submit_chat_debug_task(user_text)

    def _feishu_doctor_loop(self) -> bool:
        """进入飞书 doctor 诊断子会话，不承担启动监听语义。"""
        result_text = self.app.get_feishu_doctor_debug()
        print(result_text)
        print("Feishu doctor opened. Type /status to inspect, /back to return, /exit to quit.")
        print("Watch listener/raw_event_count while sending real messages to the bot in Feishu.")
        return self._feishu_doctor_input_loop()

    def _feishu_doctor_input_loop(self) -> bool:
        """在飞书 doctor 子会话中读取诊断命令。"""
        while True:
            try:
                command = input("FeishuDoctor> ")
            except (EOFError, KeyboardInterrupt):
                print()
                return False
            normalized = command.strip()
            if normalized == "/exit":
                return True
            if normalized in {"", "/back"}:
                return False
            if normalized in {"/help", "/feishu help"}:
                print(_feishu_doctor_help_text())
                continue
            if normalized in {"/status", "/doctor", "/feishu doctor status"}:
                print(self.app.get_feishu_doctor_debug())
                continue
            if normalized in {"/listener", "/feishu status"}:
                print(self.app.get_feishu_status_debug())
                continue
            if normalized in {"/latest", "/feishu latest"}:
                print(self.app.get_latest_feishu_debug())
                continue
            print(f"Unsupported feishu doctor command: {normalized}")

    def _handle_feishu(self, command: str) -> str:
        """执行飞书接入层本地调试命令。"""
        normalized = command.strip()
        if normalized in {"/feishu", "/feishu status"}:
            return self.app.get_feishu_status_debug()
        if normalized == "/feishu help":
            return _feishu_help_text()
        if normalized in {"/feishu doctor", "/feishu doctor listen"}:
            return self.app.get_feishu_doctor_debug()
        if normalized == "/feishu doctor status":
            return self.app.get_feishu_doctor_debug()
        if normalized == "/feishu listen":
            return _feishu_listen_deprecated_text(self.app.get_feishu_status_debug())
        if normalized == "/feishu latest":
            return self.app.get_latest_feishu_debug()
        if normalized.startswith("/feishu fixture "):
            user_text = normalized.removeprefix("/feishu fixture").strip()
            return self.app.run_feishu_fixture_debug(user_text)
        return (
            "Unsupported feishu command: "
            f"{normalized}\n"
            f"{_feishu_help_text()}"
        )

    def _help_text(self) -> str:
        """返回当前 CLI 命令说明。"""
        return (
            "Supported commands:\n"
            "/help - 查看命令\n"
            "/health - 查看健康状态\n"
            "/chat - 查看 /chat 调试命令说明\n"
            "/chat run 用户输入 - 提交一条非阻塞调试任务\n"
            "/chat status - 查看 /chat 调试 worker 状态\n"
            "/chat latest - 查看最近一条 /chat 调试结果\n"
            "/feishu - 查看当前飞书监听状态\n"
            "/feishu status - 查看当前飞书监听状态\n"
            "/feishu fixture 文本 - 以本地 fixture 事件测试接入层\n"
            "/feishu doctor - 进入飞书长连接诊断模式\n"
            "/feishu latest - 查看最近一条飞书接入结果\n"
            "/exit - 退出交互控制台"
        )


def _chat_help_text() -> str:
    """返回非阻塞 /chat 调试命令说明。"""
    return (
        "Chat commands:\n"
        "/chat run 用户输入 - 提交一条非阻塞调试任务\n"
        "/chat 用户输入 - `/chat run` 的简写形式\n"
        "/chat status - 查看调试 worker 状态\n"
        "/chat latest - 查看最近一条调试结果\n"
        "/chat help - 查看本说明"
    )


def _feishu_help_text() -> str:
    """返回飞书接入层调试命令说明。"""
    return (
        "Feishu commands:\n"
        "/feishu - 查看当前飞书监听状态\n"
        "/feishu status - 查看当前飞书监听状态\n"
        "/feishu help - 查看飞书接入层调试命令\n"
        "/feishu fixture 文本 - 以本地 fixture 事件测试接入层\n"
        "/feishu doctor - 进入飞书长连接诊断模式\n"
        "/feishu doctor status - 查看当前 doctor 诊断快照\n"
        "/feishu latest - 查看最近一条飞书接入结果\n"
        "/feishu listen - 已废弃；当前等同于查看监听状态"
    )


def _feishu_doctor_help_text() -> str:
    """返回飞书 doctor 子会话命令说明。"""
    return (
        "Feishu doctor commands:\n"
        "/help - 查看飞书 doctor 子会话命令\n"
        "/status - 查看当前监听器诊断快照\n"
        "/listener - 查看当前飞书监听状态\n"
        "/latest - 查看最近一条飞书接入结果\n"
        "/back - 返回主 CLI\n"
        "/exit - 退出程序"
    )


def _feishu_listen_deprecated_text(status_text: str) -> str:
    """为旧的 `/feishu listen` 命令返回明确兼容提示。"""
    return "`/feishu listen` 已废弃；监听会在 app 启动时自动拉起。以下返回当前监听状态：\n" + status_text


class _SelfTestApp:
    """为 CLI 自测提供最小健康检查对象。"""

    def health_check(self) -> str:
        """返回自测健康状态。"""
        return "status=ok"

    def submit_chat_debug_task(self, user_text: str) -> str:
        """返回自测 chat 入队结果。"""
        return f'{{"action": "accepted", "payload": {{"user_text": "{user_text}"}}}}'

    def get_chat_debug_status(self) -> str:
        """返回自测 chat worker 状态。"""
        return '{"action": "worker_status", "payload": {"worker_alive": true}}'

    def get_latest_chat_debug(self) -> str:
        """返回自测最近 chat 结果。"""
        return '{"action": "completed", "payload": {"result_text": "chat=ok"}}'

    def run_feishu_fixture_debug(self, user_text: str) -> str:
        """返回自测飞书 fixture 结果。"""
        return f'{{"action": "fixture", "detail": "{user_text}"}}'

    def get_feishu_status_debug(self) -> str:
        """返回自测飞书状态。"""
        return '{"action": "listener_status", "detail": "running"}'

    def start_feishu_listener_debug(self) -> str:
        """保留兼容；返回自测飞书状态。"""
        return self.get_feishu_status_debug()

    def get_latest_feishu_debug(self) -> str:
        """返回自测最近飞书事件。"""
        return '{"action": "latest", "detail": "none"}'

    def get_feishu_doctor_debug(self) -> str:
        """返回自测飞书诊断快照。"""
        return '{"action": "doctor_status", "payload": {"listener": {"raw_event_count": 0}}}'


def _self_test() -> None:
    """验证 CLI 命令解析的最小行为。"""
    cli = CliConsole(_SelfTestApp())
    assert "status=ok" in cli.handle_command("/health")
    assert "Supported commands" in cli.handle_command("/help")
    assert '"action": "accepted"' in cli.handle_command("/chat ping")
    assert "listener_status" in cli.handle_command("/feishu")
    assert '"action": "fixture"' in cli.handle_command("/feishu fixture ping")


if __name__ == "__main__":
    _self_test()
    print("dutyflow cli self-test passed")
