from __future__ import annotations

import subprocess
import unittest
from unittest import mock

from hey.shell import command_uses_sudo, run_shell_command, safety_for_command


class ShellTests(unittest.TestCase):
    def test_cpu_inspection_pipeline_is_allowed(self) -> None:
        decision = safety_for_command("ps aux | sort -nrk 3 | head")

        self.assertEqual(decision.action, "allow")

    def test_file_changes_ask_for_confirmation(self) -> None:
        decision = safety_for_command("rm old-file.txt")

        self.assertEqual(decision.action, "confirm")

    def test_broad_destructive_remove_asks_for_confirmation(self) -> None:
        decision = safety_for_command("rm -rf /")

        self.assertEqual(decision.action, "confirm")

    def test_printing_likely_credentials_asks_for_confirmation(self) -> None:
        decision = safety_for_command("cat ~/.aws/credentials")

        self.assertEqual(decision.action, "confirm")

    def test_sudo_commands_ask_for_confirmation(self) -> None:
        decision = safety_for_command("sudo ls /var/root")

        self.assertEqual(decision.action, "confirm")

    def test_sudo_is_detected_in_plain_and_piped_commands(self) -> None:
        self.assertTrue(command_uses_sudo("sudo whoami"))
        self.assertTrue(command_uses_sudo("/usr/bin/sudo whoami"))
        self.assertTrue(command_uses_sudo("echo hello | sudo tee /tmp/hello.txt"))
        self.assertTrue(command_uses_sudo("echo hello ; sudo whoami"))
        self.assertFalse(command_uses_sudo("printf sudo"))
        self.assertFalse(command_uses_sudo("printf no-sudo-here"))

    def test_harmless_command_execution_captures_stdout(self) -> None:
        result = run_shell_command(
            "printf hello",
            timeout_seconds=5,
            max_output_chars=100,
        )

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.stdout, "hello")
        self.assertEqual(result.stderr, "")

    def test_output_is_truncated(self) -> None:
        result = run_shell_command(
            "printf 1234567890",
            timeout_seconds=5,
            max_output_chars=5,
        )

        self.assertEqual(result.exit_code, 0)
        self.assertTrue(result.truncated)
        self.assertTrue(result.stdout.startswith("12345"))
        self.assertIn("[output truncated after 5 characters]", result.stdout)

    def test_sudo_authenticates_before_running_command(self) -> None:
        calls = []

        def fake_run(*args, **kwargs):
            calls.append((args, kwargs))
            command = args[0]
            if command == ["sudo", "-v"]:
                return subprocess.CompletedProcess(command, 0)
            return subprocess.CompletedProcess(command, 0, stdout="root\n", stderr="")

        with (
            mock.patch("hey.shell._running_as_root", return_value=False),
            mock.patch("hey.shell._stdio_can_prompt_for_sudo", return_value=True),
            mock.patch("hey.shell.subprocess.run", side_effect=fake_run),
        ):
            result = run_shell_command(
                "sudo whoami",
                timeout_seconds=5,
                max_output_chars=100,
            )

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.stdout, "root\n")
        self.assertEqual(calls[0][0][0], ["sudo", "-v"])
        self.assertNotIn("capture_output", calls[0][1])
        self.assertEqual(calls[1][0][0], "sudo whoami")
        self.assertTrue(calls[1][1]["capture_output"])

    def test_sudo_without_terminal_does_not_run_command(self) -> None:
        with (
            mock.patch("hey.shell._running_as_root", return_value=False),
            mock.patch("hey.shell._stdio_can_prompt_for_sudo", return_value=False),
            mock.patch("hey.shell.subprocess.run") as run,
        ):
            result = run_shell_command(
                "sudo whoami",
                timeout_seconds=5,
                max_output_chars=100,
            )

        self.assertEqual(result.exit_code, 1)
        self.assertIn("interactive terminal", result.stderr)
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
