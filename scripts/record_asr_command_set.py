from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from time import perf_counter
import wave

import numpy as np

from edge_smart_home.app.pipeline import _load_json
from edge_smart_home.audio.microphone import SoundDeviceMicrophone
from edge_smart_home.audio.sherpa_onnx_adapters import SherpaOnnxStreamingAsr
from edge_smart_home.benchmark.asr_command_set import load_commands, load_failed_commands


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Interactively record a labeled ASR command set"
    )
    parser.add_argument("--tokens", type=Path, required=True)
    parser.add_argument("--encoder", type=Path, required=True)
    parser.add_argument("--decoder", type=Path, required=True)
    parser.add_argument("--joiner", type=Path, required=True)
    parser.add_argument("--hotwords", type=Path, default=Path("configs/hotwords.txt"))
    parser.add_argument("--commands", type=Path, default=Path("configs/asr_test_commands.txt"))
    parser.add_argument(
        "--retry-manifest",
        type=Path,
        help="record only unique commands whose exact_match is false in this manifest",
    )
    parser.add_argument("--output-dir", type=Path, default=Path("evaluation/asr_command_sets"))
    parser.add_argument("--device", type=int)
    parser.add_argument("--mic-sample-rate", type=int)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--max-seconds", type=float, default=12.0)
    args = parser.parse_args()
    if args.repetitions <= 0 or args.max_seconds <= 0:
        parser.error("repetitions and max-seconds must be positive")

    root = Path(__file__).resolve().parents[1]
    audio = _load_json(root / "configs" / "audio.json")
    commands = (
        load_failed_commands(args.retry_manifest)
        if args.retry_manifest
        else load_commands(args.commands)
    )
    if not commands:
        parser.error("no commands selected for recording")
    session_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    session_dir = args.output_dir / session_id
    wav_dir = session_dir / "wav"
    wav_dir.mkdir(parents=True, exist_ok=False)

    recognizer = SherpaOnnxStreamingAsr(
        args.tokens,
        args.encoder,
        args.decoder,
        args.joiner,
        int(audio["sample_rate"]),
        audio["asr"],
        args.hotwords,
    )
    metadata = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "commands_file": str(args.commands.resolve()) if not args.retry_manifest else None,
        "retry_manifest": (
            str(args.retry_manifest.resolve()) if args.retry_manifest else None
        ),
        "repetitions": args.repetitions,
        "max_seconds": args.max_seconds,
        "device": args.device,
        "microphone_sample_rate": args.mic_sample_rate,
        "model": {
            "tokens": str(args.tokens.resolve()),
            "encoder": str(args.encoder.resolve()),
            "decoder": str(args.decoder.resolve()),
            "joiner": str(args.joiner.resolve()),
        },
        "audio_config": audio,
        "hotwords": {
            "source": str(args.hotwords.resolve()),
            "valid": list(recognizer.hotword_validation.valid),
            "invalid": list(recognizer.hotword_validation.invalid),
        },
    }
    (session_dir / "session.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    rows: list[dict] = []
    manifest_path = session_dir / "manifest.jsonl"
    print(f"Session: {session_dir}")
    print(f"Commands: {len(commands)}, repetitions: {args.repetitions}")
    print("每条命令按回车准备，看到“录音中”后再说话；输入 q 可提前结束。")
    try:
        with manifest_path.open("w", encoding="utf-8") as manifest:
            for command_index, expected in enumerate(commands, start=1):
                for repetition in range(1, args.repetitions + 1):
                    answer = input(
                        f"\n[{command_index}/{len(commands)}][{repetition}/{args.repetitions}] "
                        f"请朗读：{expected}\n按回车准备 > "
                    ).strip().lower()
                    if answer == "q":
                        raise KeyboardInterrupt
                    row = record_one(
                        expected, command_index, repetition, wav_dir, recognizer, audio, args
                    )
                    rows.append(row)
                    manifest.write(json.dumps(row, ensure_ascii=False) + "\n")
                    manifest.flush()
                    print(
                        f"识别：{row['recognized'] or '<empty>'} | "
                        f"exact={row['exact_match']} | latency={row['endpoint_latency_ms']} ms | "
                        f"RTF={row['rtf']}"
                    )
    except KeyboardInterrupt:
        print("\n录制已结束，已完成结果仍然保留。")

    summary = build_summary(rows)
    (session_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def record_one(expected, command_index, repetition, wav_dir, recognizer, audio, args):
    source = SoundDeviceMicrophone(
        int(audio["chunk_duration_ms"]), args.device, args.mic_sample_rate
    )
    wav_path = wav_dir / f"{command_index:03d}_{repetition}.wav"
    recognized = ""
    recognition = None
    audio_seconds = 0.0
    started = perf_counter()
    stream = source.chunks()
    wav_file = None
    announced = False
    try:
        for chunk in stream:
            if wav_file is None:
                wav_file = wave.open(str(wav_path), "wb")
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(chunk.sample_rate)
            if not announced:
                print("录音中，请说话……")
                announced = True
            samples = np.asarray(chunk.samples, dtype=np.float32)
            wav_file.writeframes((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())
            audio_seconds += len(samples) / chunk.sample_rate
            results = recognizer.accept(chunk)
            if results:
                recognition = results[0]
                recognized = recognition.text.strip()
                break
            if audio_seconds >= args.max_seconds:
                recognizer.reset_current()
                break
    finally:
        stream.close()
        if wav_file is not None:
            wav_file.close()
    latency_ms = round((perf_counter() - started) * 1000)
    return {
        "expected": expected,
        "recognized": recognized,
        "exact_match": normalize_text(expected) == normalize_text(recognized),
        "command_index": command_index,
        "repetition": repetition,
        "wav": str(wav_path),
        "captured_audio_seconds": round(audio_seconds, 3),
        "asr_audio_seconds": round(recognition.audio_seconds, 3) if recognition else None,
        "decode_seconds": round(recognition.decode_seconds, 4) if recognition else None,
        "rtf": round(recognition.rtf, 4) if recognition and recognition.rtf is not None else None,
        "endpoint_latency_ms": latency_ms,
        "portaudio_overflow_count": source.portaudio_overflow_count,
        "queue_drop_count": source.queue_drop_count,
    }


def normalize_text(text: str) -> str:
    return "".join(character for character in text.strip() if not character.isspace())


def build_summary(rows: list[dict]) -> dict:
    matched = sum(bool(row["exact_match"]) for row in rows)
    rtfs = [row["rtf"] for row in rows if row["rtf"] is not None]
    latencies = [row["endpoint_latency_ms"] for row in rows]
    return {
        "recordings": len(rows),
        "exact_matches": matched,
        "exact_match_accuracy": round(matched / len(rows), 4) if rows else None,
        "mean_rtf": round(sum(rtfs) / len(rtfs), 4) if rtfs else None,
        "mean_endpoint_latency_ms": round(sum(latencies) / len(latencies)) if latencies else None,
        "portaudio_overflow_count": sum(row["portaudio_overflow_count"] for row in rows),
        "queue_drop_count": sum(row["queue_drop_count"] for row in rows),
    }


if __name__ == "__main__":
    main()
