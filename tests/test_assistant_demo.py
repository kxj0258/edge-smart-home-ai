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


def _load_gesture_module():
    path = PROJECT_ROOT / "scripts" / "run_gesture_demo.py"
    spec = importlib.util.spec_from_file_location("run_gesture_demo", path)
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

    def test_gesture_parser_accepts_experimental_threshold(self) -> None:
        parser = _load_demo_module()._build_parser()
        gesture = parser.parse_args(
            [
                "--backend", "mock", "gesture", "--model", "model.task",
                "--score-threshold", "0.60",
                "--gesture-profile", "home",
                "--gesture-activation",
                "--guided-capture",
            ]
        )
        self.assertEqual(gesture.score_threshold, 0.6)
        self.assertTrue(gesture.guided_capture)
        self.assertEqual(gesture.gesture_profile, "home")
        self.assertTrue(gesture.gesture_activation)

    def test_video_fps_uses_processed_frame_timestamps(self) -> None:
        frames = [(None, 1_000 + index * 100) for index in range(11)]
        self.assertEqual(_load_gesture_module()._estimate_video_fps(frames, 30.0), 10.0)


if __name__ == "__main__":
    unittest.main()
