from __future__ import annotations

from edge_smart_home.common.observability import observed

from enum import IntEnum
from typing import Mapping

from edge_smart_home.common import DeviceCommand


class RiskLevel(IntEnum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3


class ConfirmationRequiredError(RuntimeError):
    """A high-impact command requires explicit user confirmation."""


class SafetyPolicy:
    def __init__(self, action_risks: Mapping[str, str]) -> None:
        self._action_risks = {
            action: RiskLevel[level.upper()] for action, level in action_risks.items()
        }

    def risk_for(self, command: DeviceCommand) -> RiskLevel:
        return self._action_risks.get(command.action, RiskLevel.LOW)

    @observed("safety")
    def check(self, command: DeviceCommand, confirmed: bool = False) -> None:
        if self.risk_for(command) >= RiskLevel.HIGH and not confirmed:
            raise ConfirmationRequiredError(
                f"confirmation required for {command.device_id}.{command.action}"
            )
