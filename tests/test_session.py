from __future__ import annotations

import json
import stat
import tempfile
import unittest
from pathlib import Path

from hey.session import (
    SESSION_TIMEOUT_SECONDS,
    clear_session,
    load_session_messages,
    save_session_messages,
    session_path,
)


class SessionTests(unittest.TestCase):
    def test_save_and_load_session_messages(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config"
            messages = [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi"},
            ]

            save_session_messages(messages, config_path, now=100)
            loaded = load_session_messages(config_path, now=120)

            path = session_path(config_path)
            self.assertEqual(loaded, messages)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_session_expires_after_ten_minutes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config"
            messages = [{"role": "user", "content": "old question"}]

            save_session_messages(messages, config_path, now=100)
            loaded = load_session_messages(config_path, now=100 + SESSION_TIMEOUT_SECONDS + 1)

            self.assertEqual(loaded, [])

    def test_invalid_session_file_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config"
            session_path(config_path).write_text("not json", encoding="utf-8")

            self.assertEqual(load_session_messages(config_path), [])

    def test_only_user_and_assistant_messages_are_kept(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config"
            path = session_path(config_path)
            path.write_text(
                json.dumps(
                    {
                        "updated_at": 100,
                        "messages": [
                            {"role": "system", "content": "skip me"},
                            {"role": "user", "content": "keep me"},
                            {"role": "assistant", "content": "keep me too"},
                            {"role": "user", "content": 123},
                        ],
                    }
                ),
                encoding="utf-8",
            )

            self.assertEqual(
                load_session_messages(config_path, now=101),
                [
                    {"role": "user", "content": "keep me"},
                    {"role": "assistant", "content": "keep me too"},
                ],
            )

    def test_clear_session_removes_saved_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config"
            save_session_messages([{"role": "user", "content": "hello"}], config_path)

            self.assertTrue(clear_session(config_path))
            self.assertFalse(session_path(config_path).exists())
            self.assertFalse(clear_session(config_path))


if __name__ == "__main__":
    unittest.main()
