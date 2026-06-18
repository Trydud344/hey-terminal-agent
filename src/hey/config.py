from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from .files import write_text_atomic


def default_config_path() -> Path:
    custom_path = os.environ.get("HEY_CONFIG_PATH")
    if custom_path:
        return Path(custom_path).expanduser()
    return Path.home() / ".hey" / "config"


CONFIG_PATH = default_config_path()


class ConfigError(Exception):
    """Raised when the hey config cannot be parsed or validated."""


@dataclass(frozen=True)
class HeyConfig:
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None


def load_config(path: Path = CONFIG_PATH) -> HeyConfig:
    if not path.exists():
        return HeyConfig()

    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path} is not valid TOML: {exc}") from exc

    values = {field: data.get(field) for field in ("base_url", "api_key", "model")}
    for field, value in values.items():
        if value is not None and not isinstance(value, str):
            raise ConfigError(f"{field} must be a string")

    return HeyConfig(**values)


def update_config(
    *,
    path: Path = CONFIG_PATH,
    base_url: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
) -> bool:
    current = load_config(path)
    next_config = HeyConfig(
        base_url=_normalize_base_url(base_url) if base_url is not None else current.base_url,
        api_key=_normalize_api_key(api_key) if api_key is not None else current.api_key,
        model=_normalize_model(model) if model is not None else current.model,
    )

    changed = next_config != current or not path.exists()
    if changed:
        save_config(next_config, path)
    return changed


def save_config(config: HeyConfig, path: Path = CONFIG_PATH) -> None:
    write_text_atomic(path, _format_config(config), mode=0o600, prefix=".config.")


def _format_config(config: HeyConfig) -> str:
    lines = [
        "# hey config",
        f"base_url = {_toml_string(config.base_url or '')}",
        f"api_key = {_toml_string(config.api_key or '')}",
        f"model = {_toml_string(config.model or '')}",
        "",
    ]
    return "\n".join(lines)


def _toml_string(value: str) -> str:
    return json.dumps(value)


def _normalize_base_url(value: str) -> str:
    value = value.strip().rstrip("/")
    if not value:
        raise ConfigError("base URL cannot be empty")

    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ConfigError("base URL must start with http:// or https://")

    return value


def _normalize_api_key(value: str) -> str:
    value = value.strip()
    if not value:
        raise ConfigError("API key cannot be empty")
    return value


def _normalize_model(value: str) -> str:
    value = value.strip()
    if not value:
        raise ConfigError("model cannot be empty")
    return value
