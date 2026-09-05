from __future__ import annotations

from collections import Counter
from types import MappingProxyType
from typing import Mapping

from .interfaces import GestureObservation


class GestureTemporalFilter:
    """Turn noisy frame classifications into debounced gesture triggers."""

    def __init__(
        self,
        score_threshold: float,
        min_consecutive_frames: int,
        release_frames: int,
        cooldown_seconds: float,
        release_duration_seconds: float = 0.0,
    ) -> None:
        if not 0.0 <= score_threshold <= 1.0:
            raise ValueError("score_threshold must be between 0 and 1")
        if min_consecutive_frames <= 0 or release_frames <= 0:
            raise ValueError("frame counts must be positive")
        if cooldown_seconds < 0:
            raise ValueError("cooldown_seconds must not be negative")
        if release_duration_seconds < 0:
            raise ValueError("release_duration_seconds must not be negative")
        self.score_threshold = score_threshold
        self.min_consecutive_frames = min_consecutive_frames
        self.release_frames = release_frames
        self.cooldown_ms = round(cooldown_seconds * 1000)
        self.release_duration_ms = round(release_duration_seconds * 1000)
        self._candidate: str | None = None
        self._candidate_frames = 0
        self._release_frames = 0
        self._release_started_ms: int | None = None
        self._latched: str | None = None
        self._last_trigger_ms: int | None = None
        self._decision_counts: Counter[str] = Counter()

    @property
    def decision_counts(self) -> Mapping[str, int]:
        return MappingProxyType(dict(self._decision_counts))

    def reset(self) -> None:
        """Clear the current latch without changing diagnostic counters."""
        self._candidate = None
        self._candidate_frames = 0
        self._release_frames = 0
        self._release_started_ms = None
        self._latched = None

    def accept(self, observation: GestureObservation) -> str | None:
        label = observation.label
        if label is None or observation.score < self.score_threshold:
            self._decision_counts[
                "neutral" if label is None else "low_confidence"
            ] += 1
            self._release_frames += 1
            if self._release_started_ms is None:
                self._release_started_ms = observation.timestamp_ms
            self._candidate = None
            self._candidate_frames = 0
            release_elapsed_ms = observation.timestamp_ms - self._release_started_ms
            if (
                self._release_frames >= self.release_frames
                and release_elapsed_ms >= self.release_duration_ms
            ):
                self._latched = None
            return None

        self._release_frames = 0
        self._release_started_ms = None
        if label != self._candidate:
            self._candidate = label
            self._candidate_frames = 1
        else:
            self._candidate_frames += 1
        if self._candidate_frames < self.min_consecutive_frames:
            self._decision_counts["candidate"] += 1
            return None
        if self._latched == label:
            self._decision_counts["latched"] += 1
            return None
        if (
            self._last_trigger_ms is not None
            and observation.timestamp_ms - self._last_trigger_ms < self.cooldown_ms
        ):
            self._decision_counts["cooldown"] += 1
            return None
        self._latched = label
        self._last_trigger_ms = observation.timestamp_ms
        self._decision_counts["triggered"] += 1
        return label
