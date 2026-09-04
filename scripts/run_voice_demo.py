from __future__ import annotations

import argparse
import json
from pathlib import Path

from edge_smart_home.app import AssistantOrchestrator
from edge_smart_home.app.pipeline import _load_json
from edge_smart_home.common import DeviceState
from edge_smart_home.audio.microphone import SoundDeviceMicrophone
from edge_smart_home.audio.diagnostics import AudioDiagnosticRecorder
from edge_smart_home.audio.feedback import create_feedback
from edge_smart_home.audio.pipeline import VoiceControlPipeline
from edge_smart_home.audio.sherpa_onnx_adapters import SherpaOnnxStreamingAsr


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vad-model", type=Path, help="Deprecated; online endpointing is used")
    parser.add_argument("--tokens", type=Path, required=True)
    parser.add_argument("--encoder", type=Path, required=True)
    parser.add_argument("--decoder", type=Path, required=True)
    parser.add_argument("--joiner", type=Path, required=True)
    parser.add_argument("--device", type=int)
    parser.add_argument(
        "--mic-sample-rate",
        type=int,
        help="Microphone hardware rate; defaults to the device's reported rate",
    )
    parser.add_argument(
        "--hotwords",
        type=Path,
        help="Defaults to configs/hotwords.txt",
    )
    parser.add_argument(
        "--diagnostics-dir",
        type=Path,
        help="Save raw microphone WAV, session metadata, and recognition JSONL",
    )
    parser.add_argument(
        "--feedback",
        choices=("none", "console", "tts"),
        default="console",
        help="Acknowledge and report command results through console or offline TTS",
    )
    parser.add_argument("--tts-model-dir", type=Path)
    parser.add_argument("--tts-speaker-id", type=int, default=0)
    parser.add_argument("--tts-speed", type=float, default=1.0)
    parser.add_argument("--tts-audio-device", type=int)
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    audio = _load_json(root / "configs" / "audio.json")
    sample_rate = int(audio["sample_rate"])
    diagnostics = None
    if args.diagnostics_dir:
        diagnostics = AudioDiagnosticRecorder(
            args.diagnostics_dir,
            {
                "model": {
                    "tokens": str(args.tokens.resolve()),
                    "encoder": str(args.encoder.resolve()),
                    "decoder": str(args.decoder.resolve()),
                    "joiner": str(args.joiner.resolve()),
                },
                "device": args.device,
                "microphone_sample_rate": args.mic_sample_rate,
                "audio_config": audio,
            },
        )
        print(f"Diagnostics: {diagnostics.session_dir}")
    source = SoundDeviceMicrophone(
        int(audio["chunk_duration_ms"]),
        args.device,
        args.mic_sample_rate or audio.get("microphone_sample_rate"),
    )
    recognizer = SherpaOnnxStreamingAsr(
            args.tokens,
            args.encoder,
            args.decoder,
            args.joiner,
            sample_rate,
            audio["asr"],
            args.hotwords or root / "configs" / "hotwords.txt",
    )
    print(
        f"Hotwords: {len(recognizer.hotword_validation.valid)} valid, "
        f"{len(recognizer.hotword_validation.invalid)} skipped"
    )
    orchestrator = AssistantOrchestrator.from_config_dir(root / "configs")
    _preload_model(orchestrator)
    feedback = create_feedback(
        args.feedback,
        tts_model_dir=args.tts_model_dir,
        speaker_id=args.tts_speaker_id,
        speed=args.tts_speed,
        audio_device=args.tts_audio_device,
    )
    pipeline = VoiceControlPipeline(
        source,
        recognizer,
        orchestrator,
        diagnostics,
        feedback,
    )
    print("Voice demo started. Press Ctrl+C to stop.")
    try:
        for result in pipeline.run():
            print(json.dumps(_result_payload(result), ensure_ascii=False))
    except KeyboardInterrupt:
        print("\nVoice demo stopped.")
        for result in pipeline.finish():
            print(json.dumps(_result_payload(result), ensure_ascii=False))
    finally:
        if diagnostics:
            diagnostics.update_summary({
                "portaudio_overflow_count": source.portaudio_overflow_count,
                "queue_drop_count": source.queue_drop_count,
            })
            diagnostics.close()
        if feedback:
            feedback.close()


def _preload_model(orchestrator: AssistantOrchestrator) -> None:
    try:
        latency_ms = orchestrator.preload_model()
    except Exception as error:
        print(f"小兴模型预热失败，将在需要时安全降级：{type(error).__name__}: {error}")
    else:
        if latency_ms is not None:
            print(f"小兴模型已预热：{latency_ms:.1f} ms")


def _result_payload(result) -> dict:
    payload = {
        "text": result.text,
        "message": result.message,
        "audio_seconds": round(result.audio_seconds, 3),
        "decode_seconds": round(result.decode_seconds, 3),
        "rtf": round(result.rtf, 4) if result.rtf is not None else None,
        "path": result.path,
        "provider": result.provider,
        "llm_calls": result.llm_calls,
        "llm_latency_ms": result.llm_latency_ms,
    }
    if isinstance(result.state, DeviceState):
        payload.update({
            "status": "executed",
            "device_id": result.state.device_id,
            "attributes": dict(result.state.attributes),
        })
    elif result.state is not None:
        payload.update({
            "status": "executed" if result.state.succeeded else "partial_failure",
            "scene_id": result.state.scene_id,
            "results": [
                {
                    "device_id": item.command.device_id,
                    "action": item.command.action,
                    "succeeded": item.succeeded,
                    "error": item.error,
                }
                for item in result.state.results
            ],
        })
    else:
        payload.update({"status": result.status, "error": result.error})
    return payload


if __name__ == "__main__":
    main()
