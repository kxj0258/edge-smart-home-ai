from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_demo_module():
    path = PROJECT_ROOT / "scripts" / "run_assistant_demo.py"
    spec = importlib.util.spec_from_file_location("run_assistant_demo", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class AssistantDemoTest(unittest.TestCase):
    def test_parser_requires_mode_and_keeps_modality_options_isolated(self) -> None:
        parser = _load_demo_module()._build_parser()
        text = parser.parse_args(["--backend", "mock", "text"])
        gesture = parser.parse_args(
            ["--backend", "mock", "gesture", "--model", "model.task"]
        )
        self.assertEqual(text.mode, "text")
        self.assertEqual(gesture.mode, "gesture")
        self.assertEqual(gesture.model, Path("model.task"))


if __name__ == "__main__":
    unittest.main()
