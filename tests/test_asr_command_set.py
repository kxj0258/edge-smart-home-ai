from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from edge_smart_home.benchmark.asr_command_set import load_commands, load_failed_commands


class AsrCommandSetTest(unittest.TestCase):
    def test_load_commands_ignores_blank_lines_and_comments(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "commands.txt"
            path.write_text("# comment\n打开电视\n\n  关闭电视  \n", encoding="utf-8")

            self.assertEqual(load_commands(path), ["打开电视", "关闭电视"])

    def test_load_failed_commands_is_unique_and_keeps_order(self):
        rows = [
            {"expected": "打开电视", "exact_match": True},
            {"expected": "打开窗帘", "exact_match": False},
            {"expected": "打开窗帘", "exact_match": False},
            {"expected": "风速调高", "exact_match": False},
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.jsonl"
            path.write_text(
                "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                encoding="utf-8",
            )

            self.assertEqual(load_failed_commands(path), ["打开窗帘", "风速调高"])


if __name__ == "__main__":
    unittest.main()
