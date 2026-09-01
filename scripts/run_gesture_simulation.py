from __future__ import annotations

import json
from pathlib import Path
from time import monotonic_ns

from edge_smart_home.app.cli import serialize_response
from edge_smart_home.app.pipeline import TextControlPipeline, _load_json
from edge_smart_home.vision import GestureControlPipeline, GestureObservation, GestureTemporalFilter


def main() -> None:
    config_dir = Path("configs")
    config = _load_json(config_dir / "vision.json")["gesture"]
    mappings = _load_json(config_dir / "gestures.json")
    pipeline = GestureControlPipeline(
        None,
        GestureTemporalFilter(
            config["score_threshold"],
            config["min_consecutive_frames"],
            config["release_frames"],
            config["cooldown_seconds"],
            config.get("release_duration_seconds", 0.0),
        ),
        mappings,
        TextControlPipeline.from_config_dir(config_dir),
    )
    print("输入 MediaPipe 手势标签；空行模拟无手势，输入 q 退出。")
    print("可用标签：" + ", ".join(mappings))
    while True:
        value = input("> ").strip()
        if value.lower() == "q":
            break
        timestamp_ms = monotonic_ns() // 1_000_000
        observation = GestureObservation(value or None, 0.9 if value else 0.0, timestamp_ms)
        outcome = pipeline.process_observation(observation)
        if outcome is not None:
            print(json.dumps(serialize_response(outcome), ensure_ascii=False))


if __name__ == "__main__":
    main()
