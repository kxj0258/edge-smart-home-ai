from __future__ import annotations

from pathlib import Path
import unittest

from edge_smart_home.app.pipeline import TextControlPipeline
from edge_smart_home.common import DeviceState, SceneExecutionResult
from edge_smart_home.vision import (
    DynamicGestureDecoder,
    GestureControlPipeline,
    GestureObservation,
    GestureTemporalFilter,
)


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

    def test_dynamic_swipe_emits_one_event(self) -> None:
        decoder = DynamicGestureDecoder(
            swipe_distance=0.2,
            swipe_min_duration_ms=100,
            swipe_max_duration_ms=800,
        )
        self.assertIsNone(
            decoder.accept(GestureObservation(None, 0.9, 0, wrist_x=0.2, wrist_y=0.5))
        )
        self.assertEqual(
            decoder.accept(
                GestureObservation(None, 0.9, 300, wrist_x=0.48, wrist_y=0.5)
            ).label,
            "swipe_right",
        )

    def test_dynamic_pinch_maps_through_the_same_control_pipeline(self) -> None:
        mappings = {
            "profiles": {
                "home": {
                    "static": {},
                    "dynamic": {"pinch_up": "把客厅灯亮度设置为80"},
                }
            }
        }
        pipeline = GestureControlPipeline(
            None,
            GestureTemporalFilter(0.65, 3, 2, 1.5),
            mappings,
            self.control,
            dynamic_decoder=DynamicGestureDecoder(
                pinch_threshold=0.2,
                pinch_move_distance=0.1,
                pinch_min_duration_ms=100,
                pinch_max_duration_ms=800,
            ),
            profile="home",
        )
        pipeline.process_observation(
            GestureObservation(None, 0.9, 0, wrist_x=0.5, wrist_y=0.6, pinch_distance=0.1)
        )
        outcome = pipeline.process_observation(
            GestureObservation(None, 0.9, 300, wrist_x=0.5, wrist_y=0.45, pinch_distance=0.1)
        )
        self.assertIsNotNone(outcome)
        self.assertEqual(outcome.outcome.attributes["brightness"], 80)
        self.assertTrue(outcome.event.raw_payload["dynamic"])

    def test_activation_requires_a_held_open_palm(self) -> None:
        pipeline = GestureControlPipeline(
            None,
            GestureTemporalFilter(0.65, 3, 2, 1.5),
            {"Open_Palm": "继续播放", "Closed_Fist": "暂停播放"},
            self.control,
            requires_activation=True,
            activation_hold_seconds=1.0,
        )
        for timestamp in (0, 400, 1000):
            self.assertIsNone(
                pipeline.process_observation(
                    GestureObservation("Open_Palm", 0.9, timestamp)
                )
            )
        self.assertTrue(pipeline.gesture_enabled)
        for timestamp in (1100, 1200):
            self.assertIsNone(
                pipeline.process_observation(
                    GestureObservation("Closed_Fist", 0.9, timestamp)
                )
            )
        pipeline.process_observation(
            GestureObservation("Closed_Fist", 0.9, 1400)
        )
        outcome = pipeline.process_observation(
            GestureObservation("Closed_Fist", 0.9, 1500)
        )
        self.assertIsNotNone(outcome)


if __name__ == "__main__":
    unittest.main()
