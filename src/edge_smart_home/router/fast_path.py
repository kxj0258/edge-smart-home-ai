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
        matched_aliases = {
            device_id: max(
                (len(alias) for alias in config["aliases"] if alias in text),
                default=0,
            )
            for device_id, config in self._devices.items()
        }
        longest = max(matched_aliases.values(), default=0)
        matches = {
            device_id
            for device_id, alias_length in matched_aliases.items()
            if alias_length == longest and alias_length > 0
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

        if "set_channel" in capabilities:
            match = re.search(r"(?:切换到|换到|播放)(CCTV-?\d+)", text, re.IGNORECASE)
            if match:
                return "set_channel", {"channel": match.group(1).upper()}

        if "set_mode" in capabilities:
            modes = {"自动模式": "auto", "睡眠模式": "sleep", "强力模式": "turbo"}
            for phrase, mode in modes.items():
                if phrase in text:
                    return "set_mode", {"mode": mode}

        action_order = [
            "enable_guest_wifi", "disable_guest_wifi", "restart",
            "enable_privacy", "disable_privacy", "start_recording", "stop_recording",
            "show_feed", "resume", "pause", "next", "previous", "home", "open_app",
            "volume_up", "volume_down", "speed_up", "speed_down",
            "query_state", "open", "close", "turn_on", "turn_off", "play",
        ]

        for action in action_order:
            if action in capabilities and any(
                phrase in text for phrase in self._intents.get(action, [])
            ):
                return action, {}
        raise UnsupportedCommandError("no deterministic action rule matched")
