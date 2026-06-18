from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .agent import run_agent
from .config import (
    CONFIG_PATH,
    ConfigError,
    load_config,
    update_config,
)
from .llm import LlmError, OpenAICompatibleClient
from .prompt import (
    ensure_shell_command_execution_prompt,
    ensure_system_prompt,
    load_shell_command_execution_prompt,
    load_system_prompt,
    refresh_system_info,
    shell_command_execution_prompt_path,
    system_prompt_path,
)
from .session import (
    clear_session,
    load_session_messages,
    save_session_messages,
    session_path,
)
from .status import StatusSpinner


DEFAULT_MODEL = "gpt-4o-mini"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hey",
        description="A small systemwide terminal AI agent.",
    )
    parser.add_argument(
        "--set-url",
        metavar="URL",
        help=f"Set the OpenAI-compatible API base URL in {CONFIG_PATH}.",
    )
    parser.add_argument(
        "--set-key",
        metavar="KEY",
        help=f"Set the API auth key in {CONFIG_PATH}.",
    )
    parser.add_argument(
        "--set-model",
        metavar="MODEL",
        help=f"Set the default model in {CONFIG_PATH}.",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("HEY_MODEL"),
        help=f"Override the model for this request. Defaults to config model or {DEFAULT_MODEL}.",
    )
    parser.add_argument(
        "--debug-stream",
        action="store_true",
        help="Print streaming diagnostics to stderr.",
    )
    parser.add_argument(
        "--refresh-system-info",
        action="store_true",
        help=f"Regenerate only the system information section in {system_prompt_path(CONFIG_PATH)}.",
    )
    parser.add_argument(
        "-clear",
        "--clear",
        action="store_true",
        help="Clear the saved conversation session.",
    )
    parser.add_argument(
        "query",
        nargs=argparse.REMAINDER,
        help="Natural language request for the agent.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        return _run(argv)
    except BrokenPipeError:
        return _handle_broken_pipe()


def _run(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    config_update = args.set_url is not None or args.set_key is not None or args.set_model is not None
    refresh_requested = args.refresh_system_info
    clear_requested = args.clear
    query_requested = bool(args.query)
    query = " ".join(args.query).strip() if query_requested else ""

    if sum((config_update, refresh_requested, clear_requested, query_requested)) > 1:
        return _fail("Use one action at a time: update config, refresh system info, clear session, or ask a query.")

    if config_update:
        return _handle_config_update(
            config_path=CONFIG_PATH,
            base_url=args.set_url,
            api_key=args.set_key,
            model=args.set_model,
        )

    if refresh_requested:
        return _handle_system_info_refresh(CONFIG_PATH)

    if clear_requested:
        return _handle_session_clear(CONFIG_PATH)

    if query_requested:
        return _handle_query(request=query, model=args.model, debug_stream=args.debug_stream)

    parser.print_help()
    return 0


def _handle_query(*, request: str, model: str | None, debug_stream: bool = False) -> int:
    if not request:
        return _fail("Query cannot be empty.")

    try:
        config = load_config(CONFIG_PATH)
    except ConfigError as exc:
        return _fail(f"Config error: {exc}")
    except OSError as exc:
        return _fail(f"Could not read {CONFIG_PATH}: {exc}")

    if not config.base_url:
        return _fail(f"Missing base_url in {CONFIG_PATH}. Set it with: hey --set-url URL")
    if not config.api_key:
        return _fail(f"Missing api_key in {CONFIG_PATH}. Set it with: hey --set-key KEY")

    try:
        system_prompt = load_system_prompt(CONFIG_PATH)
    except OSError as exc:
        return _fail(f"Could not read or create {system_prompt_path(CONFIG_PATH)}: {exc}")

    try:
        shell_command_execution_prompt = load_shell_command_execution_prompt(CONFIG_PATH)
    except OSError as exc:
        return _fail(
            f"Could not read or create {shell_command_execution_prompt_path(CONFIG_PATH)}: {exc}"
        )

    selected_model = model or config.model or DEFAULT_MODEL
    client = OpenAICompatibleClient(base_url=config.base_url, api_key=config.api_key)

    try:
        debug = _debug_stream if debug_stream else None
        status = None if debug_stream else StatusSpinner(sys.stderr)
        session_messages = load_session_messages(CONFIG_PATH)
        answer = run_agent(
            request=request,
            client=client,
            model=selected_model,
            system_prompt=system_prompt,
            shell_command_execution_prompt=shell_command_execution_prompt,
            session_messages=session_messages,
            debug=debug,
            stream_output=_write_stdout,
            status=status,
        )
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130
    except LlmError as exc:
        return _fail(f"LLM request failed: {exc}")
    except OSError as exc:
        if isinstance(exc, BrokenPipeError):
            raise
        return _fail(f"Session error: {exc}")

    if answer:
        path = session_path(CONFIG_PATH)
        try:
            save_session_messages(
                [
                    *session_messages,
                    {"role": "user", "content": request},
                    {"role": "assistant", "content": answer},
                ],
                CONFIG_PATH,
            )
        except OSError as exc:
            if isinstance(exc, BrokenPipeError):
                raise
            _warn_session_save_failed(path, exc)
        _write_stdout("\n")
    return 0


def _write_stdout(text: str) -> None:
    sys.stdout.write(text)
    sys.stdout.flush()


def _handle_broken_pipe() -> int:
    _redirect_stdout_to_devnull()
    return 0


def _redirect_stdout_to_devnull() -> None:
    try:
        stdout_fd = sys.stdout.fileno()
    except (AttributeError, OSError, ValueError):
        return

    try:
        devnull_fd = os.open(os.devnull, os.O_WRONLY)
        try:
            os.dup2(devnull_fd, stdout_fd)
        finally:
            os.close(devnull_fd)
    except OSError:
        return


def _debug_stream(message: str) -> None:
    print(f"debug: {message}", file=sys.stderr)


def _fail(message: str) -> int:
    print(message, file=sys.stderr)
    return 1


def _warn_session_save_failed(path: Path, exc: OSError) -> None:
    print(f"Warning: could not save conversation memory at {path}: {exc}", file=sys.stderr)
    print(f"Session memory is disabled until {path.parent} is writable by your user.", file=sys.stderr)


def _handle_config_update(
    *,
    config_path: Path,
    base_url: str | None,
    api_key: str | None,
    model: str | None,
) -> int:
    try:
        changed = update_config(
            path=config_path,
            base_url=base_url,
            api_key=api_key,
            model=model,
        )
        ensure_system_prompt(config_path)
        ensure_shell_command_execution_prompt(config_path)
    except PermissionError:
        return _fail(f"Permission denied writing {config_path}. Choose a writable HEY_CONFIG_PATH or fix file ownership.")
    except ConfigError as exc:
        return _fail(f"Config error: {exc}")
    except OSError as exc:
        return _fail(f"Could not write {config_path}: {exc}")

    if changed:
        print(f"Updated {config_path}")
    else:
        print(f"No changes needed in {config_path}")
    return 0


def _handle_system_info_refresh(config_path: Path) -> int:
    try:
        path = refresh_system_info(config_path)
    except OSError as exc:
        return _fail(f"Could not refresh {system_prompt_path(config_path)}: {exc}")

    print(f"Refreshed system information in {path}")
    return 0


def _handle_session_clear(config_path: Path) -> int:
    try:
        cleared = clear_session(config_path)
    except OSError as exc:
        return _fail(f"Could not clear {session_path(config_path)}: {exc}")

    if cleared:
        print(f"Cleared saved session in {session_path(config_path)}")
    else:
        print(f"No saved session found at {session_path(config_path)}")
    return 0
