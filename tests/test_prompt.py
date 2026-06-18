from __future__ import annotations

import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hey.prompt import (
    DEFAULT_SHELL_COMMAND_EXECUTION_PROMPT,
    DEFAULT_SYSTEM_PROMPT,
    load_shell_command_execution_prompt,
    load_system_prompt,
    refresh_system_info,
    shell_command_execution_prompt_path,
    system_prompt_path,
)
from hey.system_info import SYSTEM_INFO_END, SYSTEM_INFO_START


TEST_SYSTEM_INFO = "\n".join(
    [
        SYSTEM_INFO_START,
        "Operating system: TestOS",
        "Kernel: TestKernel",
        "Architecture: test-arch",
        SYSTEM_INFO_END,
    ]
)


class PromptTests(unittest.TestCase):
    def test_missing_shell_command_execution_prompt_is_created_with_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config"
            prompt_path = shell_command_execution_prompt_path(config_path)

            prompt = load_shell_command_execution_prompt(config_path)

            self.assertEqual(prompt, f"{DEFAULT_SHELL_COMMAND_EXECUTION_PROMPT}\n")
            self.assertEqual(prompt_path.read_text(encoding="utf-8"), prompt)
            self.assertEqual(stat.S_IMODE(prompt_path.stat().st_mode), 0o644)

    def test_existing_shell_command_execution_prompt_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config"
            prompt_path = shell_command_execution_prompt_path(config_path)
            prompt_path.write_text("custom shell instructions\n", encoding="utf-8")

            prompt = load_shell_command_execution_prompt(config_path)

            self.assertEqual(prompt, "custom shell instructions\n")
            self.assertEqual(prompt_path.read_text(encoding="utf-8"), prompt)

    def test_missing_system_prompt_is_created_with_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "config"
            prompt_path = system_prompt_path(path)

            with patch("hey.prompt.build_system_info", return_value=TEST_SYSTEM_INFO):
                prompt = load_system_prompt(path)

            self.assertTrue(prompt.startswith(DEFAULT_SYSTEM_PROMPT))
            self.assertIn(TEST_SYSTEM_INFO, prompt)
            self.assertEqual(prompt_path.read_text(encoding="utf-8"), prompt)
            self.assertEqual(stat.S_IMODE(prompt_path.stat().st_mode), 0o644)

    def test_load_system_prompt_does_not_refresh_existing_system_info(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "config"
            prompt_path = system_prompt_path(path)
            prompt_path.write_text(
                "\n".join(
                    [
                        "custom prompt",
                        "",
                        SYSTEM_INFO_START,
                        "Operating system: OldOS",
                        SYSTEM_INFO_END,
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            with patch("hey.prompt.build_system_info", return_value=TEST_SYSTEM_INFO):
                prompt = load_system_prompt(path)

            self.assertTrue(prompt.startswith("custom prompt"))
            self.assertIn("OldOS", prompt)
            self.assertNotIn("Operating system: TestOS", prompt)
            self.assertEqual(prompt.count(SYSTEM_INFO_START), 1)

    def test_refresh_system_info_replaces_only_system_info_section(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "config"
            prompt_path = system_prompt_path(path)
            prompt_path.write_text(
                "\n".join(
                    [
                        "custom prompt",
                        "keep this instruction",
                        "",
                        SYSTEM_INFO_START,
                        "Operating system: OldOS",
                        "custom note inside generated section",
                        SYSTEM_INFO_END,
                        "",
                        "after generated instruction",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            with patch("hey.prompt.build_system_info", return_value=TEST_SYSTEM_INFO):
                refresh_system_info(path)

            prompt = prompt_path.read_text(encoding="utf-8")
            self.assertTrue(prompt.startswith("custom prompt\nkeep this instruction"))
            self.assertIn("Operating system: TestOS", prompt)
            self.assertNotIn("OldOS", prompt)
            self.assertNotIn("custom note inside generated section", prompt)
            self.assertIn("after generated instruction", prompt)
            self.assertEqual(prompt.count(SYSTEM_INFO_START), 1)

    def test_refresh_system_info_preserves_text_when_existing_section_is_broken(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "config"
            prompt_path = system_prompt_path(path)
            prompt_path.write_text(
                "\n".join(
                    [
                        "custom prompt",
                        "",
                        SYSTEM_INFO_START,
                        "unfinished generated section",
                        "important user note",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            with patch("hey.prompt.build_system_info", return_value=TEST_SYSTEM_INFO):
                refresh_system_info(path)

            prompt = prompt_path.read_text(encoding="utf-8")
            self.assertIn("unfinished generated section", prompt)
            self.assertIn("important user note", prompt)
            self.assertIn("Operating system: TestOS", prompt)
            self.assertEqual(prompt.count(SYSTEM_INFO_END), 1)


if __name__ == "__main__":
    unittest.main()
