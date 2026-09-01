from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from edge_smart_home.benchmark.gesture_ground_truth import (
    GestureSegment,
    evaluate_gesture_session,
    load_ground_truth,
)
from edge_smart_home.benchmark.gesture_guided import (
    GuidedGestureProtocol,
    GuidedPhase,
    build_default_guided_phases,
)


def frame(timestamp, label=None, triggered=False):
    return {
        "timestamp_ms": timestamp,
        "observations": [{"label": label, "score": 0.9 if label else 0.0}],
        "responses": [{"status": "executed"}] if triggered else [],
    }


class GestureGroundTruthTest(unittest.TestCase):
    def test_counts_match_duplicate_miss_and_false_trigger(self) -> None:
        frames = [
            frame(0), frame(100, "Thumb_Up"), frame(200, "Thumb_Up", True),
            frame(300, "Thumb_Up", True), frame(400), frame(500, "Victory", True),
            frame(600, "Victory"), frame(700, "Victory"),
        ]
        segments = [
            GestureSegment(0, 100, None),
            GestureSegment(100, 400, "Thumb_Up"),
            GestureSegment(400, 600, None),
            GestureSegment(600, 800, "Victory"),
        ]
        result = evaluate_gesture_session(frames, segments)
        self.assertEqual(result["matched_instances"], 1)
        self.assertEqual(result["missed_instances"], 1)
        self.assertEqual(result["duplicate_triggers"], 1)
        self.assertEqual(result["false_triggers"], 1)
        self.assertEqual(result["trigger_recall"], 0.5)
        self.assertEqual(result["mean_trigger_latency_ms"], 100)

    def test_rejects_invalid_segment(self) -> None:
        with self.assertRaises(ValueError):
            GestureSegment(100, 100, "Victory")

    def test_rejects_empty_manual_annotation(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "ground_truth.json"
            path.write_text(json.dumps({"segments": []}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "segments are empty"):
                load_ground_truth(path)

    def test_guided_protocol_uses_independent_timed_segments(self) -> None:
        protocol = GuidedGestureProtocol(
            1_000,
            (
                GuidedPhase(1_000, None, "NEUTRAL", "neutral"),
                GuidedPhase(2_000, "Thumb_Up", "DO Thumb_Up", "gesture"),
            ),
        )
        self.assertIsNone(protocol.state(4_000))
        annotation = protocol.annotation(Path("session"), 4_000, True)
        self.assertTrue(annotation["protocol_complete"])
        self.assertEqual(annotation["segments"][1]["label"], "Thumb_Up")
        self.assertEqual(annotation["segments"][1]["start_ms"], 2_000)
        default_phases = build_default_guided_phases()
        gesture_phases = [phase for phase in default_phases if phase.label is not None]
        self.assertEqual(sum(phase.duration_ms for phase in default_phases), 357_000)
        self.assertEqual(len(gesture_phases), 24)
        self.assertEqual(
            {hand: sum(phase.hand == hand for phase in gesture_phases) for hand in ("left", "right")},
            {"left": 12, "right": 12},
        )


if __name__ == "__main__":
    unittest.main()
