from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
from typing import Callable


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    os.environ["EDGE_SMART_HOME_BACKEND"] = args.backend

    if args.mode == "text":
        from edge_smart_home.app.cli import main as run

        _launch(run, ["edge-smart-home"])
    elif args.mode == "text-benchmark":
        from benchmark_text_e2e import main as run

        _launch(
            run,
            [
                "benchmark_text_e2e.py",
                "--commands",
                str(args.commands),
                "--config-dir",
                str(args.config_dir),
                "--output-dir",
                str(args.output_dir),
            ],
        )
    elif args.mode == "gesture-sim":
        from run_gesture_simulation import main as run

        _launch(run, ["run_gesture_simulation.py"])
    elif args.mode == "gesture":
        from run_gesture_demo import main as run

        forwarded = [
            "run_gesture_demo.py",
            "--model",
            str(args.model),
            "--camera",
            str(args.camera),
            "--config-dir",
            str(args.config_dir),
        ]
        if args.diagnostics_dir:
            forwarded.extend(["--diagnostics-dir", str(args.diagnostics_dir)])
        if args.score_threshold is not None:
            forwarded.extend(["--score-threshold", str(args.score_threshold)])
        if args.guided_capture:
            forwarded.append("--guided-capture")
        forwarded.extend(
            [
                "--feedback",
                args.feedback,
                "--tts-speaker-id",
                str(args.tts_speaker_id),
                "--tts-speed",
                str(args.tts_speed),
            ]
        )
        if args.tts_audio_device is not None:
            forwarded.extend(["--tts-audio-device", str(args.tts_audio_device)])
        if args.tts_model_dir:
            forwarded.extend(["--tts-model-dir", str(args.tts_model_dir)])
        _launch(run, forwarded)
    elif args.mode == "voice":
        from run_voice_demo import main as run

        forwarded = [
            "run_voice_demo.py",
            "--tokens",
            str(args.tokens),
            "--encoder",
            str(args.encoder),
            "--decoder",
            str(args.decoder),
            "--joiner",
            str(args.joiner),
        ]
        if args.device is not None:
            forwarded.extend(["--device", str(args.device)])
        if args.mic_sample_rate is not None:
            forwarded.extend(["--mic-sample-rate", str(args.mic_sample_rate)])
        if args.hotwords:
            forwarded.extend(["--hotwords", str(args.hotwords)])
        if args.diagnostics_dir:
            forwarded.extend(["--diagnostics-dir", str(args.diagnostics_dir)])
        forwarded.extend(
            [
                "--feedback",
                args.feedback,
                "--tts-speaker-id",
                str(args.tts_speaker_id),
                "--tts-speed",
                str(args.tts_speed),
            ]
        )
        if args.tts_audio_device is not None:
            forwarded.extend(["--tts-audio-device", str(args.tts_audio_device)])
        if args.tts_model_dir:
            forwarded.extend(["--tts-model-dir", str(args.tts_model_dir)])
        _launch(run, forwarded)
    else:  # pragma: no cover - argparse enforces the subcommands
        parser.error(f"unsupported mode: {args.mode}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Unified Edge AI Smart Home Assistant product demo"
    )
    parser.add_argument(
        "--backend", choices=("mock", "home_assistant"), default="mock"
    )
    subparsers = parser.add_subparsers(dest="mode", required=True)
    subparsers.add_parser("text", help="Interactive text control")
    subparsers.add_parser("gesture-sim", help="Dependency-free gesture simulation")

    text_benchmark = subparsers.add_parser(
        "text-benchmark", help="Run the reproducible text command benchmark"
    )
    text_benchmark.add_argument(
        "--commands", type=Path, default=Path("configs/asr_test_commands.txt")
    )
    text_benchmark.add_argument("--config-dir", type=Path, default=Path("configs"))
    text_benchmark.add_argument(
        "--output-dir", type=Path, default=Path("evaluation/e2e_runs")
    )

    gesture = subparsers.add_parser("gesture", help="Real MediaPipe camera control")
    gesture.add_argument("--model", type=Path, required=True)
    gesture.add_argument("--camera", type=int, default=0)
    gesture.add_argument("--config-dir", type=Path, default=Path("configs"))
    gesture.add_argument("--diagnostics-dir", type=Path)
    gesture.add_argument(
        "--score-threshold",
        type=float,
        help="Override the configured Gesture threshold for this run only",
    )
    gesture.add_argument("--guided-capture", action="store_true")
    _add_feedback_args(gesture)

    voice = subparsers.add_parser("voice", help="Real sherpa-onnx microphone control")
    voice.add_argument("--tokens", type=Path, required=True)
    voice.add_argument("--encoder", type=Path, required=True)
    voice.add_argument("--decoder", type=Path, required=True)
    voice.add_argument("--joiner", type=Path, required=True)
    voice.add_argument("--device", type=int)
    voice.add_argument("--mic-sample-rate", type=int)
    voice.add_argument("--hotwords", type=Path)
    voice.add_argument("--diagnostics-dir", type=Path)
    _add_feedback_args(voice)
    return parser


def _add_feedback_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--feedback",
        choices=("none", "console", "tts"),
        default="console",
    )
    parser.add_argument("--tts-model-dir", type=Path)
    parser.add_argument("--tts-speaker-id", type=int, default=0)
    parser.add_argument("--tts-speed", type=float, default=1.0)
    parser.add_argument("--tts-audio-device", type=int)


def _launch(entrypoint: Callable[[], None], argv: list[str]) -> None:
    previous = sys.argv
    try:
        sys.argv = argv
        entrypoint()
    finally:
        sys.argv = previous


if __name__ == "__main__":
    main()
