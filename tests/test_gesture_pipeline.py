from __future__ import annotations

from pathlib import Path
import unittest

from edge_smart_home.app.pipeline import TextControlPipeline
from edge_smart_home.common import DeviceState, SceneExecutionResult
from edge_smart_home.vision import GestureControlPipeline, GestureObservation, GestureTemporalFilter


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class GesturePipelineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.control = TextControlPipeline.from_config_dir(PROJECT_ROOT / "configs")
        self.pipeline = GestureControlPipeline(
            None,
            GestureTemporalFilter(0.65, 3, 2, 1.5),
            {"Thumb_Up": "电视音量调高", "Victory": "开启观影模式"},
            self.control,
        )

    def test_requires_consecutive_frames_and_latches_held_gesture(self) -> None:
        self.assertIsNone(self.pipeline.process_observation(GestureObservation("Thumb_Up", 0.9, 0)))
        self.assertIsNone(self.pipeline.process_observation(GestureObservation("Thumb_Up", 0.9, 100)))
        outcome = self.pipeline.process_observation(GestureObservation("Thumb_Up", 0.9, 200))
        self.assertIsInstance(outcome.outcome, DeviceState)
        self.assertEqual(outcome.outcome.attributes["volume"], 25)
        self.assertIsNone(self.pipeline.process_observation(GestureObservation("Thumb_Up", 0.9, 300)))

    def test_release_and_cooldown_prevent_duplicate_trigger(self) -> None:
        for timestamp in (0, 100, 200):
            first = self.pipeline.process_observation(GestureObservation("Thumb_Up", 0.9, timestamp))
        self.assertIsNotNone(first)
        for timestamp in (300, 400):
            self.pipeline.process_observation(GestureObservation(None, 0.0, timestamp))
        for timestamp in (500, 600, 700):
            blocked = self.pipeline.process_observation(GestureObservation("Thumb_Up", 0.9, timestamp))
        self.assertIsNone(blocked)

    def test_gesture_can_trigger_scene(self) -> None:
        for timestamp in (0, 100, 200):
            outcome = self.pipeline.process_observation(GestureObservation("Victory", 0.95, timestamp))
        self.assertIsInstance(outcome.outcome, SceneExecutionResult)
        self.assertTrue(outcome.outcome.succeeded)

    def test_low_confidence_does_not_trigger(self) -> None:
        for timestamp in (0, 100, 200, 300):
            outcome = self.pipeline.process_observation(GestureObservation("Thumb_Up", 0.4, timestamp))
        self.assertIsNone(outcome)
        self.assertEqual(
            self.pipeline.temporal_filter.decision_counts["low_confidence"], 4
        )

    def test_unknown_gesture_releases_without_consuming_cooldown(self) -> None:
        for timestamp in (0, 100):
            self.pipeline.process_observation(
                GestureObservation("Unknown", 0.99, timestamp)
            )
        for timestamp in (200, 300, 400):
            outcome = self.pipeline.process_observation(
                GestureObservation("Thumb_Up", 0.9, timestamp)
            )
        self.assertIsNotNone(outcome)

    def test_filter_records_latched_and_trigger_decisions(self) -> None:
        for timestamp in (0, 100, 200, 300):
            self.pipeline.process_observation(
                GestureObservation("Thumb_Up", 0.9, timestamp)
            )
        counts = self.pipeline.temporal_filter.decision_counts
        self.assertEqual(counts["candidate"], 2)
        self.assertEqual(counts["triggered"], 1)
        self.assertEqual(counts["latched"], 1)

    def test_release_requires_continuous_neutral_duration(self) -> None:
        pipeline = GestureControlPipeline(
            None,
            GestureTemporalFilter(0.65, 3, 2, 0.0, 0.6),
            {"Thumb_Up": "电视音量调高"},
            self.control,
        )
        for timestamp in (0, 100, 200):
            first = pipeline.process_observation(
                GestureObservation("Thumb_Up", 0.9, timestamp)
            )
        self.assertIsNotNone(first)
        for timestamp in (300, 400, 500, 600, 700, 800):
            pipeline.process_observation(GestureObservation(None, 0.0, timestamp))
        for timestamp in (900, 1000, 1100):
            blocked = pipeline.process_observation(
                GestureObservation("Thumb_Up", 0.9, timestamp)
            )
        self.assertIsNone(blocked)
        pipeline.process_observation(GestureObservation(None, 0.0, 1200))
        pipeline.process_observation(GestureObservation(None, 0.0, 1800))
        for timestamp in (1900, 2000, 2100):
            retriggered = pipeline.process_observation(
                GestureObservation("Thumb_Up", 0.9, timestamp)
            )
        self.assertIsNotNone(retriggered)


if __name__ == "__main__":
    unittest.main()
