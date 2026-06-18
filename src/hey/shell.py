from __future__ import annotations

import os
import pty
import re
import select
import shlex
import signal
import subprocess
import sys
import termios
import time
import tty
from dataclasses import dataclass
from typing import TextIO


TRUNCATION_MARKER = "\n[output truncated after {limit} characters]\n"

READ_ONLY_COMMANDS = {
    "pwd",
    "whoami",
    "uname",
    "sw_vers",
    "df",
    "du",
    "ps",
    "uptime",
    "ls",
    "cat",
    "head",
    "tail",
    "grep",
    "rg",
    "find",
    "sort",
    "wc",
    "cut",
    "awk",
    "sed",
}

RISKY_COMMANDS = {
    "rm",
    "mv",
    "cp",
    "chmod",
    "chown",
    "kill",
    "pkill",
    "systemctl",
    "launchctl",
    "sudo",
    "doas",
    "su",
}

SUDO_COMMAND_RE = re.compile(r"(^|[|;&(]\s*)(?:\S*/)?sudo(?=$|[\s|;&)])")

RISKY_PHRASES = (
    "brew install",
    "brew uninstall",
    "apt install",
    "apt remove",
    "apt upgrade",
    "apt-get install",
    "apt-get remove",
    "apt-get upgrade",
    "defaults write",
    "curl | sh",
    "curl | bash",
    "wget | sh",
    "wget | bash",
)

CREDENTIAL_PATTERNS = (
    ".env",
    ".aws/credentials",
    ".git-credentials",
    ".netrc",
    ".npmrc",
    ".pypirc",
    ".ssh/id_rsa",
    ".ssh/id_ed25519",
    "id_rsa",
    "id_ed25519",
)


@dataclass(frozen=True)
class SafetyDecision:
    action: str
    reason: str

    @property
    def should_run(self) -> bool:
        return self.action in {"allow", "confirm"}

    @property
    def needs_confirmation(self) -> bool:
        return self.action == "confirm"


@dataclass(frozen=True)
class ShellResult:
    command: str
    exit_code: int
    duration_seconds: float
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    truncated: bool = False

    def format_for_model(self) -> str:
        return "\n".join(
            [
                f"Command: {self.command}",
                f"Exit code: {self.exit_code}",
                f"Duration: {self.duration_seconds:.2f}s",
                "",
                "STDOUT:",
                self.stdout,
                "",
                "STDERR:",
                self.stderr,
            ]
        )


def safety_for_command(command: str) -> SafetyDecision:
    normalized = _normalize_spaces(command).lower()
    first_words = _first_words_in_pipeline(command)

    if _looks_like_dangerous_rm(command):
        return SafetyDecision("confirm", "command appears broadly destructive")
    if _mentions_credential_file(normalized):
        return SafetyDecision("confirm", "command may print likely credential files")
    if _looks_like_disk_format(normalized):
        return SafetyDecision("confirm", "command may format or partition disks")
    if _looks_like_security_disable(normalized):
        return SafetyDecision("confirm", "command may disable security settings")

    if any(phrase in normalized for phrase in RISKY_PHRASES):
        return SafetyDecision("confirm", "command may change packages or system settings")
    if any(word in RISKY_COMMANDS for word in first_words):
        return SafetyDecision("confirm", "command may change files, processes, or settings")
    if _has_shell_write_or_chain_operator(command):
        return SafetyDecision("confirm", "command uses shell operators that may have side effects")
    if _read_only_pipeline(command):
        return SafetyDecision("allow", "read-only inspection command")

    return SafetyDecision("confirm", "command is not recognized as read-only")


def ask_confirmation(command: str, *, input_stream: TextIO | None = None, output_stream: TextIO | None = None) -> bool:
    input_stream = input_stream or sys.stdin
    output_stream = output_stream or sys.stderr

    print("Run it? [y/N] ", end="", file=output_stream, flush=True)
    answer = input_stream.readline().strip().lower()
    return answer in {"y", "yes"}


def run_shell_command(
    command: str,
    *,
    timeout_seconds: float,
    max_output_chars: int,
) -> ShellResult:
    started = time.monotonic()
    can_run_interactively = _stdio_can_run_interactive_command()
    if can_run_interactively:
        return _run_shell_command_interactively(
            command,
            started=started,
            timeout_seconds=timeout_seconds,
            max_output_chars=max_output_chars,
        )

    sudo_auth_result = _authenticate_sudo_if_needed(command, timeout_seconds=timeout_seconds)
    if sudo_auth_result is not None:
        return sudo_auth_result

    try:
        completed = subprocess.run(
            command,
            shell=True,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
        )
        duration = time.monotonic() - started
        stdout, stderr, truncated = _truncate_streams(
            completed.stdout,
            completed.stderr,
            max_output_chars,
        )
        return ShellResult(
            command=command,
            exit_code=completed.returncode,
            duration_seconds=duration,
            stdout=stdout,
            stderr=stderr,
            truncated=truncated,
        )
    except subprocess.TimeoutExpired as exc:
        duration = time.monotonic() - started
        stdout = _timeout_output(exc.stdout)
        stderr = _timeout_output(exc.stderr)
        timeout_message = f"Command timed out after {timeout_seconds:g} seconds."
        stderr = f"{stderr}\n{timeout_message}".strip()
        stdout, stderr, truncated = _truncate_streams(stdout, stderr, max_output_chars)
        return ShellResult(
            command=command,
            exit_code=124,
            duration_seconds=duration,
            stdout=stdout,
            stderr=stderr,
            timed_out=True,
            truncated=truncated,
        )


def _stdio_can_run_interactive_command() -> bool:
    return _stream_is_tty(sys.stdin) and _stream_is_tty(sys.stderr) and os.name == "posix"


def _run_shell_command_interactively(
    command: str,
    *,
    started: float,
    timeout_seconds: float,
    max_output_chars: int,
) -> ShellResult:
    pid, master_fd = pty.fork()
    if pid == 0:
        os.execl("/bin/sh", "sh", "-c", command)

    old_stdin_attrs = termios.tcgetattr(sys.stdin.fileno())
    output = bytearray()
    timed_out = False
    status: int | None = None

    try:
        tty.setcbreak(sys.stdin.fileno())

        while status is None:
            waited_pid, wait_status = os.waitpid(pid, os.WNOHANG)
            if waited_pid == pid:
                status = wait_status
                break

            if time.monotonic() - started > timeout_seconds:
                timed_out = True
                _terminate_child(pid)
                break

            readable, _, _ = select.select([master_fd, sys.stdin.fileno()], [], [], 0.1)
            if master_fd in readable:
                chunk = _read_pty(master_fd)
                if chunk:
                    output.extend(chunk)
                    _write_terminal(chunk)
            if sys.stdin.fileno() in readable:
                data = os.read(sys.stdin.fileno(), 1024)
                if data:
                    os.write(master_fd, data)

        _drain_pty(master_fd, output)
        if timed_out:
            status = _wait_for_child_after_timeout(pid)

    finally:
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, old_stdin_attrs)
        os.close(master_fd)

    duration = time.monotonic() - started
    stdout = output.decode("utf-8", errors="replace")
    stderr = f"Command timed out after {timeout_seconds:g} seconds." if timed_out else ""
    stdout, stderr, truncated = _truncate_streams(stdout, stderr, max_output_chars)
    return ShellResult(
        command=command,
        exit_code=124 if timed_out else _exit_code_from_wait_status(status),
        duration_seconds=duration,
        stdout=stdout,
        stderr=stderr,
        timed_out=timed_out,
        truncated=truncated,
    )


def _terminate_child(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return


def _kill_child(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return


def _wait_for_child_after_timeout(pid: int) -> int:
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        waited_pid, status = os.waitpid(pid, os.WNOHANG)
        if waited_pid == pid:
            return status
        time.sleep(0.05)
    _kill_child(pid)
    return os.waitpid(pid, 0)[1]


def _exit_code_from_wait_status(status: int | None) -> int:
    if status is None:
        return 1
    if os.WIFEXITED(status):
        return os.WEXITSTATUS(status)
    if os.WIFSIGNALED(status):
        return 128 + os.WTERMSIG(status)
    return 1


def _read_pty(master_fd: int) -> bytes:
    try:
        return os.read(master_fd, 4096)
    except OSError:
        return b""


def _drain_pty(master_fd: int, output: bytearray) -> None:
    while True:
        readable, _, _ = select.select([master_fd], [], [], 0)
        if master_fd not in readable:
            return
        chunk = _read_pty(master_fd)
        if not chunk:
            return
        output.extend(chunk)
        _write_terminal(chunk)


def _write_terminal(chunk: bytes) -> None:
    sys.stderr.buffer.write(chunk)
    sys.stderr.buffer.flush()


def command_uses_sudo(command: str) -> bool:
    return "sudo" in _first_words_in_pipeline(command) or bool(SUDO_COMMAND_RE.search(command))


def cancelled_result(command: str) -> ShellResult:
    return ShellResult(
        command=command,
        exit_code=130,
        duration_seconds=0,
        stderr="Command was not run because the user did not confirm it.",
    )


def _authenticate_sudo_if_needed(command: str, *, timeout_seconds: float) -> ShellResult | None:
    if not command_uses_sudo(command) or _running_as_root():
        return None

    started = time.monotonic()
    if not _stdio_can_prompt_for_sudo():
        return ShellResult(
            command=command,
            exit_code=1,
            duration_seconds=time.monotonic() - started,
            stderr="sudo needs an interactive terminal before hey can run this command.",
        )

    try:
        completed = subprocess.run(["sudo", "-v"], timeout=timeout_seconds)
    except FileNotFoundError:
        return ShellResult(
            command=command,
            exit_code=127,
            duration_seconds=time.monotonic() - started,
            stderr="sudo is not available on this system.",
        )
    except subprocess.TimeoutExpired:
        return ShellResult(
            command=command,
            exit_code=124,
            duration_seconds=time.monotonic() - started,
            stderr=f"sudo authentication timed out after {timeout_seconds:g} seconds. Command was not run.",
            timed_out=True,
        )

    if completed.returncode != 0:
        return ShellResult(
            command=command,
            exit_code=completed.returncode,
            duration_seconds=time.monotonic() - started,
            stderr="sudo authentication failed or was cancelled. Command was not run.",
        )

    return None


def _running_as_root() -> bool:
    return hasattr(os, "geteuid") and os.geteuid() == 0


def _stdio_can_prompt_for_sudo() -> bool:
    return _stream_is_tty(sys.stdin) and _stream_is_tty(sys.stderr)


def _stream_is_tty(stream: TextIO) -> bool:
    isatty = getattr(stream, "isatty", None)
    return bool(isatty and isatty())


def _truncate_streams(stdout: str, stderr: str, limit: int) -> tuple[str, str, bool]:
    if len(stdout) + len(stderr) <= limit:
        return stdout, stderr, False

    marker = TRUNCATION_MARKER.format(limit=limit)
    if len(stdout) >= limit:
        return stdout[:limit] + marker, "", True

    remaining = limit - len(stdout)
    return stdout, stderr[:remaining] + marker, True


def _timeout_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _first_words_in_pipeline(command: str) -> list[str]:
    words: list[str] = []
    for part in command.split("|"):
        tokens = _split_shell_words(part.strip())
        if tokens:
            words.append(os.path.basename(tokens[0]))
    return words


def _read_only_pipeline(command: str) -> bool:
    parts = [part.strip() for part in command.split("|")]
    if not parts or any(not part for part in parts):
        return False

    for part in parts:
        tokens = _split_shell_words(part)
        if not tokens:
            return False
        name = os.path.basename(tokens[0])
        if name not in READ_ONLY_COMMANDS:
            return False
        if name == "find" and any(token in {"-delete", "-exec", "-execdir"} for token in tokens):
            return False
        if name == "sed" and any(token.startswith("-i") for token in tokens[1:]):
            return False
    return True


def _split_shell_words(text: str) -> list[str]:
    try:
        return shlex.split(text)
    except ValueError:
        return []


def _normalize_spaces(text: str) -> str:
    return " ".join(text.split())


def _has_shell_write_or_chain_operator(command: str) -> bool:
    return any(operator in command for operator in (";", "&&", "||", ">", "<", "`", "$("))


def _mentions_credential_file(normalized_command: str) -> bool:
    return any(pattern in normalized_command for pattern in CREDENTIAL_PATTERNS)


def _looks_like_disk_format(normalized_command: str) -> bool:
    return any(
        phrase in normalized_command
        for phrase in ("mkfs", "diskutil erase", "diskutil partition", "format fs=", "fdisk")
    )


def _looks_like_security_disable(normalized_command: str) -> bool:
    return any(
        phrase in normalized_command
        for phrase in ("csrutil disable", "spctl --master-disable", "setenforce 0")
    )


def _looks_like_dangerous_rm(command: str) -> bool:
    tokens = _split_shell_words(command)
    if not tokens or os.path.basename(tokens[0]) != "rm":
        return False

    has_recursive_force = any(re.fullmatch(r"-[A-Za-z]*r[A-Za-z]*f[A-Za-z]*", token) for token in tokens)
    has_recursive_force = has_recursive_force or (
        any("r" in token for token in tokens if token.startswith("-"))
        and any("f" in token for token in tokens if token.startswith("-"))
    )
    if not has_recursive_force:
        return False

    home = os.path.expanduser("~")
    dangerous_targets = {"/", "/*", "~", "~/", "$HOME", "${HOME}", home}
    targets = [token for token in tokens[1:] if not token.startswith("-")]
    return any(target in dangerous_targets for target in targets)
