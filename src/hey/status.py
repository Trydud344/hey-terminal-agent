from __future__ import annotations

import threading
from typing import TextIO


class StatusSpinner:
    def __init__(self, stream: TextIO, *, interval_seconds: float = 0.12) -> None:
        self.stream = stream
        self.interval_seconds = interval_seconds
        self.enabled = bool(getattr(stream, "isatty", lambda: False)())
        self._message = ""
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

    def start(self, message: str) -> None:
        if not self.enabled:
            return

        self.stop()
        with self._lock:
            self._message = message
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._animate, daemon=True)
        self._thread.start()

    def update(self, message: str) -> None:
        if not self.enabled:
            return

        with self._lock:
            self._message = message

    def stop(self) -> None:
        if not self.enabled or self._thread is None:
            return

        self._stop_event.set()
        self._thread.join(timeout=1)
        self._thread = None
        self._clear_line()

    def _animate(self) -> None:
        frames = ("-", "\\", "|", "/")
        index = 0

        while not self._stop_event.is_set():
            with self._lock:
                message = self._message

            frame = frames[index % len(frames)]
            self.stream.write(f"\r\033[2K\033[36mhey\033[0m {frame} {message}")
            self.stream.flush()
            index += 1
            self._stop_event.wait(self.interval_seconds)

    def _clear_line(self) -> None:
        self.stream.write("\r\033[2K")
        self.stream.flush()
