from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any, Protocol, Sequence


@dataclass(frozen=True, slots=True)
class GestureObservation:
    label: str | None
    score: float
    timestamp_ms: int
    handedness: str | None = None
    wrist_x: float | None = None
    wrist_y: float | None = None
    pinch_distance: float | None = None
    landmarks: tuple[tuple[float, float, float], ...] = ()

    def __post_init__(self) -> None:
        if not 0.0 <= self.score <= 1.0:
            raise ValueError("score must be between 0 and 1")
        if self.timestamp_ms < 0:
            raise ValueError("timestamp_ms must not be negative")
        for name, value in (
            ("wrist_x", self.wrist_x),
            ("wrist_y", self.wrist_y),
            ("pinch_distance", self.pinch_distance),
        ):
            if value is not None and not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1")
        normalized_landmarks = tuple(
            (float(x), float(y), float(z)) for x, y, z in self.landmarks
        )
        for x, y, z in normalized_landmarks:
            if not 0.0 <= x <= 1.0 or not 0.0 <= y <= 1.0 or not isfinite(z):
                raise ValueError("landmark x and y must be between 0 and 1; z must be finite")
        object.__setattr__(self, "landmarks", normalized_landmarks)


class GestureRecognizer(Protocol):
    def recognize_rgb(
        self, rgb_frame: Any, timestamp_ms: int
    ) -> Sequence[GestureObservation]: ...

    def close(self) -> None: ...
