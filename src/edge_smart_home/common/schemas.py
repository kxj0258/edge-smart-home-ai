from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping
from uuid import uuid4


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _frozen_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType(dict(value))


class EventSource(str, Enum):
    TEXT = "text"
    VOICE = "voice"
    GESTURE = "gesture"


@dataclass(frozen=True, slots=True)
class PerceptionEvent:
    source: EventSource
    label: str
    confidence: float = 1.0
    timestamp: datetime = field(default_factory=utc_now)
    raw_payload: Mapping[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: str(uuid4()))

    def __post_init__(self) -> None:
        if not self.label.strip():
            raise ValueError("label must not be empty")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        if self.timestamp.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware")
        object.__setattr__(self, "raw_payload", _frozen_mapping(self.raw_payload))


@dataclass(frozen=True, slots=True)
class DeviceCommand:
    device_id: str
    action: str
    source: EventSource
    arguments: Mapping[str, Any] = field(default_factory=dict)
    confidence: float = 1.0
    request_id: str = field(default_factory=lambda: str(uuid4()))
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not self.device_id or not self.action:
            raise ValueError("device_id and action must not be empty")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        object.__setattr__(self, "arguments", _frozen_mapping(self.arguments))


@dataclass(frozen=True, slots=True)
class DeviceState:
    device_id: str
    available: bool
    attributes: Mapping[str, Any]
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not self.device_id:
            raise ValueError("device_id must not be empty")
        object.__setattr__(self, "attributes", _frozen_mapping(self.attributes))

