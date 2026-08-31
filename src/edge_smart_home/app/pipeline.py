from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from edge_smart_home.common import DeviceState, EventSource, PerceptionEvent
from edge_smart_home.device import MockDeviceBackend
from edge_smart_home.router import FastPathRouter


class TextControlPipeline:
    def __init__(self, router: FastPathRouter, backend: MockDeviceBackend) -> None:
        self.router = router
        self.backend = backend

    @classmethod
    def from_config_dir(cls, config_dir: Path) -> "TextControlPipeline":
        devices = _load_json(config_dir / "devices.json")
        intents = _load_json(config_dir / "intents.json")
        routing = _load_json(config_dir / "routing.json")
        return cls(
            FastPathRouter(devices, intents, routing.get("default_devices", {})),
            MockDeviceBackend(devices),
        )

    def handle_text(self, text: str) -> DeviceState:
        event = PerceptionEvent(source=EventSource.TEXT, label=text)
        return self.handle_event(event)

    def handle_event(self, event: PerceptionEvent) -> DeviceState:
        return self.backend.execute(self.router.route(event))


def _load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as file:
        value = json.load(file)
    if not isinstance(value, dict):
        raise ValueError(f"configuration root must be an object: {path}")
    return value
