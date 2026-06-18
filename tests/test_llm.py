from __future__ import annotations

import json
import unittest
from unittest.mock import patch
from typing import Any

from hey.llm import OpenAICompatibleClient


class FakeResponse:
    def __init__(self, body: bytes, *, content_type: str = "application/json") -> None:
        self._body = body
        self._lines = body.splitlines(keepends=True)
        self._index = 0
        self.headers = FakeHeaders(content_type)

    def read(self) -> bytes:
        return self._body

    def readline(self) -> bytes:
        if self._index >= len(self._lines):
            return b""
        line = self._lines[self._index]
        self._index += 1
        return line

    def close(self) -> None:
        return None


class FakeHeaders:
    def __init__(self, content_type: str) -> None:
        self._content_type = content_type

    def get_content_type(self) -> str:
        return self._content_type


class LlmTests(unittest.TestCase):
    def test_stream_messages_sends_existing_message_history(self) -> None:
        client = OpenAICompatibleClient(
            base_url="https://api.example.com/v1",
            api_key="test-key",
        )

        captured: dict[str, Any] = {}

        def fake_urlopen(request: Any, timeout: float) -> FakeResponse:
            captured["body"] = json.loads(request.data.decode("utf-8"))
            response = {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "final answer",
                        }
                    }
                ]
            }
            return FakeResponse(json.dumps(response).encode("utf-8"), content_type="application/json")

        messages = [
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "first request"},
            {"role": "assistant", "content": "<command>\nreason: inspect\nrun: pwd\n</command>"},
            {"role": "user", "content": "Command: pwd\nExit code: 0"},
        ]

        with patch("urllib.request.urlopen", fake_urlopen):
            chunks = list(client.stream_messages(messages=messages, model="test-model"))

        self.assertEqual(chunks, ["final answer"])
        self.assertEqual(captured["body"]["messages"], messages)
        self.assertTrue(captured["body"]["stream"])

    def test_stream_chat_sends_stream_request_and_yields_chunks(self) -> None:
        client = OpenAICompatibleClient(
            base_url="https://api.example.com/v1",
            api_key="test-key",
        )

        captured: dict[str, Any] = {}

        def fake_urlopen(request: Any, timeout: float) -> FakeResponse:
            captured["url"] = request.full_url
            captured["headers"] = dict(request.header_items())
            captured["body"] = json.loads(request.data.decode("utf-8"))
            captured["timeout"] = timeout
            sse_body = (
                b": OPENROUTER PROCESSING\n\n"
                b'data: {"choices":[{"delta":{"role":"assistant","content":""},"finish_reason":null}]}\n\n'
                b'data: {"choices":[{"delta":{"content":"hello "},"finish_reason":null}]}\n\n'
                b'data: {"choices":[{"delta":{"content":"world"},"finish_reason":null}]}\n\n'
                b"data: [DONE]\n\n"
            )
            return FakeResponse(sse_body, content_type="text/event-stream")

        with patch("urllib.request.urlopen", fake_urlopen):
            chunks = list(
                client.stream_chat(
                    query="say hello",
                    model="test-model",
                    system_prompt="you are hey",
                )
            )

        self.assertEqual(chunks, ["hello ", "world"])
        self.assertEqual(captured["url"], "https://api.example.com/v1/chat/completions")
        self.assertEqual(captured["headers"]["Authorization"], "Bearer test-key")
        self.assertEqual(captured["headers"]["Accept"], "text/event-stream")
        self.assertEqual(captured["headers"]["Content-type"], "application/json")
        self.assertEqual(captured["body"]["model"], "test-model")
        self.assertEqual(
            captured["body"]["messages"],
            [
                {"role": "system", "content": "you are hey"},
                {"role": "user", "content": "say hello"},
            ],
        )
        self.assertTrue(captured["body"]["stream"])

    def test_stream_chat_falls_back_when_provider_returns_json(self) -> None:
        client = OpenAICompatibleClient(
            base_url="https://api.example.com/v1",
            api_key="test-key",
        )

        captured: dict[str, Any] = {}

        def fake_urlopen(request: Any, timeout: float) -> FakeResponse:
            captured["headers"] = dict(request.header_items())
            captured["body"] = json.loads(request.data.decode("utf-8"))
            response = {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "fallback json response",
                        }
                    }
                ]
            }
            return FakeResponse(json.dumps(response).encode("utf-8"), content_type="application/json")

        with patch("urllib.request.urlopen", fake_urlopen):
            chunks = list(client.stream_chat(query="say hello", model="test-model"))

        self.assertEqual(chunks, ["fallback json response"])
        self.assertEqual(captured["headers"]["Accept"], "text/event-stream")
        self.assertTrue(captured["body"]["stream"])
        self.assertEqual(
            captured["body"]["messages"],
            [{"role": "user", "content": "say hello"}],
        )


if __name__ == "__main__":
    unittest.main()
