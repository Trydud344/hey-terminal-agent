from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Callable, Iterable, TextIO

from .prompt import DEFAULT_SHELL_COMMAND_EXECUTION_PROMPT
from .shell import (
    ShellResult,
    ask_confirmation,
    cancelled_result,
    command_uses_sudo,
    run_shell_command,
    safety_for_command,
)


MAX_COMMAND_STEPS = 5
MAX_MODEL_TURNS = MAX_COMMAND_STEPS + 3
COMMAND_TIMEOUT_SECONDS = 300.0
MAX_COMMAND_OUTPUT_CHARS = 20_000

MALFORMED_COMMAND_REMINDER = (
    "Your previous reply looked like a command request, but it did not match hey's command "
    "protocol. Reply with a normal final answer, or use exactly one command block with both "
    "reason: and run: lines."
)

COMMAND_LIMIT_REMINDER = (
    f"You have reached hey's limit of {MAX_COMMAND_STEPS} command steps. "
    "Do not request more commands. Give the best final answer using the results so far."
)

HIDDEN_REPLY_STARTS = (
    "<command>",
    "User Safety:",
    "Response Safety:",
    "Safety Categories:",
)


@dataclass(frozen=True)
class CommandRequest:
    reason: str
    command: str


def run_agent(
    *,
    request: str,
    client,
    model: str,
    system_prompt: str,
    shell_command_execution_prompt: str = DEFAULT_SHELL_COMMAND_EXECUTION_PROMPT,
    session_messages: list[dict[str, str]] | None = None,
    debug: Callable[[str], None] | None = None,
    command_runner: Callable[..., ShellResult] = run_shell_command,
    confirm: Callable[[str], bool] = ask_confirmation,
    stream_output: Callable[[str], None] | None = None,
    status=None,
    stderr: TextIO | None = None,
) -> str:
    stderr = stderr or sys.stderr
    messages = _starting_messages(
        system_prompt=system_prompt,
        shell_command_execution_prompt=shell_command_execution_prompt,
        request=request,
        session_messages=session_messages or [],
    )
    command_steps = 0
    asked_for_final_after_limit = False
    last_result: ShellResult | None = None

    for _ in range(MAX_MODEL_TURNS):
        _start_status(status, _thinking_message(command_steps))
        status_stopped = False

        def stop_status_once() -> None:
            nonlocal status_stopped
            if not status_stopped:
                _stop_status(status)
                status_stopped = True

        try:
            reply, reply_was_streamed = _collect_reply(
                client.stream_messages(messages=messages, model=model, debug=debug),
                stream_output=stream_output,
                before_stream=stop_status_once,
            )
        finally:
            stop_status_once()

        command_request = parse_command_request(reply)

        if command_request is None:
            if _looks_like_command_request(reply):
                messages.append({"role": "assistant", "content": reply})
                messages.append({"role": "user", "content": MALFORMED_COMMAND_REMINDER})
                continue

            answer = reply.rstrip()
            if last_result is not None and _looks_like_provider_safety_refusal(answer):
                answer = _local_command_output_answer(last_result)
                reply_was_streamed = False
            return _final_answer(answer, stream_output, already_streamed=reply_was_streamed)

        if command_steps >= MAX_COMMAND_STEPS:
            if asked_for_final_after_limit:
                return _final_answer(
                    f"I stopped after {MAX_COMMAND_STEPS} command steps before running more commands.",
                    stream_output,
                )
            asked_for_final_after_limit = True
            messages.append({"role": "assistant", "content": reply})
            messages.append({"role": "user", "content": COMMAND_LIMIT_REMINDER})
            continue

        command_steps += 1
        _print_command_trace(command_request, stderr)
        result = _handle_command(command_request, command_runner, confirm, status, stderr)
        last_result = result
        messages.append({"role": "assistant", "content": reply})
        messages.append({"role": "user", "content": result.format_for_model()})

    return _final_answer(
        "I could not get a final answer from the model before hey's agent loop stopped.",
        stream_output,
    )


def parse_command_request(text: str) -> CommandRequest | None:
    stripped = text.strip()
    start_tag = "<command>"
    end_tag = "</command>"

    if stripped.count(start_tag) != 1 or stripped.count(end_tag) != 1:
        return None
    if not stripped.startswith(start_tag) or not stripped.endswith(end_tag):
        return None

    body = stripped[len(start_tag) : -len(end_tag)].strip()
    values: dict[str, str] = {}
    for line in body.splitlines():
        name, separator, value = line.strip().partition(":")
        if not separator:
            return None
        key = name.strip().lower()
        if key in values:
            return None
        values[key] = value.strip()

    reason = values.get("reason", "")
    command = values.get("run", "")
    if not reason or not command or set(values) != {"reason", "run"}:
        return None

    return CommandRequest(reason=reason, command=command)


def _starting_messages(
    *,
    system_prompt: str,
    shell_command_execution_prompt: str,
    request: str,
    session_messages: list[dict[str, str]],
) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": f"{system_prompt.rstrip()}\n\n{shell_command_execution_prompt.rstrip()}",
        },
        *session_messages,
        {"role": "user", "content": request},
    ]


def _collect_reply(
    chunks: Iterable[str],
    *,
    stream_output: Callable[[str], None] | None = None,
    before_stream: Callable[[], None] | None = None,
) -> tuple[str, bool]:
    if stream_output is None:
        return "".join(chunks), False

    parts: list[str] = []
    pending = ""
    streamed = False

    for chunk in chunks:
        parts.append(chunk)

        if streamed:
            stream_output(chunk)
            continue

        pending += chunk
        if _could_still_be_hidden_reply(pending):
            continue

        _before_first_stream(before_stream)
        stream_output(pending)
        pending = ""
        streamed = True

    reply = "".join(parts)
    if pending and not _looks_like_command_request(reply) and not _looks_like_provider_safety_refusal(reply):
        _before_first_stream(before_stream)
        stream_output(pending)
        streamed = True

    return reply, streamed


def _looks_like_command_request(text: str) -> bool:
    return "<command>" in text or "</command>" in text


def _looks_like_provider_safety_refusal(text: str) -> bool:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return bool(lines) and lines[0].startswith("User Safety:") and any(
        line.startswith("Safety Categories:") for line in lines
    )


def _could_still_be_hidden_reply(text: str) -> bool:
    stripped = text.lstrip()
    return not stripped or any(prefix.startswith(stripped) or stripped.startswith(prefix) for prefix in HIDDEN_REPLY_STARTS)


def _final_answer(
    answer: str,
    stream_output: Callable[[str], None] | None,
    *,
    already_streamed: bool = False,
) -> str:
    if stream_output is not None and not already_streamed and answer:
        stream_output(answer)
    return answer


def _before_first_stream(callback: Callable[[], None] | None) -> None:
    if callback is not None:
        callback()


def _local_command_output_answer(result: ShellResult) -> str:
    return "\n".join(
        [
            "The command ran, but the model provider declined to summarize the result.",
            "",
            f"Command: {result.command}",
            f"Exit code: {result.exit_code}",
            f"Duration: {result.duration_seconds:.2f}s",
            "",
            "STDOUT:",
            result.stdout or "(no stdout)",
            "",
            "STDERR:",
            result.stderr or "(no stderr)",
        ]
    )


def _print_command_trace(command_request: CommandRequest, stderr: TextIO) -> None:
    print(f"$ {command_request.command}", file=stderr)
    print(f"reason: {command_request.reason}", file=stderr)


def _handle_command(
    command_request: CommandRequest,
    command_runner: Callable[..., ShellResult],
    confirm: Callable[[str], bool],
    status,
    stderr: TextIO,
) -> ShellResult:
    decision = safety_for_command(command_request.command)

    if decision.needs_confirmation:
        _print_permission_request(decision.reason, stderr)
        if not confirm(command_request.command):
            print("cancelled", file=stderr)
            return cancelled_result(command_request.command)

    uses_sudo = command_uses_sudo(command_request.command)
    if uses_sudo:
        print("sudo may ask for your password in the terminal.", file=stderr)
    else:
        _start_status(status, f"running: {_short_command(command_request.command)}")

    try:
        return command_runner(
            command_request.command,
            timeout_seconds=COMMAND_TIMEOUT_SECONDS,
            max_output_chars=MAX_COMMAND_OUTPUT_CHARS,
        )
    finally:
        if not uses_sudo:
            _stop_status(status)


def _thinking_message(command_steps: int) -> str:
    if command_steps == 0:
        return "thinking"
    return "reading command output"


def _print_permission_request(reason: str, stderr: TextIO) -> None:
    print("permission needed", file=stderr)
    print(f"safety: {reason}", file=stderr)


def _short_command(command: str, *, max_chars: int = 60) -> str:
    if len(command) <= max_chars:
        return command
    return f"{command[: max_chars - 3]}..."


def _start_status(status, message: str) -> None:
    if status is not None:
        status.start(message)


def _stop_status(status) -> None:
    if status is not None:
        status.stop()
