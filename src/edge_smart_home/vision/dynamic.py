from __future__ import annotations

from dataclasses import dataclass

from .interfaces import GestureObservation


@dataclass(frozen=True, slots=True)
class DynamicGestureEvent:
    label: str
    timestamp_ms: int
    score: float


class DynamicGestureDecoder:
    """Decode bounded wrist motions and pinch motions into one-shot events.

    The decoder deliberately emits symbolic events only. It never knows about
    devices or executes commands; the regular GestureControlPipeline maps the
    event through the same orchestrator, validator, safety policy and backend
    as static gestures.
    """

    def __init__(
        self,
        *,
        swipe_distance: float = 0.22,
        swipe_min_duration_ms: int = 120,
        swipe_max_duration_ms: int = 1200,
        pinch_threshold: float = 0.22,
        pinch_move_distance: float = 0.12,
        pinch_min_duration_ms: int = 180,
        pinch_max_duration_ms: int = 1800,
        cooldown_seconds: float = 1.0,
    ) -> None:
        if not 0.0 < swipe_distance <= 1.0:
            raise ValueError("swipe_distance must be between 0 and 1")
        if not 0 < swipe_min_duration_ms <= swipe_max_duration_ms:
            raise ValueError("invalid swipe duration bounds")
        if not 0.0 < pinch_threshold <= 1.0:
            raise ValueError("pinch_threshold must be between 0 and 1")
        if not 0.0 < pinch_move_distance <= 1.0:
            raise ValueError("pinch_move_distance must be between 0 and 1")
        if not 0 < pinch_min_duration_ms <= pinch_max_duration_ms:
            raise ValueError("invalid pinch duration bounds")
        if cooldown_seconds < 0:
            raise ValueError("cooldown_seconds must not be negative")
        self.swipe_distance = swipe_distance
        self.swipe_min_duration_ms = swipe_min_duration_ms
        self.swipe_max_duration_ms = swipe_max_duration_ms
        self.pinch_threshold = pinch_threshold
        self.pinch_move_distance = pinch_move_distance
        self.pinch_min_duration_ms = pinch_min_duration_ms
        self.pinch_max_duration_ms = pinch_max_duration_ms
        self.cooldown_ms = round(cooldown_seconds * 1000)
        self._motion_start: tuple[float, float, int] | None = None
        self._last_position: tuple[float, float, int] | None = None
        self._pinch_start: tuple[float, int] | None = None
        self._last_trigger_ms: int | None = None
        self._last_event: DynamicGestureEvent | None = None

    @property
    def last_event(self) -> DynamicGestureEvent | None:
        return self._last_event

    def reset(self) -> None:
        self._motion_start = None
        self._last_position = None
        self._pinch_start = None
        self._last_event = None

    def accept(self, observation: GestureObservation) -> DynamicGestureEvent | None:
        self._last_event = None
        if observation.wrist_x is None or observation.wrist_y is None:
            self.reset()
            return None

        x, y, timestamp_ms = (
            observation.wrist_x,
            observation.wrist_y,
            observation.timestamp_ms,
        )
        if self._last_position is not None and timestamp_ms <= self._last_position[2]:
            self.reset()
            return None

        is_pinching = (
            observation.pinch_distance is not None
            and observation.pinch_distance <= self.pinch_threshold
        )
        if is_pinching:
            self._motion_start = None
            self._last_position = (x, y, timestamp_ms)
            if self._pinch_start is None:
                self._pinch_start = (y, timestamp_ms)
                return None
            start_y, start_ms = self._pinch_start
            elapsed = timestamp_ms - start_ms
            delta_y = y - start_y
            if (
                self.pinch_min_duration_ms <= elapsed <= self.pinch_max_duration_ms
                and abs(delta_y) >= self.pinch_move_distance
                and self._cooldown_ready(timestamp_ms)
            ):
                label = "pinch_down" if delta_y > 0 else "pinch_up"
                return self._emit(label, timestamp_ms, observation.score)
            if elapsed > self.pinch_max_duration_ms:
                self._pinch_start = (y, timestamp_ms)
            return None

        self._pinch_start = None
        if self._motion_start is None:
            self._motion_start = (x, y, timestamp_ms)
        self._last_position = (x, y, timestamp_ms)
        start_x, _, start_ms = self._motion_start
        elapsed = timestamp_ms - start_ms
        delta_x = x - start_x
        if elapsed > self.swipe_max_duration_ms:
            self._motion_start = (x, y, timestamp_ms)
            return None
        if (
            self.swipe_min_duration_ms <= elapsed <= self.swipe_max_duration_ms
            and abs(delta_x) >= self.swipe_distance
            and self._cooldown_ready(timestamp_ms)
        ):
            label = "swipe_right" if delta_x > 0 else "swipe_left"
            return self._emit(label, timestamp_ms, observation.score)
        return None

    def _cooldown_ready(self, timestamp_ms: int) -> bool:
        return (
            self._last_trigger_ms is None
            or timestamp_ms - self._last_trigger_ms >= self.cooldown_ms
        )

    def _emit(self, label: str, timestamp_ms: int, score: float) -> DynamicGestureEvent:
        event = DynamicGestureEvent(label, timestamp_ms, score)
        self._last_trigger_ms = timestamp_ms
        self._last_event = event
        self._motion_start = None
        self._last_position = None
        self._pinch_start = None
        return event
