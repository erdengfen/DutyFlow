# 本文件负责 Linux / WSL 下持久 shell session 的创建、执行和关闭。

from __future__ import annotations

import os
import selectors
import signal
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from time import monotonic
from typing import Any
from uuid import uuid4

# 关键开关：单次 CLI 命令最多向模型返回 8000 个字符，超过后统一截断，避免污染上下文。
MAX_CLI_OUTPUT_CHARS = 8000


class CliSessionError(RuntimeError):
    """表示 CLI session 管理层可稳定识别的错误。"""

    def __init__(self, kind: str, message: str, payload: dict[str, Any] | None = None) -> None:
        """保存错误类型和稳定消息。"""
        super().__init__(message)
        self.kind = kind
        self.payload = payload or {}


@dataclass
class CliShellSession:
    """保存一个长期存在的 bash shell 会话。"""

    session_id: str
    process: subprocess.Popen[bytes]
    cwd: Path
    shell_type: str
    platform: str
    lock: Lock = field(default_factory=Lock, repr=False)


class CliSessionManager:
    """管理当前进程内的持久 bash session。"""

    def __init__(self, output_limit: int = MAX_CLI_OUTPUT_CHARS) -> None:
        """初始化 session 注册表和统一输出截断上限。"""
        self._sessions: dict[str, CliShellSession] = {}
        self._lock = Lock()
        self.output_limit = max(256, output_limit)

    def open_session(
        self,
        *,
        base_cwd: Path,
        cwd_text: str,
        timeout_seconds: float,
        shell_type: str = "bash",
    ) -> dict[str, Any]:
        """创建一个新的持久 bash session。"""
        platform_name = _platform_name()
        if platform_name not in {"linux", "wsl"}:
            raise CliSessionError("unsupported_platform", "CLI tools currently support Linux / WSL only")
        if shell_type != "bash":
            raise CliSessionError("unsupported_shell", "current CLI tools only support bash")
        cwd = _normalize_cwd(Path(base_cwd), cwd_text)
        timeout = _normalize_timeout(timeout_seconds)
        process = subprocess.Popen(
            ["bash", "--noprofile", "--norc"],
            cwd=str(cwd),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
            bufsize=0,
            preexec_fn=os.setsid,
        )
        session_id = "cli_" + uuid4().hex[:12]
        session = CliShellSession(session_id, process, cwd, shell_type, platform_name)
        try:
            _set_nonblocking(process.stdout)
            _set_nonblocking(process.stderr)
            _write_to_stdin(process, _ready_probe_script(session_id))
            _wait_ready(process, session_id, timeout)
        except Exception:
            _terminate_process(process)
            raise
        with self._lock:
            self._sessions[session_id] = session
        return {
            "session_id": session_id,
            "shell_type": shell_type,
            "platform": platform_name,
            "cwd": str(cwd),
        }

    def exec_command(
        self,
        *,
        session_id: str,
        command: str,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        """在指定 session 中执行一条命令，并返回结构化结果。"""
        session = self._get_session(session_id)
        timeout = _normalize_timeout(timeout_seconds)
        cleaned_command = _normalize_command(command)
        marker = "cmd_" + uuid4().hex[:12]
        with session.lock:
            if session.process.poll() is not None:
                self._drop_session(session_id)
                raise CliSessionError("session_not_running", f"CLI session is not running: {session_id}")
            _write_to_stdin(session.process, _command_script(cleaned_command, marker))
            started_at = monotonic()
            try:
                result = _collect_command_result(session.process, marker, timeout, self.output_limit)
            except CliSessionError as exc:
                if exc.kind == "command_timed_out":
                    self._drop_session(session_id)
                    raise
                self._drop_session(session_id)
                raise
            result["duration_ms"] = int((monotonic() - started_at) * 1000)
            session.cwd = Path(result["cwd_after"])
            return result

    def close_session(self, session_id: str) -> dict[str, Any]:
        """关闭一个已存在的 shell session。"""
        session = self._drop_session(session_id)
        _terminate_process(session.process)
        return {
            "session_id": session.session_id,
            "closed": True,
            "cwd_after": str(session.cwd),
        }

    def close_all_sessions(self) -> None:
        """关闭当前进程内的所有 shell session，供测试和退出收尾使用。"""
        with self._lock:
            sessions = tuple(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            _terminate_process(session.process)

    def _get_session(self, session_id: str) -> CliShellSession:
        """按 ID 获取 session，不存在时返回稳定错误。"""
        with self._lock:
            session = self._sessions.get(session_id)
        if session is None:
            raise CliSessionError("unknown_session", f"unknown CLI session: {session_id}")
        return session

    def _drop_session(self, session_id: str) -> CliShellSession:
        """从注册表中移除 session。"""
        with self._lock:
            session = self._sessions.pop(session_id, None)
        if session is None:
            raise CliSessionError("unknown_session", f"unknown CLI session: {session_id}")
        return session


_GLOBAL_CLI_SESSION_MANAGER = CliSessionManager()


def get_cli_session_manager() -> CliSessionManager:
    """返回当前进程共享的 CLI session 管理器。"""
    return _GLOBAL_CLI_SESSION_MANAGER


def _platform_name() -> str:
    """返回当前平台名，只区分 linux / wsl。"""
    proc_version = Path("/proc/version")
    if proc_version.exists():
        content = proc_version.read_text(encoding="utf-8", errors="ignore").lower()
        if "microsoft" in content:
            return "wsl"
    return "linux" if os.name == "posix" else "unsupported"


def _normalize_cwd(base_cwd: Path, cwd_text: str) -> Path:
    """把 CLI session 初始目录归一化到当前工作区内。"""
    base = Path(base_cwd).resolve()
    target = (base / cwd_text).resolve() if cwd_text and not Path(cwd_text).is_absolute() else Path(cwd_text or base).resolve()
    if not target.exists() or not target.is_dir():
        raise CliSessionError("invalid_cwd", f"cwd does not exist or is not a directory: {target}")
    if not _is_relative_to(target, base):
        raise CliSessionError("cwd_out_of_bounds", f"cwd must stay inside workspace root: {base}")
    return target


def _normalize_timeout(timeout_seconds: float) -> float:
    """校验 CLI 工具使用的统一 timeout。"""
    try:
        timeout = float(timeout_seconds)
    except (TypeError, ValueError) as exc:
        raise CliSessionError("invalid_timeout", "timeout must be a positive number") from exc
    if timeout <= 0:
        raise CliSessionError("invalid_timeout", "timeout must be a positive number")
    return timeout


def _normalize_command(command: str) -> str:
    """校验待执行命令只是一条非空单行命令。"""
    cleaned = str(command).strip()
    if not cleaned:
        raise CliSessionError("invalid_command", "command cannot be empty")
    if "\n" in cleaned or "\r" in cleaned:
        raise CliSessionError("invalid_command", "command must stay on a single line")
    return cleaned


def _ready_probe_script(session_id: str) -> bytes:
    """生成 open_session 使用的 shell 就绪探针。"""
    return f"printf '__DF_READY_{session_id}\\n'\n".encode("utf-8")


def _command_script(command: str, marker: str) -> bytes:
    """生成一次命令执行对应的 shell 包装脚本。"""
    return (
        f"{command}\n"
        "__df_exit=$?\n"
        f"printf '__DF_EXIT_{marker}:%s\\n' \"$__df_exit\"\n"
        f"printf '__DF_CWD_{marker}:%s\\n' \"$PWD\"\n"
        f"printf '__DF_DONE_{marker}\\n'\n"
        f"printf '__DF_ERR_DONE_{marker}\\n' >&2\n"
    ).encode("utf-8")


def _wait_ready(process: subprocess.Popen[bytes], session_id: str, timeout_seconds: float) -> None:
    """等待 bash 会话回传 ready marker。"""
    ready_marker = f"__DF_READY_{session_id}\n"
    stdout_text, _, _, _ = _collect_stream_text(
        process,
        timeout_seconds,
        lambda stdout, stderr: ready_marker in stdout,
    )
    if ready_marker not in stdout_text:
        raise CliSessionError("session_start_failed", "CLI session did not become ready in time")


def _collect_command_result(
    process: subprocess.Popen[bytes],
    marker: str,
    timeout_seconds: float,
    output_limit: int,
) -> dict[str, Any]:
    """读取命令输出，直到 stdout / stderr 都收到结束 marker。"""
    stdout_text, stderr_text, timed_out, process_exited = _collect_stream_text(
        process,
        timeout_seconds,
        lambda stdout, stderr: _command_finished(stdout, stderr, marker),
    )
    if timed_out:
        _terminate_process(process)
        stdout_preview, stdout_truncated = _truncate_text(stdout_text, output_limit)
        stderr_preview, stderr_truncated = _truncate_text(
            stderr_text + "\ncommand timed out; session closed for safety",
            output_limit,
        )
        payload = {
            "exit_code": -1,
            "stdout": stdout_preview,
            "stderr": stderr_preview,
            "cwd_after": "",
            "duration_ms": 0,
            "timed_out": True,
            "truncated": stdout_truncated or stderr_truncated,
        }
        raise CliSessionError(
            "command_timed_out",
            _result_json_preview(payload),
            payload=payload,
        )
    if process_exited and not _command_finished(stdout_text, stderr_text, marker):
        raise CliSessionError("session_not_running", "CLI session exited before command markers were collected")
    return _parse_command_result(stdout_text, stderr_text, marker, output_limit)


def _collect_stream_text(
    process: subprocess.Popen[bytes],
    timeout_seconds: float,
    stop_condition,
) -> tuple[str, str, bool, bool]:
    """在 timeout 内持续读取 stdout / stderr，直到满足停止条件。"""
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    deadline = monotonic() + timeout_seconds
    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []
    try:
        while True:
            stdout_text = "".join(stdout_chunks)
            stderr_text = "".join(stderr_chunks)
            if stop_condition(stdout_text, stderr_text):
                return stdout_text, stderr_text, False, process.poll() is not None
            timeout_left = deadline - monotonic()
            if timeout_left <= 0:
                return stdout_text, stderr_text, True, process.poll() is not None
            events = selector.select(min(timeout_left, 0.1))
            for key, _ in events:
                chunk = _read_available_bytes(key.fileobj)
                if not chunk:
                    continue
                text = chunk.decode("utf-8", errors="replace")
                if key.data == "stdout":
                    stdout_chunks.append(text)
                else:
                    stderr_chunks.append(text)
            if process.poll() is not None and not events:
                return "".join(stdout_chunks), "".join(stderr_chunks), False, True
    finally:
        selector.close()


def _read_available_bytes(fileobj) -> bytes:
    """读取当前可用的管道字节，不阻塞等待后续输出。"""
    try:
        return os.read(fileobj.fileno(), 4096)
    except BlockingIOError:
        return b""


def _command_finished(stdout_text: str, stderr_text: str, marker: str) -> bool:
    """判断命令包装脚本的 stdout / stderr marker 是否都已出现。"""
    return (
        f"__DF_DONE_{marker}\n" in stdout_text
        and f"__DF_EXIT_{marker}:" in stdout_text
        and f"__DF_CWD_{marker}:" in stdout_text
        and f"__DF_ERR_DONE_{marker}\n" in stderr_text
    )


def _parse_command_result(stdout_text: str, stderr_text: str, marker: str, output_limit: int) -> dict[str, Any]:
    """把带 marker 的原始流拆成标准结构化结果。"""
    exit_prefix = f"__DF_EXIT_{marker}:"
    cwd_prefix = f"__DF_CWD_{marker}:"
    done_marker = f"__DF_DONE_{marker}\n"
    err_done_marker = f"__DF_ERR_DONE_{marker}\n"
    exit_index = stdout_text.find(exit_prefix)
    cwd_index = stdout_text.find(cwd_prefix)
    done_index = stdout_text.find(done_marker)
    err_done_index = stderr_text.find(err_done_marker)
    if min(exit_index, cwd_index, done_index, err_done_index) < 0:
        raise CliSessionError("command_result_invalid", "CLI command markers were incomplete")
    exit_end = stdout_text.find("\n", exit_index)
    cwd_end = stdout_text.find("\n", cwd_index)
    exit_code = int(stdout_text[exit_index + len(exit_prefix) : exit_end].strip())
    cwd_after = stdout_text[cwd_index + len(cwd_prefix) : cwd_end].strip()
    stdout_body = stdout_text[:exit_index]
    stderr_body = stderr_text[:err_done_index]
    stdout_preview, stdout_truncated = _truncate_text(stdout_body, output_limit)
    stderr_preview, stderr_truncated = _truncate_text(stderr_body, output_limit)
    return {
        "exit_code": exit_code,
        "stdout": stdout_preview,
        "stderr": stderr_preview,
        "cwd_after": cwd_after,
        "duration_ms": 0,
        "timed_out": False,
        "truncated": stdout_truncated or stderr_truncated,
    }


def _truncate_text(text: str, limit: int) -> tuple[str, bool]:
    """按统一字符上限裁剪输出文本。"""
    if len(text) <= limit:
        return text, False
    return text[:limit] + "...(truncated)", True


def _set_nonblocking(fileobj) -> None:
    """把 pipe 设为 non-blocking，供 selectors 轮询读取。"""
    os.set_blocking(fileobj.fileno(), False)


def _write_to_stdin(process: subprocess.Popen[bytes], payload: bytes) -> None:
    """向 bash stdin 写入脚本内容。"""
    if process.stdin is None:
        raise CliSessionError("session_not_running", "CLI session stdin is unavailable")
    try:
        process.stdin.write(payload)
        process.stdin.flush()
    except BrokenPipeError as exc:
        raise CliSessionError("session_not_running", "CLI session stdin is closed") from exc


def _terminate_process(process: subprocess.Popen[bytes]) -> None:
    """结束 bash 进程组，避免遗留僵尸子进程。"""
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=0.5)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    finally:
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()


def _is_relative_to(path: Path, base: Path) -> bool:
    """兼容判断 path 是否位于 base 目录之下。"""
    try:
        path.relative_to(base)
    except ValueError:
        return False
    return True


def _result_json_preview(payload: dict[str, Any]) -> str:
    """把超时结果临时压成稳定文本，供异常链路传递。"""
    parts = [
        f"exit_code={payload['exit_code']}",
        f"stdout={payload['stdout']}",
        f"stderr={payload['stderr']}",
        f"timed_out={payload['timed_out']}",
    ]
    return "; ".join(parts)


def _self_test() -> None:
    """验证 session 可以创建、执行 pwd 并关闭。"""
    manager = CliSessionManager()
    try:
        opened = manager.open_session(base_cwd=Path.cwd(), cwd_text=".", timeout_seconds=1.0)
        result = manager.exec_command(
            session_id=str(opened["session_id"]),
            command="pwd",
            timeout_seconds=1.0,
        )
        assert result["exit_code"] == 0
        assert result["cwd_after"]
        closed = manager.close_session(str(opened["session_id"]))
        assert closed["closed"] is True
    finally:
        manager.close_all_sessions()


if __name__ == "__main__":
    _self_test()
    print("dutyflow cli session manager self-test passed")
