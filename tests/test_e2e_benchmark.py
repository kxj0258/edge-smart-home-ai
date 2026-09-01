from __future__ import annotations

import unittest

from edge_smart_home.benchmark.e2e import summarize_e2e


class E2eBenchmarkTest(unittest.TestCase):
    def test_summary_uses_real_rows_and_scene_device_counts(self) -> None:
        summary = summarize_e2e(
            [
                {
                    "status": "executed",
                    "processing_ms": 1.0,
                    "end_to_end_ms": 2.0,
                    "device_attempts": 1,
                    "device_successes": 1,
                },
                {
                    "status": "partial_failure",
                    "processing_ms": 3.0,
                    "end_to_end_ms": 4.0,
                    "device_attempts": 5,
                    "device_successes": 4,
                },
            ]
        )
        self.assertEqual(summary["command_success_rate"], 0.5)
        self.assertEqual(summary["device_command_success_rate"], 0.8333)
        self.assertEqual(summary["mean_end_to_end_ms"], 3.0)
        self.assertEqual(summary["p95_end_to_end_ms"], 3.9)

    def test_unexecuted_perception_rows_do_not_report_zero_execution_accuracy(self) -> None:
        summary = summarize_e2e(
            [
                {
                    "status": "semantic_match",
                    "processing_ms": None,
                    "end_to_end_ms": None,
                    "device_attempts": 0,
                    "device_successes": 0,
                }
            ]
        )
        self.assertEqual(summary["execution_evaluated"], 0)
        self.assertIsNone(summary["command_success_rate"])


if __name__ == "__main__":
    unittest.main()
