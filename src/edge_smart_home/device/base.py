from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

from edge_smart_home.common import DeviceCommand, DeviceState


class DeviceBackendError(RuntimeError):
    """A device command could not be completed."""


class DeviceBackend(ABC):
    @abstractmethod
    def execute(self, command: DeviceCommand) -> DeviceState:
        """Execute one normalized command and return confirmed state."""

    @abstractmethod
    def get_state(self, device_id: str) -> DeviceState:
        """Return the latest known state for one device."""

    @abstractmethod
    def list_states(self) -> Sequence[DeviceState]:
        """Return the latest known state for all devices."""

