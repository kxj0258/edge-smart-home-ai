from __future__ import annotations

import argparse
import json
from pathlib import Path

from edge_smart_home.app.pipeline import TextControlPipeline, _load_json
from edge_smart_home.common import DeviceState
from edge_smart_home.audio.microphone import SoundDeviceMicrophone
from edge_smart_home.audio.diagnostics import AudioDiagnosticRecorder
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
    pipeline = VoiceControlPipeline(
        SoundDeviceMicrophone(
            int(audio["chunk_duration_ms"]),
            args.device,
            args.mic_sample_rate or audio.get("microphone_sample_rate"),
        ),
        SherpaOnnxStreamingAsr(
            args.tokens,
            args.encoder,
            args.decoder,
            args.joiner,
            sample_rate,
            audio["asr"],
            args.hotwords or root / "configs" / "hotwords.txt",
        ),
        TextControlPipeline.from_config_dir(root / "configs"),
        diagnostics,
    )
    print("Voice demo started. Press Ctrl+C to stop.")
    try:
        for result in pipeline.run():
            payload = {"text": result.text}
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
                payload.update({"status": "not_routed", "error": result.error})
            print(json.dumps(payload, ensure_ascii=False))
    except KeyboardInterrupt:
        print("\nVoice demo stopped.")
    finally:
        if diagnostics:
            diagnostics.close()


if __name__ == "__main__":
    main()
