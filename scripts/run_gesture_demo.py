from __future__ import annotations

import argparse
from collections import deque
import json
from pathlib import Path
from time import monotonic, monotonic_ns, perf_counter

from edge_smart_home.app import AssistantOrchestrator
from edge_smart_home.app.cli import serialize_response
from edge_smart_home.app.pipeline import _load_json
from edge_smart_home.vision import (
    GestureControlPipeline,
    GestureDiagnosticRecorder,
    GestureTemporalFilter,
    MediaPipeGestureRecognizer,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="MediaPipe camera gesture demo")
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--config-dir", type=Path, default=Path("configs"))
    parser.add_argument(
        "--diagnostics-dir",
        type=Path,
        help="Save measured frame, inference, filter, and trigger metrics",
    )
    args = parser.parse_args()
    try:
        import cv2
    except ImportError as error:
        raise RuntimeError("opencv-python is required for the camera demo") from error

    vision_config = _load_json(args.config_dir / "vision.json")["gesture"]
    mappings = _load_json(args.config_dir / "gestures.json")
    control = AssistantOrchestrator.from_config_dir(args.config_dir)
    temporal_filter = GestureTemporalFilter(
        vision_config["score_threshold"],
        vision_config["min_consecutive_frames"],
        vision_config["release_frames"],
        vision_config["cooldown_seconds"],
        vision_config.get("release_duration_seconds", 0.0),
    )
    camera = cv2.VideoCapture(args.camera)
    if not camera.isOpened():
        raise RuntimeError(f"unable to open camera {args.camera}")
    camera.set(cv2.CAP_PROP_FRAME_WIDTH, int(vision_config["camera_width"]))
    camera.set(cv2.CAP_PROP_FRAME_HEIGHT, int(vision_config["camera_height"]))
    camera.set(cv2.CAP_PROP_FPS, int(vision_config["camera_fps"]))
    camera.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    actual_camera = {
        "width": int(camera.get(cv2.CAP_PROP_FRAME_WIDTH)),
        "height": int(camera.get(cv2.CAP_PROP_FRAME_HEIGHT)),
        "fps": round(float(camera.get(cv2.CAP_PROP_FPS)), 3),
    }
    diagnostics = None
    if args.diagnostics_dir:
        diagnostics = GestureDiagnosticRecorder(
            args.diagnostics_dir,
            {
                "model": str(args.model.resolve()),
                "camera_index": args.camera,
                "camera": actual_camera,
                "vision_config": vision_config,
                "gesture_mappings": mappings,
            },
        )
        print(f"Diagnostics: {diagnostics.session_dir}")
    print(f"Camera: {json.dumps(actual_camera, ensure_ascii=False)}")
    print("Gesture demo started. Press ESC to stop.")
    started_at = monotonic()
    frame_times: deque[float] = deque(maxlen=60)
    consecutive_read_failures = 0
    total_read_failures = 0
    last_trigger = "-"
    try:
        with MediaPipeGestureRecognizer(args.model, vision_config) as recognizer:
            pipeline = GestureControlPipeline(
                recognizer, temporal_filter, mappings, control
            )
            while True:
                success, frame = camera.read()
                if not success:
                    consecutive_read_failures += 1
                    total_read_failures += 1
                    if consecutive_read_failures >= 5:
                        raise RuntimeError("unable to read 5 consecutive camera frames")
                    continue
                consecutive_read_failures = 0
                now = monotonic()
                frame_times.append(now)
                if vision_config.get("mirror_preview", True):
                    frame = cv2.flip(frame, 1)
                rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                timestamp_ms = monotonic_ns() // 1_000_000
                inference_started = perf_counter()
                frame_results = pipeline.process_frame(
                    rgb_frame, timestamp_ms
                )
                inference_ms = round((perf_counter() - inference_started) * 1000, 3)
                observation_rows = []
                response_rows = []
                display_observation = None
                for observation, outcome in frame_results:
                    observation_rows.append(
                        {
                            "label": observation.label,
                            "score": round(observation.score, 4),
                            "handedness": observation.handedness,
                        }
                    )
                    if display_observation is None or observation.score > display_observation.score:
                        display_observation = observation
                    if outcome is not None:
                        payload = serialize_response(outcome)
                        response_rows.append(payload)
                        last_trigger = f"{outcome.event.label}: {outcome.status}"
                        print(json.dumps(payload, ensure_ascii=False))
                if diagnostics:
                    diagnostics.record_frame(
                        timestamp_ms,
                        inference_ms,
                        observation_rows,
                        response_rows,
                    )
                if vision_config.get("overlay", True):
                    _draw_overlay(
                        cv2,
                        frame,
                        display_observation,
                        _rolling_fps(frame_times),
                        inference_ms,
                        last_trigger,
                        temporal_filter.decision_counts,
                    )
                cv2.imshow("Edge Smart Home Gesture", frame)
                if cv2.waitKey(1) == 27:
                    break
    except KeyboardInterrupt:
        print("\nGesture demo stopped.")
    finally:
        camera.release()
        cv2.destroyAllWindows()
        if diagnostics:
            diagnostics.close(
                monotonic() - started_at,
                temporal_filter.decision_counts,
                total_read_failures,
            )


def _rolling_fps(frame_times: deque[float]) -> float:
    if len(frame_times) < 2:
        return 0.0
    elapsed = frame_times[-1] - frame_times[0]
    return (len(frame_times) - 1) / elapsed if elapsed > 0 else 0.0


def _draw_overlay(
    cv2,
    frame,
    observation,
    fps: float,
    inference_ms: float,
    last_trigger: str,
    filter_counts,
) -> None:
    gesture = "None"
    if observation is not None and observation.label:
        gesture = f"{observation.label} {observation.score:.2f}"
    lines = [
        f"Gesture: {gesture}",
        f"FPS: {fps:.1f}  Inference: {inference_ms:.1f} ms",
        f"Triggered: {filter_counts.get('triggered', 0)}  "
        f"Cooldown: {filter_counts.get('cooldown', 0)}  "
        f"Latched: {filter_counts.get('latched', 0)}",
        f"Last: {last_trigger}",
    ]
    for index, line in enumerate(lines):
        cv2.putText(
            frame,
            line,
            (20, 35 + index * 30),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            (0, 255, 0),
            2,
        )


if __name__ == "__main__":
    main()
