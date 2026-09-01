from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from edge_smart_home.benchmark.hagrid import (
    build_hagrid_manifest,
    load_label_mapping,
    select_ranked_samples,
    summarize_hagrid,
    summarize_hagrid_threshold,
)


class HagridEvaluationTest(unittest.TestCase):
    def test_mapping_excludes_ambiguous_labels_and_keeps_neutral(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "mapping.json"
            path.write_text(
                json.dumps(
                    {
                        "labels": {
                            "like": {"include": True, "product_label": "Thumb_Up"},
                            "no_gesture": {"include": True, "product_label": None},
                            "stop": {"include": False, "product_label": None},
                        }
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                load_label_mapping(path),
                {"like": "Thumb_Up", "no_gesture": None},
            )

    def test_manifest_sampling_is_deterministic_and_reports_unmapped(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            annotations = root / "annotations"
            annotations.mkdir()
            payload = {
                "b": {"labels": ["like"], "user_id": "u2"},
                "a": {"labels": ["like"], "user_id": "u1"},
            }
            (annotations / "like.json").write_text(json.dumps(payload), encoding="utf-8")
            (annotations / "stop.json").write_text(
                json.dumps({"c": {"labels": ["stop"], "user_id": "u3"}}),
                encoding="utf-8",
            )
            first = build_hagrid_manifest(
                annotations, root / "images", {"like": "Thumb_Up"}, "test", 1, 7
            )
            second = build_hagrid_manifest(
                annotations, root / "images", {"like": "Thumb_Up"}, "test", 1, 7
            )
            self.assertEqual(first, second)
            self.assertEqual(len(first), 2)
            self.assertEqual(
                {row["dataset_label"]: row["evaluation_included"] for row in first},
                {"like": True, "stop": False},
            )

    def test_summary_separates_mapping_coverage_and_classification(self) -> None:
        rows = [
            {
                "dataset_label": "like", "expected_label": "Thumb_Up",
                "predicted_label": "Thumb_Up", "evaluation_included": True,
                "hand_detected": True, "inference_ms": 10.0, "error": None,
            },
            {
                "dataset_label": "dislike", "expected_label": "Thumb_Down",
                "predicted_label": "Thumb_Up", "evaluation_included": True,
                "hand_detected": True, "inference_ms": 20.0, "error": None,
            },
            {
                "dataset_label": "stop", "expected_label": None,
                "evaluation_included": False,
            },
        ]
        summary = summarize_hagrid(rows)
        self.assertEqual(summary["mapping_coverage"], 0.6667)
        self.assertEqual(summary["accuracy"], 0.5)
        self.assertEqual(summary["hand_detection_coverage"], 1.0)
        self.assertEqual(summary["mean_inference_ms"], 15.0)

    def test_smoke_selection_is_deterministic_and_bounded(self) -> None:
        samples = [
            {"dataset_label": "like", "source_path": f"{index}.jpg"}
            for index in range(10)
        ]
        first = select_ranked_samples(samples, {"like"}, 3, 42)
        second = select_ranked_samples(reversed(samples), {"like"}, 3, 42)
        self.assertEqual(
            [row["source_path"] for row in first["like"]],
            [row["source_path"] for row in second["like"]],
        )
        self.assertEqual(len(first["like"]), 3)

    def test_threshold_replay_reports_recall_and_neutral_tradeoff(self) -> None:
        rows = [
            {
                "image_path": "gesture.jpg", "expected_label": "Thumb_Up",
                "raw_predicted_label": "Thumb_Up", "score": 0.6,
                "evaluation_included": True, "hand_detected": True,
                "inference_ms": 10.0, "error": None,
            },
            {
                "image_path": "neutral.jpg", "expected_label": None,
                "raw_predicted_label": "Open_Palm", "score": 0.55,
                "evaluation_included": True, "hand_detected": True,
                "inference_ms": 10.0, "error": None,
            },
        ]
        conservative = summarize_hagrid_threshold(rows, 0.65)
        permissive = summarize_hagrid_threshold(rows, 0.5)
        self.assertEqual(conservative["gesture_recall"], 0.0)
        self.assertEqual(conservative["neutral_false_trigger_rate"], 0.0)
        self.assertEqual(permissive["gesture_recall"], 1.0)
        self.assertEqual(permissive["neutral_false_trigger_rate"], 1.0)


if __name__ == "__main__":
    unittest.main()
