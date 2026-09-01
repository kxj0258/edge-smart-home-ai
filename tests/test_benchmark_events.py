from __future__ import annotations

import unittest

from edge_smart_home.benchmark.events import BenchmarkEventRecord


class BenchmarkEventRecordTest(unittest.TestCase):
    def test_nullable_unmeasured_latency_is_allowed(self) -> None:
        record = BenchmarkEventRecord(
            source="voice",
            input="打开电视",
            status="executed",
            perception_ms=25.0,
            end_to_end_ms=None,
            device_attempts=1,
            device_successes=1,
            metrics={"rtf": 0.1},
        )
        self.assertIsNone(record.to_dict()["end_to_end_ms"])

    def test_invalid_device_counts_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            BenchmarkEventRecord(
                source="gesture",
                input="Thumb_Up",
                status="executed",
                device_attempts=0,
                device_successes=1,
            )


if __name__ == "__main__":
    unittest.main()
