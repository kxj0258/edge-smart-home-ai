from __future__ import annotations

from time import monotonic
from typing import Any

from edge_smart_home.common import DeviceCommand, EventSource


class DuplicateCommandError(RuntimeError):
    """A repeated voice command was suppressed within the cooldown window."""


class CommandDeduplicator:
    def __init__(self, window_seconds: float, exempt_actions: set[str] | None = None) -> None:
        self.window_seconds = window_seconds
        self.exempt_actions = exempt_actions or {"query_state", "show_feed"}
        self._last_seen: dict[tuple[Any, ...], float] = {}

    def check(self, command: DeviceCommand) -> None:
        if command.source != EventSource.VOICE or command.action in self.exempt_actions:
            return
        key = (
            command.device_id,
            command.action,
            tuple(sorted(command.arguments.items())),
        )
        now = monotonic()
        previous = self._last_seen.get(key)
        self._last_seen[key] = now
        if previous is not None and now - previous < self.window_seconds:
            raise DuplicateCommandError(
                f"duplicate command suppressed for {command.device_id}.{command.action}"
            )
