from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, Sequence


@dataclass(frozen=True, slots=True)
class GestureObservation:
    label: str | None
    score: float
    timestamp_ms: int
    handedness: str | None = None

    def __post_init__(self) -> None:
        if not 0.0 <= self.score <= 1.0:
            raise ValueError("score must be between 0 and 1")
        if self.timestamp_ms < 0:
            raise ValueError("timestamp_ms must not be negative")


class GestureRecognizer(Protocol):
    def recognize_rgb(
        self, rgb_frame: Any, timestamp_ms: int
    ) -> Sequence[GestureObservation]: ...

    def close(self) -> None: ...
