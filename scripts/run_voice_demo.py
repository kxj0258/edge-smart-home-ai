from __future__ import annotations

import argparse
from dataclasses import replace
import json
from pathlib import Path
import shlex
import sys

from edge_smart_home.app import AssistantOrchestrator
from edge_smart_home.app.cli import serialize_outcome
from edge_smart_home.app.pipeline import _load_json
from edge_smart_home.common import DeviceState
from edge_smart_home.audio.microphone import SoundDeviceMicrophone
from edge_smart_home.audio.diagnostics import AudioDiagnosticRecorder
from edge_smart_home.audio.feedback import create_feedback
from edge_smart_home.audio.pipeline import VoiceControlPipeline, VoiceControlResult
from edge_smart_home.audio.sherpa_onnx_adapters import SherpaOnnxStreamingAsr, VoiceDependencyError
from edge_smart_home.audio.validation import AudioInputError


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
                "input_source": "real_mic",
                "backend": "configured",
                "hotwords": str((args.hotwords or root / "configs/hotwords.txt").resolve()),
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
    try:
        recognizer = SherpaOnnxStreamingAsr(
            args.tokens,
            args.encoder,
            args.decoder,
            args.joiner,
            sample_rate,
            audio["asr"],
            args.hotwords or root / "configs" / "hotwords.txt",
        )
    except Exception as error:
        result = _initialization_error_result(error)
        if diagnostics:
            try:
                diagnostics.record_result("", result.status, {**_result_payload(result), "microphone_started": False})
            except Exception as diagnostic_error:
                result = replace(result, diagnostic_error=f"{type(diagnostic_error).__name__}: {diagnostic_error}")
            try:
                diagnostics.close()
            except Exception as close_error:
                detail = f"{type(close_error).__name__}: {close_error}"
                result = replace(result, diagnostic_error="; ".join(v for v in (result.diagnostic_error, detail) if v))
        parser.exit(2, json.dumps({**_result_payload(result), "microphone_started": False}, ensure_ascii=False) + "\n")
    source = SoundDeviceMicrophone(
        int(audio["chunk_duration_ms"]),
        args.device,
        args.mic_sample_rate or audio.get("microphone_sample_rate"),
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
                **source.metrics(),
            })
        if feedback:
            feedback.close()
        if diagnostics:
            diagnostics.close()


def _preload_model(orchestrator: AssistantOrchestrator) -> None:
    try:
        latency_ms = orchestrator.preload_model()
    except Exception as error:
        print(f"小兴模型预热失败，将在需要时安全降级：{type(error).__name__}: {error}")
    else:
        if latency_ms is not None:
            print(f"小兴模型已预热：{latency_ms:.1f} ms")


def _initialization_error_result(error: Exception) -> VoiceControlResult:
    if isinstance(error, VoiceDependencyError):
        stage, code = "asr_dependency", "ASR_DEPENDENCY_UNAVAILABLE"
    elif isinstance(error, AudioInputError):
        stage, code = "audio_input", error.code
    else:
        # The exception's type/detail is evidence; do not guess a missing
        # dependency, decoding fault or device effect from its human message.
        stage, code = "asr_initialization", "ASR_INITIALIZATION_FAILED"
    return VoiceControlResult(
        text="", status="asr_error", error_code=code, error_stage=stage,
        error=f"{type(error).__name__}: {error}",
        message="语音初始化失败，可使用文本输入。",
        text_fallback=("在项目根目录运行：PYTHONPATH=src " + shlex.quote(sys.executable) +
                       " scripts/run_assistant_demo.py --backend mock text --no-model"),
    )


def _result_payload(result) -> dict:
    payload = {
        "status": result.status,
        "error_code": result.error_code,
        "error": result.error,
        "text": result.text,
        "message": result.message,
        "audio_seconds": round(result.audio_seconds, 3),
        "decode_seconds": round(result.decode_seconds, 3),
        "rtf": round(result.rtf, 4) if result.rtf is not None else None,
        "path": result.path,
        "provider": result.provider,
        "llm_calls": result.llm_calls,
        "llm_latency_ms": result.llm_latency_ms,
        "text_fallback": result.text_fallback,
        "feedback_error": result.feedback_error,
        "diagnostic_error": result.diagnostic_error,
        "error_stage": result.error_stage,
        "request_accepted": result.request_accepted,
        "observed_state": serialize_outcome(result.observed_state) if result.observed_state else None,
    }
    if isinstance(result.state, DeviceState):
        payload.update({
            "device_id": result.state.device_id,
            "attributes": dict(result.state.attributes),
            "available": result.state.available,
            "updated_at": result.state.updated_at.isoformat(),
            "backend_kind": result.state.backend_kind,
            "completion_scope": result.state.completion_scope,
        })
    elif result.state is not None:
        payload.update({
            "scene_id": result.state.scene_id,
            "results": serialize_outcome(result.state)["results"],
        })
    else:
        payload.update({"status": result.status, "error": result.error})
    return payload


if __name__ == "__main__":
    main()
