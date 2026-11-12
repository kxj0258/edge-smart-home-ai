from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from edge_smart_home.common import DeviceCommand, DeviceState
from edge_smart_home.common.schemas import utc_now

from .base import DeviceBackend, DeviceBackendError
from .validation import validate_action_arguments


class MockDeviceBackend(DeviceBackend):
    def __init__(self, device_config: Mapping[str, Mapping[str, Any]]) -> None:
        self._capabilities = {
            device_id: frozenset(config["capabilities"])
            for device_id, config in device_config.items()
        }
        self._states = {
            device_id: deepcopy(config["initial_state"])
            for device_id, config in device_config.items()
        }
        self._available = {}
        for device_id, state in self._states.items():
            online = state.get("online", True)
            if type(online) is not bool:
                raise DeviceBackendError("initial online value must be boolean", error_code="INVALID_ARGUMENTS")
            self._available[device_id] = online
        initialized_at = utc_now()
        self._updated_at = {device_id: initialized_at for device_id in self._states}

    def execute(self, command: DeviceCommand) -> DeviceState:
        self._require_device(command.device_id)
        if command.action not in self._capabilities[command.device_id]:
            raise DeviceBackendError(
                f"{command.device_id} does not support {command.action}",
                error_code="UNSUPPORTED_ACTION",
            )
        validate_action_arguments(command.action, command.arguments)
        if not self._available[command.device_id]:
            raise DeviceBackendError(f"device is offline: {command.device_id}", error_code="DEVICE_OFFLINE")

        # Work on one detached candidate. Any validation/state conflict leaves
        # both the published attributes and their last-change clock untouched.
        previous_state = self._states[command.device_id]
        state = deepcopy(previous_state)
        if command.action == "turn_on":
            state["power"] = True
            if "brightness" in state and state["brightness"] == 0:
                state["brightness"] = 100
            if "power_watts" in state:
                state["power_watts"] = 12.5
        elif command.action == "turn_off":
            state["power"] = False
            if "brightness" in state:
                state["brightness"] = 0
            if "playback" in state:
                state["playback"] = "stopped"
            if "power_watts" in state:
                state["power_watts"] = 0.0
        elif command.action == "volume_up":
            state["volume"] = min(100, int(state["volume"]) + 5)
        elif command.action == "volume_down":
            state["volume"] = max(0, int(state["volume"]) - 5)
        elif command.action == "set_volume":
            state["volume"] = command.arguments["volume"]
        elif command.action == "set_brightness":
            brightness = command.arguments["brightness"]
            state["brightness"] = brightness
            state["power"] = brightness > 0
        elif command.action == "set_temperature":
            state["temperature"] = command.arguments["temperature"]
            state["power"] = True
        elif command.action == "set_hvac_mode":
            state["power"] = True
            state["hvac_mode"] = command.arguments["mode"]
        elif command.action == "open":
            state["position"] = "open"
            if "position_percent" in state:
                state["position_percent"] = 100
        elif command.action == "close":
            state["position"] = "closed"
            if "position_percent" in state:
                state["position_percent"] = 0
        elif command.action == "set_position":
            position = command.arguments["position"]
            state["position_percent"] = position
            state["position"] = (
                "closed" if position == 0 else "open" if position == 100 else "partial"
            )
        elif command.action == "enable_guest_wifi":
            state["guest_wifi_enabled"] = True
        elif command.action == "disable_guest_wifi":
            state["guest_wifi_enabled"] = False
        elif command.action == "restart":
            state["restart_count"] = int(state["restart_count"]) + 1
            state["last_restart_at"] = utc_now().isoformat()
        elif command.action == "play":
            state["power"] = True
            state["playback"] = "playing"
        elif command.action == "pause":
            # Pausing an off/stopped Mock player is an accepted no-op, not a
            # fictional powered-off player entering an active paused state.
            if state.get("power") and state.get("playback") == "playing":
                state["playback"] = "paused"
        elif command.action == "resume":
            state["power"] = True
            state["playback"] = "playing"
        elif command.action == "home":
            state["power"] = True
            state["app"] = "home"
            state["playback"] = "stopped"
        elif command.action in {"next", "previous"}:
            state["power"] = True
            state["playback"] = "playing"
            state["position_seconds"] = 0
        elif command.action == "open_app":
            state["power"] = True
            state["app"] = command.arguments["app"]
        elif command.action == "set_channel":
            state["power"] = True
            state["app"] = "IPTV"
            state["channel"] = command.arguments["channel"]
            state["playback"] = "playing"
        elif command.action == "enable_privacy":
            state["privacy_mode"] = True
            state["recording"] = False
        elif command.action == "disable_privacy":
            state["privacy_mode"] = False
        elif command.action == "start_recording":
            if state["privacy_mode"]:
                raise DeviceBackendError("recording is unavailable in privacy mode", error_code="DEVICE_STATE_CONFLICT")
            state["recording"] = True
        elif command.action == "stop_recording":
            state["recording"] = False
        elif command.action == "lock":
            state["locked"] = True
        elif command.action == "unlock":
            state["locked"] = False
        elif command.action == "show_feed":
            if state["privacy_mode"]:
                raise DeviceBackendError("camera feed is unavailable in privacy mode", error_code="DEVICE_STATE_CONFLICT")
        elif command.action == "speed_up":
            state["power"] = True
            state["speed"] = min(3, int(state["speed"]) + 1)
        elif command.action == "speed_down":
            state["speed"] = max(1, int(state["speed"]) - 1)
        elif command.action == "set_speed":
            state["power"] = True
            state["speed"] = command.arguments["speed"]
        elif command.action == "set_mode":
            state["power"] = True
            state["mode"] = command.arguments["mode"]
        elif command.action != "query_state":
            raise DeviceBackendError(f"unsupported action: {command.action}", error_code="UNSUPPORTED_ACTION")
        if state != previous_state:
            changed_at = utc_now()
            self._states[command.device_id] = state
            self._updated_at[command.device_id] = changed_at
        return self.get_state(command.device_id)

    def get_state(self, device_id: str) -> DeviceState:
        self._require_device(device_id)
        return DeviceState(
            device_id,
            self._available[device_id],
            deepcopy(self._states[device_id]),
            updated_at=self._updated_at[device_id],
            backend_kind="mock",
            completion_scope="mock_state_snapshot",
        )

    def set_available(self, device_id: str, available: bool) -> DeviceState:
        """Simulate transport availability without executing a device action.

        Offline reads expose last-known attributes, not a fresh device query.
        Availability alone does not update the attribute clock. Cameras have
        an explicit ``online`` attribute, so changing it does advance that clock.
        """
        self._require_device(device_id)
        if type(available) is not bool:
            raise DeviceBackendError("availability must be boolean", error_code="INVALID_ARGUMENTS")
        if "online" in self._states[device_id] and self._states[device_id]["online"] != available:
            state = deepcopy(self._states[device_id])
            state["online"] = available
            changed_at = utc_now()
            self._states[device_id] = state
            self._updated_at[device_id] = changed_at
        self._available[device_id] = available
        return self.get_state(device_id)

    def list_states(self) -> tuple[DeviceState, ...]:
        return tuple(self.get_state(device_id) for device_id in self._states)

    def _require_device(self, device_id: str) -> None:
        if device_id not in self._states:
            raise DeviceBackendError(f"unknown device: {device_id}", error_code="UNKNOWN_DEVICE")
