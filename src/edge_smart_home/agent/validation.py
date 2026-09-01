from __future__ import annotations

from typing import Any, Mapping, Sequence

from .interfaces import PlannedDeviceCommand


class AgentPlanValidationError(ValueError):
    """An Agent plan violates the configured device/action contract."""


class AgentPlanValidator:
    def __init__(
        self, devices: Mapping[str, Mapping[str, Any]], max_commands: int = 8
    ) -> None:
        if max_commands <= 0:
            raise ValueError("max_commands must be positive")
        self.devices = devices
        self.max_commands = max_commands

    def validate(self, commands: Sequence[PlannedDeviceCommand]) -> None:
        if not commands or len(commands) > self.max_commands:
            raise AgentPlanValidationError(
                f"agent plan must contain 1 to {self.max_commands} commands"
            )
        for command in commands:
            device = self.devices.get(command.device_id)
            if device is None:
                raise AgentPlanValidationError(
                    f"agent plan references unknown device: {command.device_id}"
                )
            if command.action not in device.get("capabilities", []):
                raise AgentPlanValidationError(
                    f"agent action is not allowed: {command.device_id}.{command.action}"
                )
            self._validate_arguments(command)

    @staticmethod
    def _validate_arguments(command: PlannedDeviceCommand) -> None:
        arguments = dict(command.arguments)
        rules = {
            "set_volume": ("volume", int, 0, 100),
            "set_temperature": ("temperature", int, 16, 30),
        }
        if command.action in rules:
            name, value_type, minimum, maximum = rules[command.action]
            value = arguments.pop(name, None)
            if type(value) is not value_type or not minimum <= value <= maximum:
                raise AgentPlanValidationError(
                    f"invalid agent argument for {command.action}: {name}"
                )
        elif command.action in {"set_channel", "open_app", "set_mode"}:
            names = {"set_channel": "channel", "open_app": "app", "set_mode": "mode"}
            name = names[command.action]
            value = arguments.pop(name, None)
            if not isinstance(value, str) or not value:
                raise AgentPlanValidationError(
                    f"invalid agent argument for {command.action}: {name}"
                )
            if command.action == "set_mode" and value not in {"auto", "sleep", "turbo"}:
                raise AgentPlanValidationError(
                    f"invalid agent argument for set_mode: {value}"
                )
        if arguments:
            raise AgentPlanValidationError(
                f"unexpected agent arguments for {command.action}: {sorted(arguments)}"
            )
