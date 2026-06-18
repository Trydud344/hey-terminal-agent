from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from hey.config import (
    ConfigError,
    default_config_path,
    load_config,
    update_config,
)
from hey.prompt import system_prompt_path


class ConfigTests(unittest.TestCase):
    def test_default_config_path_is_private_to_user(self) -> None:
        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch("hey.config.Path.home", return_value=Path("/Users/tester")),
        ):
            self.assertEqual(default_config_path(), Path("/Users/tester/.hey/config"))

    def test_config_path_can_be_overridden_for_tests(self) -> None:
        with mock.patch.dict(os.environ, {"HEY_CONFIG_PATH": "~/tmp/hey-config"}):
            self.assertEqual(default_config_path(), Path.home() / "tmp" / "hey-config")

    def test_update_config_writes_url_key_and_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "config"

            changed = update_config(
                path=path,
                base_url="https://api.example.com/v1/",
                api_key="secret-key",
                model="gpt-4o-mini",
            )

            self.assertTrue(changed)
            config = load_config(path)
            self.assertEqual(config.base_url, "https://api.example.com/v1")
            self.assertEqual(config.api_key, "secret-key")
            self.assertEqual(config.model, "gpt-4o-mini")
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_update_config_preserves_existing_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "config"

            update_config(
                path=path,
                base_url="https://api.example.com/v1",
                api_key="secret-key",
                model="gpt-4o-mini",
            )
            update_config(path=path, base_url="http://localhost:11434/v1")

            config = load_config(path)
            self.assertEqual(config.base_url, "http://localhost:11434/v1")
            self.assertEqual(config.api_key, "secret-key")
            self.assertEqual(config.model, "gpt-4o-mini")

    def test_update_config_does_not_touch_existing_system_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "config"
            prompt_path = system_prompt_path(path)
            prompt_path.write_text("custom prompt\n", encoding="utf-8")

            update_config(
                path=path,
                base_url="https://api.example.com/v1",
                api_key="secret-key",
                model="gpt-4o-mini",
            )

            self.assertEqual(prompt_path.read_text(encoding="utf-8"), "custom prompt\n")

    def test_invalid_base_url_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "config"

            with self.assertRaises(ConfigError):
                update_config(path=path, base_url="localhost:11434/v1")

    def test_invalid_model_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "config"

            with self.assertRaises(ConfigError):
                update_config(path=path, model="   ")


if __name__ == "__main__":
    unittest.main()
