from __future__ import annotations

import io
import unittest

from hey.agent import CommandRequest, parse_command_request, run_agent
from hey.shell import ShellResult


class FakeClient:
    def __init__(self, replies: list[str]) -> None:
        self.replies = replies
        self.calls: list[list[dict[str, str]]] = []

    def stream_messages(self, *, messages: list[dict[str, str]], model: str, debug=None):
        self.calls.append([dict(message) for message in messages])
        if debug:
            debug(f"fake model call with {model}")
        yield self.replies.pop(0)


class ChunkyClient:
    def __init__(self, replies: list[list[str]]) -> None:
        self.replies = replies
        self.calls: list[list[dict[str, str]]] = []

    def stream_messages(self, *, messages: list[dict[str, str]], model: str, debug=None):
        self.calls.append([dict(message) for message in messages])
        yield from self.replies.pop(0)


class FakeRunner:
    def __init__(self, result: ShellResult | None = None) -> None:
        self.result = result or ShellResult(
            command="",
            exit_code=0,
            duration_seconds=0.01,
            stdout="ok\n",
        )
        self.commands: list[str] = []
        self.kwargs: list[dict[str, object]] = []

    def __call__(self, command: str, **kwargs: object) -> ShellResult:
        self.commands.append(command)
        self.kwargs.append(kwargs)
        return ShellResult(
            command=command,
            exit_code=self.result.exit_code,
            duration_seconds=self.result.duration_seconds,
            stdout=self.result.stdout,
            stderr=self.result.stderr,
        )


class FakeStatus:
    def __init__(self) -> None:
        self.events: list[tuple[str, str | None]] = []

    def start(self, message: str) -> None:
        self.events.append(("start", message))

    def stop(self) -> None:
        self.events.append(("stop", None))


class AgentTests(unittest.TestCase):
    def test_parse_command_request_accepts_one_strict_block(self) -> None:
        command = parse_command_request(
            "\n".join(
                [
                    "<command>",
                    "reason: Check disk usage",
                    "run: df -h",
                    "</command>",
                ]
            )
        )

        self.assertEqual(command, CommandRequest(reason="Check disk usage", command="df -h"))

    def test_parse_command_request_rejects_malformed_blocks(self) -> None:
        self.assertIsNone(parse_command_request("before\n<command>\nreason: x\nrun: pwd\n</command>"))
        self.assertIsNone(parse_command_request("<command>\nrun: pwd\n</command>"))
        self.assertIsNone(
            parse_command_request("<command>\nreason: x\nrun: pwd\n</command>\n<command>\nreason: y\nrun: ls\n</command>")
        )

    def test_agent_returns_final_answer_without_running_commands(self) -> None:
        client = FakeClient(["All set."])
        runner = FakeRunner()

        answer = run_agent(
            request="say hi",
            client=client,
            model="test-model",
            system_prompt="system prompt",
            shell_command_execution_prompt="custom shell command prompt",
            command_runner=runner,
        )

        self.assertEqual(answer, "All set.")
        self.assertEqual(runner.commands, [])
        self.assertTrue(client.calls[0][0]["content"].startswith("system prompt"))
        self.assertIn("custom shell command prompt", client.calls[0][0]["content"])
        self.assertEqual(client.calls[0][1], {"role": "user", "content": "say hi"})

    def test_agent_streams_normal_final_answers(self) -> None:
        client = ChunkyClient([["All ", "set."]])
        streamed: list[str] = []

        answer = run_agent(
            request="say hi",
            client=client,
            model="test-model",
            system_prompt="system prompt",
            stream_output=streamed.append,
        )

        self.assertEqual(answer, "All set.")
        self.assertEqual(streamed, ["All ", "set."])

    def test_agent_starts_with_saved_session_messages(self) -> None:
        client = FakeClient(["That matches the earlier answer."])
        saved_messages = [
            {"role": "user", "content": "my project is called hey"},
            {"role": "assistant", "content": "Got it."},
        ]

        answer = run_agent(
            request="what is my project called?",
            client=client,
            model="test-model",
            system_prompt="system prompt",
            session_messages=saved_messages,
        )

        self.assertEqual(answer, "That matches the earlier answer.")
        self.assertEqual(client.calls[0][1:4], [*saved_messages, {"role": "user", "content": "what is my project called?"}])

    def test_agent_runs_command_and_sends_result_back_to_model(self) -> None:
        client = FakeClient(
            [
                "\n".join(
                    [
                        "<command>",
                        "reason: Check disk usage",
                        "run: df -h",
                        "</command>",
                    ]
                ),
                "Disk usage looks fine.",
            ]
        )
        runner = FakeRunner(ShellResult(command="", exit_code=0, duration_seconds=0.02, stdout="disk ok\n"))
        stderr = io.StringIO()

        answer = run_agent(
            request="check disk",
            client=client,
            model="test-model",
            system_prompt="system prompt",
            command_runner=runner,
            stderr=stderr,
        )

        self.assertEqual(answer, "Disk usage looks fine.")
        self.assertEqual(runner.commands, ["df -h"])
        self.assertEqual(runner.kwargs[0]["timeout_seconds"], 300.0)
        self.assertEqual(runner.kwargs[0]["max_output_chars"], 20_000)
        self.assertIn("$ df -h", stderr.getvalue())
        self.assertIn("reason: Check disk usage", stderr.getvalue())
        self.assertIn("Command: df -h", client.calls[1][-1]["content"])
        self.assertIn("STDOUT:\ndisk ok", client.calls[1][-1]["content"])

    def test_agent_does_not_stream_command_blocks(self) -> None:
        client = ChunkyClient(
            [
                ["<com", "mand>\nreason: Check disk usage\nrun: df -h\n</command>"],
                ["Disk ", "usage looks fine."],
            ]
        )
        runner = FakeRunner()
        streamed: list[str] = []

        answer = run_agent(
            request="check disk",
            client=client,
            model="test-model",
            system_prompt="system prompt",
            command_runner=runner,
            stream_output=streamed.append,
            stderr=io.StringIO(),
        )

        self.assertEqual(answer, "Disk usage looks fine.")
        self.assertEqual(streamed, ["Disk ", "usage looks fine."])
        self.assertEqual(runner.commands, ["df -h"])

    def test_agent_replaces_provider_safety_refusal_with_command_output(self) -> None:
        client = ChunkyClient(
            [
                ["<command>\nreason: Find large files\nrun: find / -type f | head\n</command>"],
                ["User Safety: unsafe\n", "Response Safety: unsafe\nSafety Categories: PII/Privacy"],
            ]
        )
        runner = FakeRunner(
            ShellResult(
                command="",
                exit_code=0,
                duration_seconds=0.03,
                stdout="10G /Users/me/large-file.mov\n",
            )
        )
        streamed: list[str] = []

        answer = run_agent(
            request="find the biggest files",
            client=client,
            model="test-model",
            system_prompt="system prompt",
            command_runner=runner,
            stream_output=streamed.append,
            stderr=io.StringIO(),
        )

        self.assertIn("The command ran", answer)
        self.assertIn("10G /Users/me/large-file.mov", answer)
        self.assertNotIn("User Safety: unsafe", answer)
        self.assertEqual("".join(streamed), answer)

    def test_agent_updates_status_while_thinking_and_running_commands(self) -> None:
        client = FakeClient(
            [
                "<command>\nreason: Check disk usage\nrun: df -h\n</command>",
                "Disk usage looks fine.",
            ]
        )
        runner = FakeRunner()
        status = FakeStatus()

        answer = run_agent(
            request="check disk",
            client=client,
            model="test-model",
            system_prompt="system prompt",
            command_runner=runner,
            status=status,
            stderr=io.StringIO(),
        )

        self.assertEqual(answer, "Disk usage looks fine.")
        self.assertEqual(
            status.events,
            [
                ("start", "thinking"),
                ("stop", None),
                ("start", "running: df -h"),
                ("stop", None),
                ("start", "reading command output"),
                ("stop", None),
            ],
        )

    def test_agent_asks_for_normal_answer_after_malformed_command_block(self) -> None:
        client = FakeClient(["<command>\nrun: pwd\n</command>", "Here is a normal answer."])
        runner = FakeRunner()

        answer = run_agent(
            request="where am I",
            client=client,
            model="test-model",
            system_prompt="system prompt",
            command_runner=runner,
        )

        self.assertEqual(answer, "Here is a normal answer.")
        self.assertEqual(runner.commands, [])
        self.assertIn("did not match hey's command protocol", client.calls[1][-1]["content"])

    def test_risky_command_is_cancelled_when_user_declines_confirmation(self) -> None:
        client = FakeClient(
            [
                "<command>\nreason: Remove a file\nrun: rm old-file.txt\n</command>",
                "I did not remove it.",
            ]
        )
        runner = FakeRunner()
        stderr = io.StringIO()

        answer = run_agent(
            request="clean up old file",
            client=client,
            model="test-model",
            system_prompt="system prompt",
            command_runner=runner,
            confirm=lambda command: False,
            stderr=stderr,
        )

        self.assertEqual(answer, "I did not remove it.")
        self.assertEqual(runner.commands, [])
        self.assertIn("cancelled", stderr.getvalue())
        self.assertIn("Exit code: 130", client.calls[1][-1]["content"])

    def test_dangerous_command_runs_after_confirmation(self) -> None:
        client = FakeClient(
            [
                "<command>\nreason: Read a key\nrun: cat ~/.ssh/id_rsa\n</command>",
                "Here is the requested key.",
            ]
        )
        runner = FakeRunner()
        confirmations: list[str] = []
        stderr = io.StringIO()

        answer = run_agent(
            request="show my ssh key",
            client=client,
            model="test-model",
            system_prompt="system prompt",
            command_runner=runner,
            confirm=lambda command: confirmations.append(command) or True,
            stderr=stderr,
        )

        self.assertEqual(answer, "Here is the requested key.")
        self.assertEqual(runner.commands, ["cat ~/.ssh/id_rsa"])
        self.assertEqual(confirmations, ["cat ~/.ssh/id_rsa"])
        self.assertNotIn("blocked", stderr.getvalue())
        self.assertIn("Command: cat ~/.ssh/id_rsa", client.calls[1][-1]["content"])


if __name__ == "__main__":
    unittest.main()
