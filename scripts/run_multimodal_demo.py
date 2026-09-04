from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from time import monotonic_ns

from edge_smart_home.app import AssistantOrchestrator
from edge_smart_home.app.cli import serialize_response
from edge_smart_home.audio.feedback import create_feedback
from edge_smart_home.common import EventSource, PerceptionEvent
from edge_smart_home.app.pipeline import _load_json
from edge_smart_home.vision import (
    GestureControlPipeline,
    GestureObservation,
    GestureTemporalFilter,
)


def main() -> None:
    args = _build_parser().parse_args()
    os.environ["EDGE_SMART_HOME_BACKEND"] = args.backend
    config_dir = args.config_dir.resolve()
    orchestrator = AssistantOrchestrator.from_config_dir(config_dir)
    feedback = create_feedback(
        args.feedback,
        tts_model_dir=args.tts_model_dir,
        speaker_id=args.tts_speaker_id,
        speed=args.tts_speed,
        audio_device=args.tts_audio_device,
    )
    try:
        gesture_pipeline = _build_gesture_pipeline(
            config_dir, orchestrator, feedback
        )
        print("Edge AI 多模态联动 Demo")
        print("共享一个 AssistantOrchestrator 和 Mock DeviceBackend。")
        print("输入格式：text <文本> | voice <ASR文本> | gesture <手势标签> | q")
        print("支持简单单轮对话：问候、能力询问和一般说明不会执行设备操作。")
        print("语音反馈模式：" + args.feedback)
        if args.scripted:
            _run_scripted(orchestrator, gesture_pipeline, feedback)
            return
        _run_interactive(orchestrator, gesture_pipeline, feedback)
    finally:
        feedback.close()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Shared-state Text/Voice/Gesture demonstration"
    )
    parser.add_argument("--backend", choices=("mock", "home_assistant"), default="mock")
    parser.add_argument("--config-dir", type=Path, default=Path("configs"))
    parser.add_argument("--scripted", action="store_true")
    parser.add_argument(
        "--feedback", choices=("none", "console", "tts"), default="console"
    )
    parser.add_argument("--tts-model-dir", type=Path)
    parser.add_argument("--tts-speaker-id", type=int, default=0)
    parser.add_argument("--tts-speed", type=float, default=1.0)
    parser.add_argument(
        "--tts-audio-device",
        type=int,
        help="PortAudio output device index; omit to use the system default",
    )
    return parser


def _build_gesture_pipeline(config_dir, orchestrator, feedback):
    vision = _load_json(config_dir / "vision.json")["gesture"]
    mappings = _load_json(config_dir / "gestures.json")
    return GestureControlPipeline(
        None,
        GestureTemporalFilter(
            vision["score_threshold"],
            vision["min_consecutive_frames"],
            vision["release_frames"],
            vision["cooldown_seconds"],
            vision.get("release_duration_seconds", 0.0),
        ),
        mappings,
        orchestrator,
        feedback,
    )


def _run_scripted(orchestrator, gesture_pipeline, feedback) -> None:
    print("开始固定联动流程：Gesture → Dialogue → Voice → Voice → Gesture → Text")
    scripted_events = (
        ("gesture", "Victory"),
        ("text", "你好"),
        ("text", "你能控制哪些设备"),
        ("voice", "客厅有点暗，帮我处理一下"),
        ("voice", "这里有点暗，帮我处理一下"),
        ("gesture", "Closed_Fist"),
        ("text", "播放网络音乐"),
        ("text", "把电视音量设置为1000"),
    )
    for kind, value in scripted_events:
        print(f"\n[{kind}] {value}")
        response = _dispatch(
            kind, value, orchestrator, gesture_pipeline, feedback
        )
        if response is not None:
            print(json.dumps(serialize_response(response), ensure_ascii=False))


def _run_interactive(orchestrator, gesture_pipeline, feedback) -> None:
    while True:
        line = input("> ").strip()
        if line.lower() in {"q", "quit", "exit", "退出"}:
            return
        if not line:
            continue
        try:
            kind, value = line.split(maxsplit=1)
        except ValueError:
            print("请输入：text <文本>、voice <ASR文本> 或 gesture <标签>")
            continue
        response = _dispatch(kind, value, orchestrator, gesture_pipeline, feedback)
        if response is not None:
            print(json.dumps(serialize_response(response), ensure_ascii=False))


def _dispatch(kind, value, orchestrator, gesture_pipeline, feedback):
    if kind == "gesture":
        return _dispatch_gesture(value, gesture_pipeline)
    if kind not in {"text", "voice"}:
        print("未知输入类型：请使用 text、voice 或 gesture")
        return None
    if feedback:
        feedback.received(value)
    event = PerceptionEvent(
        source=EventSource.TEXT if kind == "text" else EventSource.VOICE,
        label=value,
    )
    response = orchestrator.handle_event(event)
    if feedback:
        feedback.response(response)
    return response


def _dispatch_gesture(value, gesture_pipeline):
    timestamp_ms = monotonic_ns() // 1_000_000
    response = None
    for offset in (0, 100, 200):
        response = gesture_pipeline.process_observation(
            GestureObservation(value, 0.9, timestamp_ms + offset)
        ) or response
    for offset in (1000, 1800):
        gesture_pipeline.process_observation(
            GestureObservation(None, 0.0, timestamp_ms + offset)
        )
    return response


if __name__ == "__main__":
    main()
