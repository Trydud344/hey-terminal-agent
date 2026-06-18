from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Iterator


class LlmError(Exception):
    """Raised when the LLM provider request fails."""


@dataclass(frozen=True)
class OpenAICompatibleClient:
    base_url: str
    api_key: str
    timeout_seconds: float = 60.0

    def stream_chat(
        self,
        *,
        query: str,
        model: str,
        system_prompt: str | None = None,
        debug: Callable[[str], None] | None = None,
    ) -> Iterator[str]:
        messages = _chat_messages(query=query, system_prompt=system_prompt)
        yield from self.stream_messages(messages=messages, model=model, debug=debug)

    def stream_messages(
        self,
        *,
        messages: list[dict[str, str]],
        model: str,
        debug: Callable[[str], None] | None = None,
    ) -> Iterator[str]:
        try:
            response = self._open_request(messages=messages, model=model)
            yield from _iter_streaming_chunks(response, debug=debug)
        except urllib.error.HTTPError as exc:
            raise LlmError(_format_http_error(exc)) from exc
        except urllib.error.URLError as exc:
            raise LlmError(str(exc.reason)) from exc
        except TimeoutError as exc:
            raise LlmError("request timed out") from exc

    def _chat_completions_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/chat/completions"

    def _open_request(self, *, messages: list[dict[str, str]], model: str):
        data = json.dumps(_chat_payload(messages=messages, model=model)).encode("utf-8")
        request = urllib.request.Request(
            self._chat_completions_url(),
            data=data,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Accept": "text/event-stream",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        return urllib.request.urlopen(request, timeout=self.timeout_seconds)


def _chat_messages(*, query: str, system_prompt: str | None) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": query})
    return messages


def _chat_payload(*, messages: list[dict[str, str]], model: str) -> dict[str, Any]:
    return {
        "model": model,
        "stream": True,
        "messages": messages,
    }


def _decode_json(body: str) -> dict[str, Any]:
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise LlmError(f"provider returned invalid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise LlmError("provider returned a non-object JSON response")
    return data


def _extract_message_content(data: dict[str, Any]) -> str:
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise LlmError("provider response did not include choices")

    first_choice = choices[0]
    if not isinstance(first_choice, dict):
        raise LlmError("provider response choice was not an object")

    message = first_choice.get("message")
    if not isinstance(message, dict):
        raise LlmError("provider response choice did not include a message")

    content = _text_from_content(message.get("content"))
    if content:
        return content

    raise LlmError("provider response message did not include text content")


def _iter_streaming_chunks(
    response: Any,
    *,
    debug: Callable[[str], None] | None = None,
) -> Iterator[str]:
    try:
        content_type = _response_content_type(response)
        if debug:
            debug(f"response content-type: {content_type or 'unknown'}")

        if content_type and "text/event-stream" not in content_type:
            if debug:
                debug("provider returned a non-SSE response; reading it as one JSON message")
            yield from _iter_json_response(response)
        else:
            yield from _iter_sse_or_json_lines(response, debug=debug)
    except TimeoutError as exc:
        raise LlmError("stream timed out while waiting for provider output") from exc
    finally:
        response.close()


def _iter_json_response(response: Any) -> Iterator[str]:
    body = response.read().decode("utf-8").strip()
    if body:
        yield _extract_message_content(_decode_json(body))


def _iter_sse_or_json_lines(
    response: Any,
    *,
    debug: Callable[[str], None] | None = None,
) -> Iterator[str]:
    data_lines: list[str] = []
    fallback_json_lines: list[str] = []
    saw_sse = False

    while raw_line := response.readline():
        line = raw_line.decode("utf-8").strip()
        if not line:
            if not data_lines:
                continue
            saw_sse = True
            done, chunk = _parse_sse_event(data_lines)
            data_lines = []
            if done:
                return
            if chunk:
                yield chunk
            continue

        if line.startswith(":"):
            if debug:
                debug(f"sse comment: {line}")
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
        else:
            fallback_json_lines.append(line)

    if data_lines:
        saw_sse = True
        done, chunk = _parse_sse_event(data_lines)
        if done:
            return
        if chunk:
            yield chunk

    if not saw_sse and fallback_json_lines:
        yield _extract_message_content(_decode_json("\n".join(fallback_json_lines)))


def _parse_sse_event(data_lines: list[str]) -> tuple[bool, str]:
    payload = "\n".join(data_lines).strip()
    if not payload:
        return False, ""
    if payload == "[DONE]":
        return True, ""

    data = _decode_json(payload)
    error = _extract_error_message_from_data(data)
    if error:
        raise LlmError(error)

    return False, _extract_delta_content(data)


def _extract_delta_content(data: dict[str, Any]) -> str:
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""

    first_choice = choices[0]
    if not isinstance(first_choice, dict):
        return ""

    delta = first_choice.get("delta")
    if not isinstance(delta, dict):
        return ""

    content = _text_from_content(delta.get("content"))
    return content or ""


def _text_from_content(content: Any) -> str | None:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") in {"text", "output_text"}
        )
    return None


def _response_content_type(response: Any) -> str:
    headers = getattr(response, "headers", None)
    if headers is None:
        return ""

    get_content_type = getattr(headers, "get_content_type", None)
    if callable(get_content_type):
        return str(get_content_type()).split(";", 1)[0].strip().lower()

    if hasattr(headers, "get"):
        value = headers.get("Content-Type", "")
        return str(value).split(";", 1)[0].strip().lower()

    return ""


def _format_http_error(exc: urllib.error.HTTPError) -> str:
    body = exc.read().decode("utf-8", errors="replace")
    message = _extract_error_message(body)
    if message:
        return f"HTTP {exc.code}: {message}"
    if body:
        return f"HTTP {exc.code}: {body}"
    return f"HTTP {exc.code}: {exc.reason}"


def _extract_error_message(body: str) -> str | None:
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return None

    return _extract_error_message_from_data(data)


def _extract_error_message_from_data(data: Any) -> str | None:
    if not isinstance(data, dict):
        return None

    error = data.get("error")
    if isinstance(error, dict) and isinstance(error.get("message"), str):
        return error["message"]
    if isinstance(error, str):
        return error

    choices = data.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        choice_error = choices[0].get("error")
        if isinstance(choice_error, dict) and isinstance(choice_error.get("message"), str):
            return choice_error["message"]

    return None
