from __future__ import annotations

import argparse
from collections import deque
import json
from pathlib import Path
from queue import Empty, Queue
from threading import Event, Thread
from time import monotonic_ns, sleep

from edge_smart_home.app import AssistantOrchestrator
from edge_smart_home.app.pipeline import _load_json
from edge_smart_home.audio.diagnostics import AudioDiagnosticRecorder
from edge_smart_home.audio.feedback import create_feedback
from edge_smart_home.audio.microphone import SoundDeviceMicrophone
from edge_smart_home.audio.pipeline import VoiceControlPipeline, VoiceControlResult
from edge_smart_home.audio.sherpa_onnx_adapters import SherpaOnnxStreamingAsr
from edge_smart_home.vision import (
    GestureControlPipeline,
    GestureTemporalFilter,
    MediaPipeGestureRecognizer,
)


def main() -> None:
    args = _build_parser().parse_args()
    root = Path(__file__).resolve().parents[1]
    config_dir = _resolve(root, args.config_dir)
    gesture_model = _resolve(root, args.gesture_model)
    asr_model_dir = _resolve(root, args.asr_model_dir)
    tts_model_dir = _resolve(root, args.tts_model_dir) if args.tts_model_dir else None
    hotwords = _resolve(root, args.hotwords)

    try:
        import cv2
    except ImportError as error:
        raise RuntimeError(
            "opencv-python is required for the live multimodal demo"
        ) from error

    os_backend = args.backend
    import os

    os.environ["EDGE_SMART_HOME_BACKEND"] = os_backend
    audio_config = _load_json(config_dir / "audio.json")
    vision_config = _load_json(config_dir / "vision.json")["gesture"]
    mappings = _load_json(config_dir / "gestures.json")
    orchestrator = AssistantOrchestrator.from_config_dir(config_dir)
    capture_blocked = Event()

    def resume_capture() -> None:
        # Let the speaker tail decay before ASR receives microphone samples again.
        sleep(args.echo_guard_seconds)
        capture_blocked.clear()

    feedback = create_feedback(
        args.feedback,
        tts_model_dir=tts_model_dir,
        speaker_id=args.tts_speaker_id,
        speed=args.tts_speed,
        audio_device=args.tts_audio_device,
        asynchronous=True,
        before_speak=capture_blocked.set,
        after_speak=resume_capture,
    )

    diagnostics = None
    if args.diagnostics_dir:
        diagnostics = AudioDiagnosticRecorder(
            _resolve(root, args.diagnostics_dir),
            {
                "kind": "live_multimodal_voice_session",
                "backend": args.backend,
                "camera": args.camera,
                "mic_device": args.mic_device,
                "microphone_sample_rate": args.mic_sample_rate,
                "asr_model_dir": str(asr_model_dir.resolve()),
                "hotwords": str(hotwords.resolve()),
                "audio_config": audio_config,
                "gesture_model": str(gesture_model.resolve()),
                "tts_model_dir": str(tts_model_dir.resolve()) if tts_model_dir else None,
                "tts_speaker_id": args.tts_speaker_id,
                "tts_speed": args.tts_speed,
                "tts_audio_device": args.tts_audio_device,
                "echo_guard_seconds": args.echo_guard_seconds,
            },
        )
        print(f"Voice diagnostics: {diagnostics.session_dir}")

    microphone = SoundDeviceMicrophone(
        int(audio_config["chunk_duration_ms"]),
        args.mic_device,
        args.mic_sample_rate or audio_config.get("microphone_sample_rate"),
        capture_blocked.is_set,
    )
    asr = SherpaOnnxStreamingAsr(
        asr_model_dir / "tokens.txt",
        asr_model_dir / "encoder.int8.onnx",
        asr_model_dir / "decoder.onnx",
        asr_model_dir / "joiner.int8.onnx",
        int(audio_config["sample_rate"]),
        audio_config["asr"],
        hotwords,
    )
    voice_pipeline = VoiceControlPipeline(
        microphone,
        asr,
        orchestrator,
        diagnostics=diagnostics,
        feedback=feedback,
    )
    voice_results: Queue[VoiceControlResult] = Queue()
    voice_errors: Queue[Exception] = Queue()

    def run_voice() -> None:
        try:
            for result in voice_pipeline.run():
                voice_results.put(result)
        except Exception as error:  # keep the camera window usable for diagnosis
            voice_errors.put(error)

    voice_thread = Thread(target=run_voice, name="live-voice-control", daemon=True)
    voice_thread.start()

    camera = cv2.VideoCapture(args.camera)
    if not camera.isOpened():
        microphone.stop()
        voice_thread.join(timeout=1.0)
        if feedback:
            feedback.close()
        if diagnostics:
            diagnostics.update_summary({
                "portaudio_overflow_count": microphone.portaudio_overflow_count,
                "queue_drop_count": microphone.queue_drop_count,
            })
            diagnostics.close()
        raise RuntimeError(f"unable to open camera {args.camera}")
    camera.set(cv2.CAP_PROP_FRAME_WIDTH, int(vision_config["camera_width"]))
    camera.set(cv2.CAP_PROP_FRAME_HEIGHT, int(vision_config["camera_height"]))
    camera.set(cv2.CAP_PROP_FPS, int(vision_config["camera_fps"]))
    camera.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    temporal_filter = GestureTemporalFilter(
        vision_config["score_threshold"],
        vision_config["min_consecutive_frames"],
        vision_config["release_frames"],
        vision_config["cooldown_seconds"],
        vision_config.get("release_duration_seconds", 0.0),
    )
    last_voice = "waiting"
    last_voice_result = "-"
    last_gesture = "none"
    last_event = "-"
    frame_times: deque[int] = deque(maxlen=30)
    last_timestamp_ms = 0

    print("Edge AI Smart Home Assistant - live multimodal window")
    print("Voice: listening in background")
    print("Gesture: camera recognition active")
    print("Press q or ESC in the camera window to stop.")

    try:
        with MediaPipeGestureRecognizer(gesture_model, vision_config) as recognizer:
            gesture_pipeline = GestureControlPipeline(
                recognizer,
                temporal_filter,
                mappings,
                orchestrator,
                feedback,
            )
            while True:
                success, frame = camera.read()
                if not success:
                    continue
                if vision_config.get("mirror_preview", True):
                    frame = cv2.flip(frame, 1)
                rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                now_ms = monotonic_ns() // 1_000_000
                last_timestamp_ms = max(last_timestamp_ms + 1, now_ms)
                frame_times.append(last_timestamp_ms)

                for observation, response in gesture_pipeline.process_frame(
                    rgb_frame, last_timestamp_ms
                ):
                    if observation.label:
                        last_gesture = f"{observation.label} {observation.score:.2f}"
                    if response is not None:
                        last_event = f"gesture {response.status} {response.path}"
                        print(
                            json.dumps(
                                {
                                    "source": "gesture",
                                    "label": response.event.label
                                    if response.event is not None
                                    else None,
                                    "status": response.status,
                                    "path": response.path,
                                    "provider": response.provider,
                                    "llm_calls": response.llm_calls,
                                    "processing_ms": response.processing_ms,
                                },
                                ensure_ascii=False,
                            ),
                            flush=True,
                        )

                while True:
                    try:
                        result = voice_results.get_nowait()
                    except Empty:
                        break
                    last_voice = result.text
                    last_voice_result = f"{result.status} {result.path}"
                    last_event = f"voice {result.status} {result.path}"
                    print(_voice_result_payload(result), flush=True)

                try:
                    error = voice_errors.get_nowait()
                except Empty:
                    error = None
                if error is not None:
                    last_voice_result = f"error: {type(error).__name__}"
                    print(f"Voice pipeline stopped: {error}", flush=True)

                fps = _fps(frame_times)
                _draw_overlay(
                    cv2,
                    frame,
                    fps,
                    last_voice,
                    last_voice_result,
                    last_gesture,
                    last_event,
                )
                cv2.imshow("Edge AI Smart Home Assistant", frame)
                key = cv2.waitKey(1) & 0xFF
                if key in (ord("q"), 27):
                    break
    finally:
        camera.release()
        cv2.destroyAllWindows()
        microphone.stop()
        voice_thread.join(timeout=2.0)
        if feedback:
            feedback.close()
        if diagnostics:
            diagnostics.update_summary({
                "portaudio_overflow_count": microphone.portaudio_overflow_count,
                "queue_drop_count": microphone.queue_drop_count,
            })
            diagnostics.close()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Live camera gesture + microphone voice assistant window"
    )
    parser.add_argument("--backend", choices=("mock", "home_assistant"), default="mock")
    parser.add_argument("--config-dir", type=Path, default=Path("configs"))
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument(
        "--gesture-model", type=Path, default=Path("models/gesture_recognizer.task")
    )
    parser.add_argument(
        "--asr-model-dir",
        type=Path,
        default=Path("models/sherpa-onnx-streaming-zipformer-zh-xlarge-int8-2025-06-30"),
    )
    parser.add_argument("--mic-device", type=int)
    parser.add_argument("--mic-sample-rate", type=int)
    parser.add_argument("--hotwords", type=Path, default=Path("configs/hotwords_v2.txt"))
    parser.add_argument(
        "--diagnostics-dir",
        type=Path,
        help="Save raw microphone audio, recognized events, and ASR metrics",
    )
    parser.add_argument(
        "--feedback", choices=("none", "console", "tts"), default="console"
    )
    parser.add_argument("--tts-model-dir", type=Path)
    parser.add_argument("--tts-speaker-id", type=int, default=0)
    parser.add_argument("--tts-speed", type=float, default=1.0)
    parser.add_argument("--tts-audio-device", type=int)
    parser.add_argument(
        "--echo-guard-seconds",
        type=float,
        default=0.4,
        help="Ignore microphone samples briefly after TTS playback",
    )
    return parser


def _resolve(root: Path, value: Path) -> Path:
    return value if value.is_absolute() else root / value


def _fps(frame_times: deque[int]) -> float:
    if len(frame_times) < 2:
        return 0.0
    elapsed_ms = frame_times[-1] - frame_times[0]
    if elapsed_ms <= 0:
        return 0.0
    return (len(frame_times) - 1) * 1000.0 / elapsed_ms


def _draw_overlay(
    cv2,
    frame,
    fps: float,
    last_voice: str,
    last_voice_result: str,
    last_gesture: str,
    last_event: str,
) -> None:
    lines = (
        "Edge AI Smart Home Assistant",
        f"FPS: {fps:.1f} | Press Q/ESC to quit",
        f"VOICE: {last_voice_result}",
        f"GESTURE: {last_gesture}",
        f"EVENT: {last_event}",
    )
    for index, line in enumerate(lines):
        cv2.putText(
            frame,
            line[:100],
            (16, 30 + index * 28),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.58,
            (0, 255, 0),
            2,
            cv2.LINE_AA,
        )


def _voice_result_payload(result: VoiceControlResult) -> str:
    return json.dumps(
        {
            "source": "voice",
            "text": result.text,
            "status": result.status,
            "path": result.path,
            "provider": result.provider,
            "llm_calls": result.llm_calls,
            "llm_latency_ms": result.llm_latency_ms,
            "processing_ms": result.processing_ms,
            "error": result.error,
        },
        ensure_ascii=False,
    )


if __name__ == "__main__":
    main()
