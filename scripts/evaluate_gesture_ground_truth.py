from __future__ import annotations

import argparse
import json
from pathlib import Path

from edge_smart_home.benchmark.gesture_ground_truth import (
    evaluate_gesture_session,
    load_frames,
    load_ground_truth,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate a labeled Gesture session")
    parser.add_argument("session_dir", type=Path)
    parser.add_argument("--ground-truth", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    ground_truth = args.ground_truth or args.session_dir / "ground_truth.json"
    output = args.output or args.session_dir / "ground_truth_summary.json"
    summary = evaluate_gesture_session(
        load_frames(args.session_dir / "frames.jsonl"),
        load_ground_truth(ground_truth),
    )
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Gesture Ground Truth report: {output}")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
