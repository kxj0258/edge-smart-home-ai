from __future__ import annotations

import unittest
from types import SimpleNamespace

from edge_smart_home.vision import GestureObservation, landmark_pixel_points
from edge_smart_home.vision.mediapipe_adapter import _observation_from_result


class VisionDisplayTest(unittest.TestCase):
    def test_observation_keeps_normalized_landmarks(self) -> None:
        observation = GestureObservation(
            "Open_Palm",
            0.9,
            100,
            landmarks=tuple((index / 20, 0.5, -0.01 * index) for index in range(21)),
        )
        self.assertEqual(len(observation.landmarks), 21)
        self.assertEqual(observation.landmarks[20], (1.0, 0.5, -0.2))

    def test_landmarks_map_to_display_pixels_and_can_be_mirrored(self) -> None:
        observation = GestureObservation(
            None,
            0.0,
            100,
            landmarks=((0.0, 0.0, 0.0), (1.0, 1.0, 0.0)),
        )
        self.assertEqual(
            landmark_pixel_points(observation, 100, 80, margin=10),
            ((10, 10), (90, 70)),
        )
        self.assertEqual(
            landmark_pixel_points(observation, 100, 80, margin=10, mirror=True),
            ((90, 10), (10, 70)),
        )

    def test_invalid_landmark_coordinates_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            GestureObservation(
                None,
                0.0,
                100,
                landmarks=((1.1, 0.5, 0.0),),
            )

    def test_mediapipe_adapter_exposes_all_landmarks(self) -> None:
        landmarks = [
            SimpleNamespace(x=index / 20, y=0.5, z=-index / 100)
            for index in range(21)
        ]
        result = SimpleNamespace(hand_landmarks=[landmarks])
        observation = _observation_from_result(
            "Open_Palm", 0.9, 100, "Right", result, 0
        )
        self.assertEqual(len(observation.landmarks), 21)
        self.assertEqual(observation.wrist_x, 0.0)
        self.assertEqual(observation.wrist_y, 0.5)
        self.assertEqual(observation.landmarks[20], (1.0, 0.5, -0.2))


if __name__ == "__main__":
    unittest.main()
