from __future__ import annotations

import unittest

from edge_smart_home.benchmark.asr_evaluation import (
    classify_route_mismatch,
    edit_distance,
    normalize_text,
    summarize,
    text_metrics,
)


class AsrEvaluationTest(unittest.TestCase):
    def test_classify_parameter_and_opposite_action_mismatches(self):
        expected = {
            "kind": "device_command",
            "device_id": "air_conditioner",
            "action": "set_temperature",
            "arguments": {"temperature": 26},
        }
        recognized = {**expected, "arguments": {"temperature": 16}}
        self.assertEqual(
            classify_route_mismatch(expected, recognized), "parameter_mismatch"
        )
        self.assertEqual(
            classify_route_mismatch(
                {"kind": "device_command", "device_id": "tv", "action": "play"},
                {"kind": "device_command", "device_id": "tv", "action": "pause"},
            ),
            "opposite_action_mismatch",
        )

    def test_normalize_and_character_metrics(self):
        self.assertEqual(normalize_text(" 打开，电视！"), "打开电视")
        self.assertEqual(edit_distance("打开电视", "打开电机"), 1)
        self.assertEqual(
            text_metrics("打开电视", "打开电机"),
            {
                "exact_match": False,
                "character_edits": 1,
                "reference_characters": 4,
                "cer": 0.25,
            },
        )

    def test_summary_uses_micro_cer_and_groups(self):
        rows = [
            {
                "expected": "打开电视",
                "speaker_id": 0,
                "speed": 1.0,
                "exact_match": True,
                "character_edits": 0,
                "reference_characters": 4,
                "router_match": True,
                "router_outcome": "semantic_match",
                "semantic_mismatch_type": None,
                "rtf": 0.1,
            },
            {
                "expected": "关闭电视",
                "speaker_id": 1,
                "speed": 1.1,
                "exact_match": False,
                "character_edits": 2,
                "reference_characters": 4,
                "router_match": False,
                "router_outcome": "recognized_unroutable",
                "semantic_mismatch_type": None,
                "rtf": 0.2,
            },
        ]

        summary = summarize(rows)

        self.assertEqual(summary["overall"]["exact_match_accuracy"], 0.5)
        self.assertEqual(summary["overall"]["cer"], 0.25)
        self.assertEqual(summary["overall"]["router_accuracy"], 0.5)
        self.assertEqual(summary["overall"]["expected_route_coverage"], 1.0)
        self.assertEqual(
            summary["overall"]["router_outcomes"]["recognized_unroutable"], 1
        )
        self.assertEqual(summary["by_speaker"]["0"]["recordings"], 1)


if __name__ == "__main__":
    unittest.main()
