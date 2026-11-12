from __future__ import annotations

import importlib.util
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
import socket
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("unit_test_runner", ROOT / "scripts/run_unit_tests.py")
runner = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(runner)


class UnitTestRunnerTest(unittest.TestCase):
    def test_network_guard_records_attempts_even_when_client_catches_errors(self):
        with runner.network_disabled() as attempts, socket.socket() as client:
            calls = (
                lambda: socket.getaddrinfo("example.invalid", 80),
                lambda: socket.create_connection(("127.0.0.1", 11434)),
                lambda: client.connect(("127.0.0.1", 11434)),
                lambda: client.connect_ex(("127.0.0.1", 11434)),
            )
            for call in calls:
                try:
                    call()
                except AssertionError:
                    pass
            self.assertEqual(attempts, ["getaddrinfo", "create_connection", "connect", "connect_ex"])

    def test_runner_rejects_successful_suite_that_catches_network_error(self):
        class CatchingClientTest(unittest.TestCase):
            def runTest(self):
                try:
                    socket.create_connection(("127.0.0.1", 11434))
                except AssertionError:
                    pass

        output = StringIO()
        suite = unittest.TestSuite([CatchingClientTest()])
        with patch.object(runner.unittest.defaultTestLoader, "discover", return_value=suite), redirect_stderr(output):
            exit_code = runner.main(["--verbosity", "0"])
        self.assertEqual(exit_code, 1)
        self.assertIn("suite rejected", output.getvalue())


if __name__ == "__main__":
    unittest.main()
