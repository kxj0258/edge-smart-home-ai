from __future__ import annotations

from typing import Any, Mapping

from edge_smart_home.app.orchestrator import AssistantOrchestrator, AssistantResponse
from edge_smart_home.audio.feedback import AssistantFeedback
from edge_smart_home.app.pipeline import TextControlPipeline
from edge_smart_home.common import EventSource, PerceptionEvent

from .interfaces import GestureObservation, GestureRecognizer
from .dynamic import DynamicGestureDecoder
from .temporal_filter import GestureTemporalFilter


class GestureControlPipeline:
    def __init__(
        self,
        recognizer: GestureRecognizer | None,
        temporal_filter: GestureTemporalFilter,
        mappings: Mapping[str, Any],
        control: TextControlPipeline | AssistantOrchestrator,
        feedback: AssistantFeedback | None = None,
        *,
        dynamic_decoder: DynamicGestureDecoder | None = None,
        profile: str | None = None,
        requires_activation: bool = False,
        activation_gesture: str = "Open_Palm",
        activation_hold_seconds: float = 1.0,
    ) -> None:
        self.recognizer = recognizer
        self.temporal_filter = temporal_filter
        self._profiles = _normalize_profiles(mappings)
        self._profile = profile or next(iter(self._profiles))
        if self._profile not in self._profiles:
            raise ValueError(f"unknown gesture profile: {self._profile}")
        self.dynamic_decoder = dynamic_decoder
        self.requires_activation = requires_activation
        self.activation_gesture = activation_gesture
        self.activation_hold_ms = round(activation_hold_seconds * 1000)
        if self.activation_hold_ms < 0:
            raise ValueError("activation_hold_seconds must not be negative")
        self._gesture_enabled = not requires_activation
        self._activation_started_ms: int | None = None
        self._activation_frames = 0
        self._activation_release_required = False
        self._status_message: str | None = None
        self.orchestrator = (
            control if isinstance(control, AssistantOrchestrator) else AssistantOrchestrator(control)
        )
        self.feedback = feedback

    @property
    def profile(self) -> str:
        return self._profile

    @property
    def profiles(self) -> tuple[str, ...]:
        return tuple(self._profiles)

    @property
    def gesture_enabled(self) -> bool:
        return self._gesture_enabled

    def set_profile(self, profile: str) -> None:
        if profile not in self._profiles:
            raise ValueError(f"unknown gesture profile: {profile}")
        self._profile = profile
        self._reset_decoders()
        self._status_message = f"手势控制模式：{profile}"

    def set_enabled(self, enabled: bool) -> None:
        self._gesture_enabled = bool(enabled)
        self._activation_started_ms = None
        self._activation_frames = 0
        self._activation_release_required = False
        self._reset_decoders()
        self._status_message = "手势控制已激活" if enabled else "手势控制已暂停"

    def consume_status_message(self) -> str | None:
        message = self._status_message
        self._status_message = None
        return message

    def current_mappings(self) -> Mapping[str, str]:
        return self._profiles[self._profile]["static"]

    def current_dynamic_mappings(self) -> Mapping[str, str]:
        return self._profiles[self._profile]["dynamic"]

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
        if not self._gesture_enabled:
            self._accept_activation(observation)
            return None
        if self._activation_release_required:
            if (
                observation.label != self.activation_gesture
                or observation.score < self.temporal_filter.score_threshold
            ):
                self._activation_release_required = False
                self._reset_decoders()
            return None

        mappings = self.current_mappings()
        if observation.label is not None and observation.label not in mappings:
            self.temporal_filter.accept(
                GestureObservation(
                    label=None,
                    score=0.0,
                    timestamp_ms=observation.timestamp_ms,
                    handedness=observation.handedness,
                )
            )
            static_response = None
        else:
            triggered = self.temporal_filter.accept(observation)
            static_response = (
                self._dispatch(triggered, mappings[triggered], observation)
                if triggered is not None
                else None
            )
        if static_response is not None:
            return static_response
        if self.dynamic_decoder is None:
            return None
        dynamic_event = self.dynamic_decoder.accept(observation)
        if dynamic_event is None:
            return None
        intent_text = self.current_dynamic_mappings().get(dynamic_event.label)
        if intent_text is None:
            return None
        return self._dispatch(dynamic_event.label, intent_text, observation, dynamic=True)

    def _dispatch(
        self,
        gesture: str,
        intent_text: str,
        observation: GestureObservation,
        dynamic: bool = False,
    ) -> AssistantResponse:
        event = PerceptionEvent(
            source=EventSource.GESTURE,
            label=intent_text,
            confidence=observation.score,
            raw_payload={
                "gesture": gesture,
                "dynamic": dynamic,
                "handedness": observation.handedness,
                "wrist_x": observation.wrist_x,
                "wrist_y": observation.wrist_y,
                "pinch_distance": observation.pinch_distance,
                "timestamp_ms": observation.timestamp_ms,
            },
        )
        response = self.orchestrator.handle_event(event)
        if self.feedback:
            self.feedback.response(response)
        return response

    def _accept_activation(self, observation: GestureObservation) -> None:
        if (
            observation.label != self.activation_gesture
            or observation.score < self.temporal_filter.score_threshold
        ):
            self._activation_started_ms = None
            self._activation_frames = 0
            self.temporal_filter.accept(
                GestureObservation(
                    None,
                    0.0,
                    observation.timestamp_ms,
                    observation.handedness,
                )
            )
            return
        if self._activation_started_ms is None:
            self._activation_started_ms = observation.timestamp_ms
            self._activation_frames = 1
        else:
            self._activation_frames += 1
        elapsed = observation.timestamp_ms - self._activation_started_ms
        if (
            self._activation_frames >= self.temporal_filter.min_consecutive_frames
            and elapsed >= self.activation_hold_ms
        ):
            self.set_enabled(True)
            # Do not turn the activation hold itself into an action. The user
            # must release the activation gesture before the next command.
            self._activation_release_required = True

    def _reset_decoders(self) -> None:
        self.temporal_filter.reset()
        if self.dynamic_decoder is not None:
            self.dynamic_decoder.reset()


def _normalize_profiles(
    mappings: Mapping[str, Any],
) -> dict[str, dict[str, dict[str, str]]]:
    """Accept both the original flat mapping and the profile configuration."""
    profiles = mappings.get("profiles") if isinstance(mappings, Mapping) else None
    if not isinstance(profiles, Mapping):
        return {"default": {"static": dict(mappings), "dynamic": {}}}
    normalized: dict[str, dict[str, dict[str, str]]] = {}
    for name, value in profiles.items():
        if not isinstance(value, Mapping):
            raise ValueError(f"gesture profile must be an object: {name}")
        static = value.get("static", {})
        dynamic = value.get("dynamic", {})
        if not isinstance(static, Mapping) or not isinstance(dynamic, Mapping):
            raise ValueError(f"gesture profile mappings must be objects: {name}")
        normalized[str(name)] = {
            "static": {str(key): str(item) for key, item in static.items()},
            "dynamic": {str(key): str(item) for key, item in dynamic.items()},
        }
    if not normalized:
        raise ValueError("at least one gesture profile is required")
    return normalized
