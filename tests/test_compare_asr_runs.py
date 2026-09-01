from __future__ import annotations

import unittest

from scripts.compare_asr_runs import render_comparison


class CompareAsrRunsTest(unittest.TestCase):
    def test_render_comparison(self):
        report = render_comparison(
            [
                {
                    "run": "baseline",
                    "decoding_method": "greedy_search",
                    "hotwords_enabled": False,
                    "hotwords_score": 1.5,
                    "exact_match_accuracy": 0.5,
                    "cer": 0.2,
                    "router_accuracy": 0.7,
                    "mean_rtf": 0.08,
                }
            ]
        )
        self.assertIn("baseline", report)
        self.assertIn("50.00%", report)
        self.assertIn("greedy_search", report)


if __name__ == "__main__":
    unittest.main()
