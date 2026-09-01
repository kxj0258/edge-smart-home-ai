from __future__ import annotations

from pathlib import Path
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
                GestureObservation(
                    label=category.category_name,
                    score=float(category.score),
                    timestamp_ms=timestamp_ms,
                    handedness=handedness,
                )
            )
        if not observations:
            observations.append(GestureObservation(None, 0.0, timestamp_ms))
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
                GestureObservation(
                    label=None if label == "None" else label,
                    score=float(category.score),
                    timestamp_ms=timestamp_ms,
                    handedness=handedness,
                )
            )
        if not observations:
            observations.append(GestureObservation(None, 0.0, timestamp_ms))
        return tuple(observations)

    def close(self) -> None:
        self._recognizer.close()

    def __enter__(self) -> "MediaPipeImageGestureRecognizer":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
