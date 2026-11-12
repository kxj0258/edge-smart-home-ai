from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

from edge_smart_home.common import DeviceCommand, DeviceState


class DeviceBackendError(RuntimeError):
    """A device command could not be completed."""

    def __init__(self, message: str, *, error_code: str = "EXECUTION_FAILED",
                 request_accepted: bool | None = False, observed_state: DeviceState | None = None) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.request_accepted = request_accepted
        self.observed_state = observed_state


class DeviceBackend(ABC):
    @abstractmethod
    def execute(self, command: DeviceCommand) -> DeviceState:
        """Execute a validated command and return backend-scoped readback.

        Mock state and HA target observations are distinct from physical-device
        confirmation. Acceptance without verified target state must raise.
        """

    @abstractmethod
    def get_state(self, device_id: str) -> DeviceState:
        """Return the latest known state for one device."""

    @abstractmethod
    def list_states(self) -> Sequence[DeviceState]:
        """Return the latest known state for all devices."""

