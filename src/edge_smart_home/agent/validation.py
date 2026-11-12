from __future__ import annotations

from typing import Any, Mapping, Sequence

from .interfaces import PlannedDeviceCommand
from .error_codes import IntentErrorCode, normalize_error_code
from edge_smart_home.device.validation import validate_action_arguments
from edge_smart_home.device.base import DeviceBackendError


class AgentPlanValidationError(ValueError):
    """An Agent plan violates the configured device/action contract."""

    def __init__(self, message: str, *, error_code: IntentErrorCode | str = IntentErrorCode.PLAN_VALIDATION_FAILED):
        super().__init__(message)
        self.error_code = normalize_error_code(error_code)


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
                    f"agent plan references unknown device: {command.device_id}",
                    error_code=IntentErrorCode.UNKNOWN_DEVICE,
                )
            if command.action not in device.get("capabilities", []):
                raise AgentPlanValidationError(
                    f"agent action is not allowed: {command.device_id}.{command.action}",
                    error_code=IntentErrorCode.UNSUPPORTED_ACTION,
                )
            self._validate_arguments(command)

    @staticmethod
    def _validate_arguments(command: PlannedDeviceCommand) -> None:
        try:
            validate_action_arguments(command.action, command.arguments)
        except DeviceBackendError as error:
            raise AgentPlanValidationError(
                f"invalid agent argument for {command.action}: {error}",
                error_code=error.error_code,
            ) from error
