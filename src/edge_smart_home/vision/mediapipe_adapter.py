from __future__ import annotations

from pathlib import Path
from math import hypot
from typing import Any, Mapping, Sequence

from .interfaces import GestureObservation


class VisionDependencyError(RuntimeError):
    """A vision runtime dependency or model is unavailable."""


class MediaPipeGestureRecognizer:
    """Adapter for the MediaPipe Tasks Gesture Recognizer VIDEO API."""

    def __init__(self, model: Path, config: Mapping[str, Any]) -> None:
        if not model.is_file():
            raise VisionDependencyError(f"gesture model does not exist: {model}")
        try:
            import mediapipe as mp
            from mediapipe.tasks import python
            from mediapipe.tasks.python import vision
        except ImportError as error:
            raise VisionDependencyError(
                "mediapipe is required for gesture recognition"
            ) from error

        options = vision.GestureRecognizerOptions(
            base_options=python.BaseOptions(model_asset_path=str(model)),
            running_mode=vision.RunningMode.VIDEO,
            num_hands=int(config["num_hands"]),
            min_hand_detection_confidence=float(
                config["min_hand_detection_confidence"]
            ),
            min_hand_presence_confidence=float(
                config["min_hand_presence_confidence"]
            ),
            min_tracking_confidence=float(config["min_tracking_confidence"]),
        )
        self._mp = mp
        self._recognizer = vision.GestureRecognizer.create_from_options(options)
        self._last_timestamp_ms = -1

    def recognize_rgb(
        self, rgb_frame: Any, timestamp_ms: int
    ) -> Sequence[GestureObservation]:
        if timestamp_ms <= self._last_timestamp_ms:
            raise ValueError("MediaPipe VIDEO timestamps must be strictly increasing")
        self._last_timestamp_ms = timestamp_ms
        image = self._mp.Image(
            image_format=self._mp.ImageFormat.SRGB,
            data=rgb_frame,
        )
        result = self._recognizer.recognize_for_video(image, timestamp_ms)
        observations = []
        for hand_index, categories in enumerate(result.gestures):
            if not categories:
                continue
            category = categories[0]
            handedness = None
            if hand_index < len(result.handedness) and result.handedness[hand_index]:
                handedness = result.handedness[hand_index][0].category_name
            observations.append(
                _observation_from_result(
                    category.category_name,
                    float(category.score),
                    timestamp_ms,
                    handedness,
                    result,
                    hand_index,
                )
            )
        if not observations:
            observations.append(_observation_from_result(
                None, 0.0, timestamp_ms, None, result, 0
            ))
        return tuple(observations)

    def close(self) -> None:
        self._recognizer.close()

    def __enter__(self) -> "MediaPipeGestureRecognizer":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class MediaPipeImageGestureRecognizer:
    """MediaPipe IMAGE-mode adapter for independent dataset samples."""

    def __init__(self, model: Path, config: Mapping[str, Any]) -> None:
        if not model.is_file():
            raise VisionDependencyError(f"gesture model does not exist: {model}")
        try:
            import mediapipe as mp
            from mediapipe.tasks import python
            from mediapipe.tasks.python import vision
        except ImportError as error:
            raise VisionDependencyError(
                "mediapipe is required for gesture recognition"
            ) from error
        options = vision.GestureRecognizerOptions(
            base_options=python.BaseOptions(model_asset_path=str(model)),
            running_mode=vision.RunningMode.IMAGE,
            num_hands=int(config["num_hands"]),
            min_hand_detection_confidence=float(
                config["min_hand_detection_confidence"]
            ),
            min_hand_presence_confidence=float(
                config["min_hand_presence_confidence"]
            ),
            min_tracking_confidence=float(config["min_tracking_confidence"]),
        )
        self._mp = mp
        self._recognizer = vision.GestureRecognizer.create_from_options(options)

    def recognize_rgb(
        self, rgb_frame: Any, timestamp_ms: int
    ) -> Sequence[GestureObservation]:
        image = self._mp.Image(
            image_format=self._mp.ImageFormat.SRGB,
            data=rgb_frame,
        )
        result = self._recognizer.recognize(image)
        observations = []
        for hand_index, categories in enumerate(result.gestures):
            if not categories:
                continue
            category = categories[0]
            handedness = None
            if hand_index < len(result.handedness) and result.handedness[hand_index]:
                handedness = result.handedness[hand_index][0].category_name
            label = category.category_name
            observations.append(
                _observation_from_result(
                    None if label == "None" else label,
                    float(category.score),
                    timestamp_ms,
                    handedness,
                    result,
                    hand_index,
                )
            )
        if not observations:
            observations.append(_observation_from_result(
                None, 0.0, timestamp_ms, None, result, 0
            ))
        return tuple(observations)

    def close(self) -> None:
        self._recognizer.close()

    def __enter__(self) -> "MediaPipeImageGestureRecognizer":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def _observation_from_result(
    label: str | None,
    score: float,
    timestamp_ms: int,
    handedness: str | None,
    result: Any,
    hand_index: int,
) -> GestureObservation:
    """Keep motion features and normalized landmarks at the adapter boundary."""
    try:
        landmarks = result.hand_landmarks[hand_index]
    except (AttributeError, IndexError):
        return GestureObservation(label, score, timestamp_ms, handedness)
    if not landmarks:
        return GestureObservation(label, score, timestamp_ms, handedness)
    normalized_landmarks = tuple(
        (
            min(1.0, max(0.0, float(landmark.x))),
            min(1.0, max(0.0, float(landmark.y))),
            float(landmark.z),
        )
        for landmark in landmarks
    )
    wrist = landmarks[0]
    if len(landmarks) < 18:
        return GestureObservation(
            label=label,
            score=score,
            timestamp_ms=timestamp_ms,
            handedness=handedness,
            wrist_x=normalized_landmarks[0][0],
            wrist_y=normalized_landmarks[0][1],
            landmarks=normalized_landmarks,
        )
    thumb_tip = landmarks[4]
    index_tip = landmarks[8]
    palm_scale = max(
        hypot(float(landmarks[5].x) - float(landmarks[17].x),
              float(landmarks[5].y) - float(landmarks[17].y)),
        1e-6,
    )
    pinch_distance = min(
        1.0,
        hypot(float(thumb_tip.x) - float(index_tip.x),
              float(thumb_tip.y) - float(index_tip.y)) / palm_scale,
    )
    return GestureObservation(
        label=label,
        score=score,
        timestamp_ms=timestamp_ms,
        handedness=handedness,
        wrist_x=min(1.0, max(0.0, float(wrist.x))),
        wrist_y=min(1.0, max(0.0, float(wrist.y))),
        pinch_distance=pinch_distance,
        landmarks=normalized_landmarks,
    )
