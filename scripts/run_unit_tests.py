"""Run unit tests with in-process DNS and socket connections disabled.

Real model/device integration belongs in the explicit evaluation scripts, not
this runner. Child processes are outside this in-process guard.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack, contextmanager
from pathlib import Path
import socket
import sys
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def network_disabled():
    attempts: list[str] = []

    def deny(operation):
        def blocked(*args, **kwargs):
            attempts.append(operation)
            raise AssertionError("Network access is disabled in unit tests; use a fake client")
        return blocked

    with ExitStack() as stack:
        for target, name in (
            (socket, "getaddrinfo"),
            (socket, "create_connection"),
            (socket.socket, "connect"),
            (socket.socket, "connect_ex"),
        ):
            stack.enter_context(patch.object(target, name, deny(name)))
        yield attempts


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pattern", default="test_*.py")
    parser.add_argument("--verbosity", type=int, choices=(0, 1, 2), default=1)
    args = parser.parse_args(argv)
    for path in (ROOT, ROOT / "src"):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))
    # Unit tests always use the sandbox backend, independently of the caller's
    # configured real Home Assistant environment.
    with (
        patch.dict("os.environ", {"EDGE_SMART_HOME_BACKEND": "mock"}),
        network_disabled() as attempts,
    ):
        suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern=args.pattern)
        result = unittest.TextTestRunner(verbosity=args.verbosity).run(suite)
    if attempts:
        print(f"Unit-test network guard blocked {len(attempts)} attempt(s); suite rejected.", file=sys.stderr)
    else:
        print("Unit-test network guard: 0 attempts.", file=sys.stderr)
    return 0 if result.wasSuccessful() and not attempts else 1


if __name__ == "__main__":
    raise SystemExit(main())
