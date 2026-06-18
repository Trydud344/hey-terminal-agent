from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from .config import CONFIG_PATH
from .files import write_text_atomic


SESSION_TIMEOUT_SECONDS = 10 * 60
SESSION_FILENAME = "session.json"


def session_path(config_path: Path = CONFIG_PATH) -> Path:
    custom_path = os.environ.get("HEY_SESSION_PATH")
    if custom_path:
        return Path(custom_path).expanduser()

    if config_path == Path("/etc/hey/config"):
        return Path.home() / ".hey" / SESSION_FILENAME

    return config_path.parent / SESSION_FILENAME


def load_session_messages(
    config_path: Path = CONFIG_PATH,
    *,
    now: float | None = None,
) -> list[dict[str, str]]:
    path = session_path(config_path)
    if not path.exists():
        return []

    data = _read_session_file(path)
    updated_at = data.get("updated_at")
    if not isinstance(updated_at, (int, float)):
        return []

    now = time.time() if now is None else now
    if now - float(updated_at) > SESSION_TIMEOUT_SECONDS:
        return []

    return _clean_messages(data.get("messages"))


def save_session_messages(
    messages: list[dict[str, str]],
    config_path: Path = CONFIG_PATH,
    *,
    now: float | None = None,
) -> None:
    path = session_path(config_path)
    data = {
        "updated_at": time.time() if now is None else now,
        "messages": _clean_messages(messages),
    }
    content = json.dumps(data, indent=2, sort_keys=True) + "\n"
    write_text_atomic(path, content, mode=0o600, prefix=".session.")


def clear_session(config_path: Path = CONFIG_PATH) -> bool:
    path = session_path(config_path)
    if not path.exists():
        return False

    path.unlink()
    return True


def _read_session_file(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    return data if isinstance(data, dict) else {}


def _clean_messages(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []

    messages: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = item.get("content")
        if role in {"user", "assistant"} and isinstance(content, str):
            messages.append({"role": role, "content": content})

    return messages
