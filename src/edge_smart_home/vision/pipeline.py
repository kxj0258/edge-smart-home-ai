from __future__ import annotations

from typing import Any, Mapping

from edge_smart_home.app.orchestrator import AssistantOrchestrator, AssistantResponse
from edge_smart_home.app.pipeline import TextControlPipeline
from edge_smart_home.common import EventSource, PerceptionEvent

from .interfaces import GestureObservation, GestureRecognizer
from .temporal_filter import GestureTemporalFilter


class GestureControlPipeline:
    def __init__(
        self,
        recognizer: GestureRecognizer | None,
        temporal_filter: GestureTemporalFilter,
        mappings: Mapping[str, str],
        control: TextControlPipeline | AssistantOrchestrator,
    ) -> None:
        self.recognizer = recognizer
        self.temporal_filter = temporal_filter
        self.mappings = dict(mappings)
        self.orchestrator = (
            control if isinstance(control, AssistantOrchestrator) else AssistantOrchestrator(control)
        )

    def process_frame(
        self, rgb_frame: Any, timestamp_ms: int
    ) -> tuple[tuple[GestureObservation, AssistantResponse | None], ...]:
        if self.recognizer is None:
            raise RuntimeError("gesture recognizer is not configured")
        return tuple(
            (observation, self.process_observation(observation))
            for observation in self.recognizer.recognize_rgb(rgb_frame, timestamp_ms)
        )

    def process_observation(
        self, observation: GestureObservation
    ) -> AssistantResponse | None:
        if observation.label is not None and observation.label not in self.mappings:
            self.temporal_filter.accept(
                GestureObservation(
                    label=None,
                    score=0.0,
                    timestamp_ms=observation.timestamp_ms,
                    handedness=observation.handedness,
                )
            )
            return None
        triggered = self.temporal_filter.accept(observation)
        if triggered is None:
            return None
        intent_text = self.mappings[triggered]
        event = PerceptionEvent(
            source=EventSource.GESTURE,
            label=intent_text,
            confidence=observation.score,
            raw_payload={
                "gesture": triggered,
                "handedness": observation.handedness,
                "timestamp_ms": observation.timestamp_ms,
            },
        )
        return self.orchestrator.handle_event(event)
