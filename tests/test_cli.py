from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from hey.cli import main
from hey.config import HeyConfig
from hey.prompt import (
    DEFAULT_SHELL_COMMAND_EXECUTION_PROMPT,
    DEFAULT_SYSTEM_PROMPT,
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


class FakeClient:
    init_kwargs: dict[str, str] | None = None
    stream_messages_kwargs: dict[str, object] | None = None
    raise_keyboard_interrupt: bool = False

    def __init__(self, *, base_url: str, api_key: str) -> None:
        FakeClient.init_kwargs = {
            "base_url": base_url,
            "api_key": api_key,
        }

    def stream_messages(self, *, messages: list[dict[str, str]], model: str, debug=None):
        FakeClient.stream_messages_kwargs = {
            "messages": messages,
            "model": model,
        }
        if debug:
            debug("fake streaming diagnostics")
        if FakeClient.raise_keyboard_interrupt:
            raise KeyboardInterrupt
        yield "hello "
        yield "from cli"


class BrokenPipeAfterOneWrite(io.StringIO):
    def __init__(self) -> None:
        super().__init__()
        self._writes = 0

    def write(self, text: str) -> int:
        self._writes += 1
        if self._writes > 1:
            raise BrokenPipeError
        return super().write(text)


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeClient.init_kwargs = None
        FakeClient.stream_messages_kwargs = None
        FakeClient.raise_keyboard_interrupt = False

    def test_config_update_creates_system_prompt(self) -> None:
        output = io.StringIO()

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config"

            with (
                patch("hey.prompt.build_system_info", return_value=TEST_SYSTEM_INFO),
                patch("hey.cli.CONFIG_PATH", config_path),
                redirect_stdout(output),
            ):
                exit_code = main(["--set-url", "https://api.example.com/v1"])

            prompt = system_prompt_path(config_path).read_text(encoding="utf-8")
            shell_prompt = shell_command_execution_prompt_path(config_path).read_text(encoding="utf-8")

        self.assertEqual(exit_code, 0)
        self.assertIn("Updated", output.getvalue())
        self.assertTrue(prompt.startswith(DEFAULT_SYSTEM_PROMPT))
        self.assertIn(TEST_SYSTEM_INFO, prompt)
        self.assertEqual(shell_prompt, f"{DEFAULT_SHELL_COMMAND_EXECUTION_PROMPT}\n")

    def test_mixed_actions_are_rejected(self) -> None:
        stderr = io.StringIO()

        with redirect_stderr(stderr):
            exit_code = main(["--refresh-system-info", "hello"])

        self.assertEqual(exit_code, 1)
        self.assertIn("Use one action at a time", stderr.getvalue())

    def test_clear_removes_saved_session(self) -> None:
        output = io.StringIO()

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config"
            session_file = config_path.parent / "session.json"
            session_file.write_text('{"messages": []}', encoding="utf-8")

            with (
                patch("hey.cli.CONFIG_PATH", config_path),
                redirect_stdout(output),
            ):
                exit_code = main(["-clear"])

            self.assertFalse(session_file.exists())

        self.assertEqual(exit_code, 0)
        self.assertIn("Cleared saved session", output.getvalue())

    def test_query_uses_model_from_config(self) -> None:
        output = io.StringIO()
        saved_messages = [
            {"role": "user", "content": "previous question"},
            {"role": "assistant", "content": "previous answer"},
        ]

        with (
            patch(
                "hey.cli.load_config",
                return_value=HeyConfig(
                    base_url="https://api.example.com/v1",
                    api_key="secret-key",
                    model="configured-model",
                ),
            ),
            patch("hey.cli.load_system_prompt", return_value="system prompt"),
            patch("hey.cli.load_shell_command_execution_prompt", return_value="custom shell command prompt"),
            patch("hey.cli.load_session_messages", return_value=saved_messages),
            patch("hey.cli.save_session_messages") as save_session_messages,
            patch("hey.cli.OpenAICompatibleClient", FakeClient),
            redirect_stdout(output),
        ):
            exit_code = main(["hello there"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(output.getvalue(), "hello from cli\n")
        assert FakeClient.stream_messages_kwargs is not None
        messages = FakeClient.stream_messages_kwargs["messages"]
        assert isinstance(messages, list)
        self.assertEqual(FakeClient.stream_messages_kwargs["model"], "configured-model")
        self.assertTrue(messages[0]["content"].startswith("system prompt"))
        self.assertIn("custom shell command prompt", messages[0]["content"])
        self.assertEqual(messages[1:4], [*saved_messages, {"role": "user", "content": "hello there"}])
        save_session_messages.assert_called_once()
        saved = save_session_messages.call_args.args[0]
        self.assertEqual(
            saved,
            [
                *saved_messages,
                {"role": "user", "content": "hello there"},
                {"role": "assistant", "content": "hello from cli"},
            ],
        )
        self.assertEqual(
            FakeClient.init_kwargs,
            {"base_url": "https://api.example.com/v1", "api_key": "secret-key"},
        )

    def test_query_autogenerates_missing_system_prompt(self) -> None:
        output = io.StringIO()

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config"
            config_path.write_text(
                '\n'.join(
                    [
                        "# hey config",
                        'base_url = "https://api.example.com/v1"',
                        'api_key = "secret-key"',
                        'model = "configured-model"',
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            prompt_path = system_prompt_path(config_path)
            shell_prompt_path = shell_command_execution_prompt_path(config_path)

            with (
                patch("hey.prompt.build_system_info", return_value=TEST_SYSTEM_INFO),
                patch("hey.cli.CONFIG_PATH", config_path),
                patch("hey.cli.OpenAICompatibleClient", FakeClient),
                redirect_stdout(output),
            ):
                exit_code = main(["hello there"])

            self.assertEqual(exit_code, 0)
            prompt = prompt_path.read_text(encoding="utf-8").strip()
            shell_prompt = shell_prompt_path.read_text(encoding="utf-8")
            self.assertTrue(prompt.startswith(DEFAULT_SYSTEM_PROMPT))
            self.assertIn(TEST_SYSTEM_INFO, prompt)
            self.assertEqual(shell_prompt, f"{DEFAULT_SHELL_COMMAND_EXECUTION_PROMPT}\n")
            session_was_saved = (config_path.parent / "session.json").exists()

        self.assertEqual(output.getvalue(), "hello from cli\n")
        assert FakeClient.stream_messages_kwargs is not None
        messages = FakeClient.stream_messages_kwargs["messages"]
        assert isinstance(messages, list)
        system_prompt = messages[0]["content"]
        self.assertTrue(system_prompt.startswith(DEFAULT_SYSTEM_PROMPT))
        self.assertIn(TEST_SYSTEM_INFO, system_prompt)
        self.assertIn(DEFAULT_SHELL_COMMAND_EXECUTION_PROMPT, system_prompt)
        self.assertEqual(FakeClient.stream_messages_kwargs["model"], "configured-model")
        self.assertEqual(messages[1], {"role": "user", "content": "hello there"})
        self.assertTrue(session_was_saved)

    def test_query_option_overrides_model_from_config(self) -> None:
        output = io.StringIO()

        with (
            patch(
                "hey.cli.load_config",
                return_value=HeyConfig(
                    base_url="https://api.example.com/v1",
                    api_key="secret-key",
                    model="configured-model",
                ),
            ),
            patch("hey.cli.load_system_prompt", return_value="system prompt"),
            patch("hey.cli.load_shell_command_execution_prompt", return_value="shell command prompt"),
            patch("hey.cli.load_session_messages", return_value=[]),
            patch("hey.cli.save_session_messages"),
            patch("hey.cli.OpenAICompatibleClient", FakeClient),
            redirect_stdout(output),
        ):
            exit_code = main(["--model", "override-model", "hello there"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(output.getvalue(), "hello from cli\n")
        assert FakeClient.stream_messages_kwargs is not None
        self.assertEqual(
            FakeClient.stream_messages_kwargs["model"],
            "override-model",
        )

    def test_refresh_system_info_command_preserves_instructions(self) -> None:
        output = io.StringIO()

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config"
            prompt_path = system_prompt_path(config_path)
            prompt_path.write_text(
                "\n".join(
                    [
                        "custom instruction",
                        "",
                        SYSTEM_INFO_START,
                        "Operating system: OldOS",
                        "custom system info note",
                        SYSTEM_INFO_END,
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            with (
                patch("hey.prompt.build_system_info", return_value=TEST_SYSTEM_INFO),
                patch("hey.cli.CONFIG_PATH", config_path),
                redirect_stdout(output),
            ):
                exit_code = main(["--refresh-system-info"])

            prompt = prompt_path.read_text(encoding="utf-8")

        self.assertEqual(exit_code, 0)
        self.assertIn("Refreshed system information", output.getvalue())
        self.assertTrue(prompt.startswith("custom instruction"))
        self.assertIn("Operating system: TestOS", prompt)
        self.assertNotIn("OldOS", prompt)
        self.assertNotIn("custom system info note", prompt)

    def test_keyboard_interrupt_returns_130_without_traceback(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        FakeClient.raise_keyboard_interrupt = True

        with (
            patch(
                "hey.cli.load_config",
                return_value=HeyConfig(
                    base_url="https://api.example.com/v1",
                    api_key="secret-key",
                    model="configured-model",
                ),
            ),
            patch("hey.cli.load_system_prompt", return_value="system prompt"),
            patch("hey.cli.load_shell_command_execution_prompt", return_value="shell command prompt"),
            patch("hey.cli.load_session_messages", return_value=[]),
            patch("hey.cli.OpenAICompatibleClient", FakeClient),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            exit_code = main(["hello there"])

        self.assertEqual(exit_code, 130)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue().strip(), "Interrupted.")

    def test_debug_stream_prints_diagnostics_to_stderr(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()

        with (
            patch(
                "hey.cli.load_config",
                return_value=HeyConfig(
                    base_url="https://api.example.com/v1",
                    api_key="secret-key",
                    model="configured-model",
                ),
            ),
            patch("hey.cli.load_system_prompt", return_value="system prompt"),
            patch("hey.cli.load_shell_command_execution_prompt", return_value="shell command prompt"),
            patch("hey.cli.load_session_messages", return_value=[]),
            patch("hey.cli.save_session_messages"),
            patch("hey.cli.OpenAICompatibleClient", FakeClient),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            exit_code = main(["--debug-stream", "hello there"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(stdout.getvalue(), "hello from cli\n")
        self.assertIn("debug: fake streaming diagnostics", stderr.getvalue())

    def test_session_save_failure_warns_without_failing_answer(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()

        with (
            patch(
                "hey.cli.load_config",
                return_value=HeyConfig(
                    base_url="https://api.example.com/v1",
                    api_key="secret-key",
                    model="configured-model",
                ),
            ),
            patch("hey.cli.load_system_prompt", return_value="system prompt"),
            patch("hey.cli.load_shell_command_execution_prompt", return_value="shell command prompt"),
            patch("hey.cli.load_session_messages", return_value=[]),
            patch("hey.cli.session_path", return_value=Path("/Users/tester/.hey/session.json")),
            patch("hey.cli.save_session_messages", side_effect=PermissionError("permission denied")),
            patch("hey.cli.OpenAICompatibleClient", FakeClient),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            exit_code = main(["hello there"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(stdout.getvalue(), "hello from cli\n")
        self.assertIn("Warning: could not save conversation memory", stderr.getvalue())
        self.assertIn("Session memory is disabled", stderr.getvalue())

    def test_broken_stdout_pipe_exits_quietly(self) -> None:
        stdout = BrokenPipeAfterOneWrite()
        stderr = io.StringIO()

        with (
            patch(
                "hey.cli.load_config",
                return_value=HeyConfig(
                    base_url="https://api.example.com/v1",
                    api_key="secret-key",
                    model="configured-model",
                ),
            ),
            patch("hey.cli.load_system_prompt", return_value="system prompt"),
            patch("hey.cli.load_shell_command_execution_prompt", return_value="shell command prompt"),
            patch("hey.cli.load_session_messages", return_value=[]),
            patch("hey.cli.save_session_messages"),
            patch("hey.cli.OpenAICompatibleClient", FakeClient),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            exit_code = main(["hello there"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(stdout.getvalue(), "hello ")
        self.assertEqual(stderr.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
