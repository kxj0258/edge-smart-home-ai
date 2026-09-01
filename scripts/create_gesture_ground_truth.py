from __future__ import annotations

import argparse
import json
from pathlib import Path

from edge_smart_home.benchmark.gesture_ground_truth import build_template, load_frames


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a Gesture Ground Truth template")
    parser.add_argument("session_dir", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    frames = load_frames(args.session_dir / "frames.jsonl")
    output = args.output or args.session_dir / "ground_truth.json"
    if output.exists():
        raise FileExistsError(f"refusing to overwrite existing annotation: {output}")
    output.write_text(
        json.dumps(build_template(frames, args.session_dir), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Ground Truth template: {output}")


if __name__ == "__main__":
    main()
