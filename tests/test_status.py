from __future__ import annotations

import io
import unittest

from hey.status import StatusSpinner


class StatusTests(unittest.TestCase):
    def test_spinner_stays_quiet_when_stream_is_not_a_terminal(self) -> None:
        stream = io.StringIO()
        spinner = StatusSpinner(stream)

        spinner.start("thinking")
        spinner.update("running")
        spinner.stop()

        self.assertEqual(stream.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
