from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, Protocol

from edge_smart_home.common import PerceptionEvent

from .error_codes import result_error_code


class IntelligentResultKind(str, Enum):
    PLAN = "plan"
    CLARIFICATION = "clarification"
    RESPONSE = "response"
    UNAVAILABLE = "unavailable"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class PlannedDeviceCommand:
    device_id: str
    action: str
    arguments: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.device_id or not self.action:
            raise ValueError("planned device_id and action must not be empty")
        object.__setattr__(self, "arguments", MappingProxyType(dict(self.arguments)))


@dataclass(frozen=True, slots=True)
class IntelligentPathResult:
    kind: IntelligentResultKind
    commands: tuple[PlannedDeviceCommand, ...] = ()
    message: str | None = None
    reason: str | None = None
    error_code: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "error_code", result_error_code(self.kind, self.error_code))
        if self.kind == IntelligentResultKind.PLAN and not self.commands:
            raise ValueError("a plan must contain at least one command")
        if self.kind != IntelligentResultKind.PLAN and self.commands:
            raise ValueError("only a plan may contain commands")
        if self.kind in {
            IntelligentResultKind.CLARIFICATION,
            IntelligentResultKind.RESPONSE,
        } and not self.message:
            raise ValueError(f"{self.kind.value} requires a message")


class IntelligentPath(Protocol):
    def route(self, event: PerceptionEvent) -> IntelligentPathResult: ...
