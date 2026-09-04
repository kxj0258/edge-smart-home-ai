from __future__ import annotations

import argparse
from collections import deque
import json
from pathlib import Path
from time import monotonic, monotonic_ns, perf_counter

from edge_smart_home.app import AssistantOrchestrator
from edge_smart_home.app.cli import serialize_response
from edge_smart_home.audio.feedback import create_feedback
from edge_smart_home.app.pipeline import _load_json
from edge_smart_home.benchmark.gesture_guided import GuidedGestureProtocol
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
        "--score-threshold",
        type=float,
        help="Override the configured score threshold for this run only",
    )
    parser.add_argument(
        "--diagnostics-dir",
        type=Path,
        help="Save measured frame, inference, filter, and trigger metrics",
    )
    parser.add_argument(
        "--guided-capture",
        action="store_true",
        help="Run the fixed six-gesture Ground Truth capture protocol",
    )
    parser.add_argument(
        "--feedback",
        choices=("none", "console", "tts"),
        default="console",
        help="Acknowledge and report triggered gestures through console or offline TTS",
    )
    parser.add_argument("--tts-model-dir", type=Path)
    parser.add_argument("--tts-speaker-id", type=int, default=0)
    parser.add_argument("--tts-speed", type=float, default=1.0)
    parser.add_argument("--tts-audio-device", type=int)
    args = parser.parse_args()
    if args.guided_capture and not args.diagnostics_dir:
        parser.error("--guided-capture requires --diagnostics-dir")
    try:
        import cv2
    except ImportError as error:
        raise RuntimeError("opencv-python is required for the camera demo") from error

    vision_config = _load_json(args.config_dir / "vision.json")["gesture"]
    if args.score_threshold is not None:
        if not 0.0 <= args.score_threshold <= 1.0:
            parser.error("--score-threshold must be between 0 and 1")
        vision_config["score_threshold"] = args.score_threshold
    mappings = _load_json(args.config_dir / "gestures.json")
    control = AssistantOrchestrator.from_config_dir(args.config_dir)
    _preload_model(control)
    feedback = create_feedback(
        args.feedback,
        tts_model_dir=args.tts_model_dir,
        speaker_id=args.tts_speaker_id,
        speed=args.tts_speed,
        audio_device=args.tts_audio_device,
    )
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
                "guided_capture": args.guided_capture,
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
    guided_protocol = None
    guided_phase_index = None
    guided_completed = False
    last_timestamp_ms = None
    video_writer = None
    video_pending_frames = []
    video_nominal_fps = None
    video_frames_seen = 0
    video_frames_written = 0
    video_first_timestamp_ms = None
    video_last_timestamp_ms = None
    video_path = diagnostics.session_dir / "camera.mp4" if args.guided_capture else None
    try:
        with MediaPipeGestureRecognizer(args.model, vision_config) as recognizer:
            pipeline = GestureControlPipeline(
                recognizer, temporal_filter, mappings, control, feedback
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
                last_timestamp_ms = timestamp_ms
                video_frame_index = None
                if args.guided_capture:
                    video_frame_index = video_frames_seen
                    video_frames_seen += 1
                    if video_writer is None:
                        video_pending_frames.append((frame.copy(), timestamp_ms))
                        if len(video_pending_frames) >= 60:
                            video_nominal_fps = _estimate_video_fps(
                                video_pending_frames,
                                float(actual_camera["fps"] or vision_config["camera_fps"]),
                            )
                            video_writer = _open_video_writer(
                                cv2, video_path, frame, video_nominal_fps
                            )
                            for pending_frame, _ in video_pending_frames:
                                video_writer.write(pending_frame)
                                video_frames_written += 1
                            video_pending_frames.clear()
                    else:
                        video_writer.write(frame)
                        video_frames_written += 1
                    if video_first_timestamp_ms is None:
                        video_first_timestamp_ms = timestamp_ms
                    video_last_timestamp_ms = timestamp_ms
                guided_state = None
                if args.guided_capture:
                    if guided_protocol is None:
                        guided_protocol = GuidedGestureProtocol(timestamp_ms)
                    guided_state = guided_protocol.state(timestamp_ms)
                    if guided_state is not None and guided_state.index != guided_phase_index:
                        guided_phase_index = guided_state.index
                        print(f"Guided: {guided_state.prompt}")
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
                        video_frame_index,
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
                        guided_state,
                    )
                cv2.imshow("Edge Smart Home Gesture", frame)
                if cv2.waitKey(1) == 27:
                    break
                if args.guided_capture and guided_state is None:
                    guided_completed = True
                    print("Guided capture complete.")
                    break
    except KeyboardInterrupt:
        print("\nGesture demo stopped.")
    finally:
        camera.release()
        cv2.destroyAllWindows()
        if args.guided_capture and video_writer is None and video_pending_frames:
            video_nominal_fps = _estimate_video_fps(
                video_pending_frames,
                float(actual_camera["fps"] or vision_config["camera_fps"]),
            )
            video_writer = _open_video_writer(
                cv2, video_path, video_pending_frames[0][0], video_nominal_fps
            )
            for pending_frame, _ in video_pending_frames:
                video_writer.write(pending_frame)
                video_frames_written += 1
            video_pending_frames.clear()
        if video_writer is not None:
            video_writer.release()
        if diagnostics:
            diagnostics.close(
                monotonic() - started_at,
                temporal_filter.decision_counts,
                total_read_failures,
            )
            if guided_protocol is not None and last_timestamp_ms is not None:
                annotation = guided_protocol.annotation(
                    diagnostics.session_dir, last_timestamp_ms, guided_completed
                )
                filename = (
                    "ground_truth.json"
                    if guided_completed
                    else "guided_ground_truth.partial.json"
                )
                (diagnostics.session_dir / filename).write_text(
                    json.dumps(annotation, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                print(f"Guided Ground Truth: {diagnostics.session_dir / filename}")
                video_metadata = {
                    "video_file": video_path.name,
                    "codec": "mp4v",
                    "container": "mp4",
                    "nominal_fps": video_nominal_fps,
                    "nominal_fps_source": "first_60_processed_frame_timestamps",
                    "frames_written": video_frames_written,
                    "first_timestamp_ms": video_first_timestamp_ms,
                    "last_timestamp_ms": video_last_timestamp_ms,
                    "frame_content": "mirrored camera frame before diagnostic overlay",
                    "synchronization": (
                        "Use frames.jsonl video_frame_index and timestamp_ms; "
                        "player time is not the canonical capture clock."
                    ),
                }
                (diagnostics.session_dir / "video_metadata.json").write_text(
                    json.dumps(video_metadata, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                print(f"Guided Video: {video_path}")


def _preload_model(orchestrator: AssistantOrchestrator) -> None:
    try:
        latency_ms = orchestrator.preload_model()
    except Exception as error:
        print(f"小兴模型预热失败，将在需要时安全降级：{type(error).__name__}: {error}")
    else:
        if latency_ms is not None:
            print(f"小兴模型已预热：{latency_ms:.1f} ms")


def _estimate_video_fps(frames, fallback_fps: float) -> float:
    if len(frames) < 2:
        return fallback_fps
    elapsed_seconds = (frames[-1][1] - frames[0][1]) / 1000
    if elapsed_seconds <= 0:
        return fallback_fps
    measured = (len(frames) - 1) / elapsed_seconds
    return round(min(max(measured, 1.0), fallback_fps), 3)


def _open_video_writer(cv2, path: Path, frame, fps: float):
    height, width = frame.shape[:2]
    writer = cv2.VideoWriter(
        str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height)
    )
    if not writer.isOpened():
        raise RuntimeError(f"unable to create guided video: {path}")
    return writer


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
    guided_state=None,
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
    if guided_state is not None:
        lines.append(
            f"GUIDED: {guided_state.overlay_prompt}  "
            f"{guided_state.remaining_ms / 1000:.1f}s"
        )
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
