from __future__ import annotations

import json
from pathlib import Path
from time import monotonic_ns

from edge_smart_home.app.cli import serialize_response
from edge_smart_home.app.pipeline import TextControlPipeline, _load_json
from edge_smart_home.vision import (
    DynamicGestureDecoder,
    GestureControlPipeline,
    GestureObservation,
    GestureTemporalFilter,
)


def main() -> None:
    config_dir = Path("configs")
    config = _load_json(config_dir / "vision.json")["gesture"]
    mappings = _load_json(config_dir / "gestures.json")
    profile = mappings.get("default_profile", "media")
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
        dynamic_decoder=DynamicGestureDecoder(
            swipe_distance=float(config.get("swipe_distance", 0.22)),
            swipe_min_duration_ms=int(config.get("swipe_min_duration_ms", 120)),
            swipe_max_duration_ms=int(config.get("swipe_max_duration_ms", 1200)),
            pinch_threshold=float(config.get("pinch_threshold", 0.22)),
            pinch_move_distance=float(config.get("pinch_move_distance", 0.12)),
            pinch_min_duration_ms=int(config.get("pinch_min_duration_ms", 180)),
            pinch_max_duration_ms=int(config.get("pinch_max_duration_ms", 1800)),
            cooldown_seconds=float(config.get("dynamic_cooldown_seconds", 1.0)),
        ),
        profile=profile,
    )
    print("输入 MediaPipe 手势标签；也可输入 profile home/media、swipe left/right 或 pinch up/down。")
    print("当前 profile：" + pipeline.profile)
    print("可用标签：" + ", ".join(pipeline.current_mappings()))
    virtual_timestamp_ms = monotonic_ns() // 1_000_000
    while True:
        value = input("> ").strip()
        if value.lower() == "q":
            break
        # Keep the dependency-free simulation deterministic and allow the
        # dynamic decoder cooldown to elapse between typed gestures.
        virtual_timestamp_ms += 2_000
        timestamp_ms = virtual_timestamp_ms
        if value.startswith("profile "):
            pipeline.set_profile(value.split(maxsplit=1)[1])
            print(f"已切换到 profile：{pipeline.profile}")
            continue
        if value.startswith("swipe "):
            direction = value.split(maxsplit=1)[1].lower()
            start_x = 0.25 if direction == "right" else 0.75
            end_x = 0.55 if direction == "right" else 0.45
            outcome = pipeline.process_observation(
                GestureObservation(None, 0.9, timestamp_ms, wrist_x=start_x, wrist_y=0.5)
            )
            outcome = pipeline.process_observation(
                GestureObservation(None, 0.9, timestamp_ms + 300, wrist_x=end_x, wrist_y=0.5)
            ) or outcome
        elif value.startswith("pinch "):
            direction = value.split(maxsplit=1)[1].lower()
            start_y = 0.65 if direction == "up" else 0.35
            end_y = 0.45 if direction == "up" else 0.55
            outcome = pipeline.process_observation(
                GestureObservation(None, 0.9, timestamp_ms, wrist_x=0.5, wrist_y=start_y, pinch_distance=0.1)
            )
            outcome = pipeline.process_observation(
                GestureObservation(None, 0.9, timestamp_ms + 300, wrist_x=0.5, wrist_y=end_y, pinch_distance=0.1)
            ) or outcome
        else:
            observation = GestureObservation(value or None, 0.9 if value else 0.0, timestamp_ms)
            outcome = pipeline.process_observation(observation)
        if outcome is not None:
            print(json.dumps(serialize_response(outcome), ensure_ascii=False))


if __name__ == "__main__":
    main()
