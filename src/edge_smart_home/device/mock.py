from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from edge_smart_home.common import DeviceCommand, DeviceState
from edge_smart_home.common.schemas import utc_now

from .base import DeviceBackend, DeviceBackendError


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
        initialized_at = utc_now()
        self._updated_at = {device_id: initialized_at for device_id in self._states}

    def execute(self, command: DeviceCommand) -> DeviceState:
        self._require_device(command.device_id)
        if command.action not in self._capabilities[command.device_id]:
            raise DeviceBackendError(
                f"{command.device_id} does not support {command.action}"
            )

        state = self._states[command.device_id]
        previous_state = deepcopy(state)
        if command.action == "turn_on":
            state["power"] = True
        elif command.action == "turn_off":
            state["power"] = False
        elif command.action == "volume_up":
            state["volume"] = min(100, int(state["volume"]) + 5)
        elif command.action == "volume_down":
            state["volume"] = max(0, int(state["volume"]) - 5)
        elif command.action == "set_temperature":
            temperature = command.arguments.get("temperature")
            if not isinstance(temperature, int) or not 16 <= temperature <= 30:
                raise DeviceBackendError("temperature must be an integer from 16 to 30")
            state["temperature"] = temperature
            state["power"] = True
        elif command.action == "open":
            state["position"] = "open"
        elif command.action == "close":
            state["position"] = "closed"
        elif command.action != "query_state":
            raise DeviceBackendError(f"unsupported action: {command.action}")
        if state != previous_state:
            self._updated_at[command.device_id] = utc_now()
        return self.get_state(command.device_id)

    def get_state(self, device_id: str) -> DeviceState:
        self._require_device(device_id)
        return DeviceState(
            device_id,
            True,
            deepcopy(self._states[device_id]),
            updated_at=self._updated_at[device_id],
        )

    def list_states(self) -> tuple[DeviceState, ...]:
        return tuple(self.get_state(device_id) for device_id in self._states)

    def _require_device(self, device_id: str) -> None:
        if device_id not in self._states:
            raise DeviceBackendError(f"unknown device: {device_id}")
