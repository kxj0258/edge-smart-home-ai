from __future__ import annotations

import re
from typing import Any, Mapping

from edge_smart_home.common import DeviceCommand, PerceptionEvent


class UnsupportedCommandError(ValueError):
    """The deterministic router cannot safely parse an event."""


class FastPathRouter:
    def __init__(
        self,
        devices: Mapping[str, Mapping[str, Any]],
        intents: Mapping[str, list[str]],
        default_devices: Mapping[str, str] | None = None,
    ) -> None:
        self._devices = devices
        self._intents = intents
        self._default_devices = dict(default_devices or {})

    def route(self, event: PerceptionEvent) -> DeviceCommand:
        text = self._normalize(event.label)
        device_id = self._match_device(text)
        capabilities = set(self._devices[device_id]["capabilities"])
        action, arguments = self._match_action(text, device_id, capabilities)
        return DeviceCommand(
            device_id=device_id,
            action=action,
            arguments=arguments,
            source=event.source,
            confidence=event.confidence,
            request_id=event.event_id,
        )

    @staticmethod
    def _normalize(text: str) -> str:
        return re.sub(r"[\s，。！？,.!?]", "", text)

    def _match_device(self, text: str) -> str:
        matches = {
            device_id
            for device_id, config in self._devices.items()
            if any(alias in text for alias in config["aliases"])
        }
        if len(matches) == 1:
            return matches.pop()
        if len(matches) > 1:
            raise UnsupportedCommandError("command identifies more than one device")

        default_matches = {
            device_id
            for action, device_id in self._default_devices.items()
            if any(phrase in text for phrase in self._intents.get(action, []))
        }
        if len(default_matches) == 1:
            return default_matches.pop()
        raise UnsupportedCommandError("command must identify exactly one device")

    def _match_action(
        self, text: str, device_id: str, capabilities: set[str]
    ) -> tuple[str, dict[str, Any]]:
        if "set_temperature" in capabilities:
            match = re.search(r"(?:设置|调到|调至|空调)?(1[6-9]|2[0-9]|30)(?:度|℃)", text)
            if match:
                return "set_temperature", {"temperature": int(match.group(1))}

        action_order = ["query_state", "volume_up", "volume_down"]
        if device_id == "living_room_curtain":
            action_order.extend(["open", "close"])
        else:
            action_order.extend(["turn_on", "turn_off"])

        for action in action_order:
            if action in capabilities and any(
                phrase in text for phrase in self._intents.get(action, [])
            ):
                return action, {}
        raise UnsupportedCommandError("no deterministic action rule matched")
