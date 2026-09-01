from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from time import perf_counter
import wave

import numpy as np

from edge_smart_home.audio.tts import SherpaOnnxVitsTts
from edge_smart_home.benchmark.asr_command_set import load_commands


def parse_csv_numbers(value: str, number_type, name: str):
    try:
        values = [number_type(item.strip()) for item in value.split(",") if item.strip()]
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"invalid {name}: {value}") from error
    if not values:
        raise argparse.ArgumentTypeError(f"{name} cannot be empty")
    return values


def write_pcm16_wav(path: Path, samples: np.ndarray, sample_rate: int) -> None:
    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm.tobytes())


def build_merged_lexicon(base: Path, overrides: Path, destination: Path) -> None:
    base_text = base.read_text(encoding="utf-8").rstrip()
    override_lines = [
        line.strip()
        for line in overrides.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    destination.write_text(
        base_text + "\n" + "\n".join(override_lines) + "\n", encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a labeled TTS command set")
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--commands", type=Path, default=Path("configs/asr_test_commands.txt"))
    parser.add_argument("--output-dir", type=Path, default=Path("evaluation/tts_command_sets"))
    parser.add_argument(
        "--pronunciation-overrides",
        type=Path,
        default=Path("configs/tts_pronunciation_overrides.txt"),
    )
    parser.add_argument("--speakers", default="0,1,2,3,4")
    parser.add_argument("--speeds", default="1.0")
    parser.add_argument("--num-threads", type=int, default=2)
    parser.add_argument("--provider", default="cpu")
    args = parser.parse_args()
    if args.num_threads <= 0:
        parser.error("num-threads must be positive")
    try:
        speakers = parse_csv_numbers(args.speakers, int, "speakers")
        speeds = parse_csv_numbers(args.speeds, float, "speeds")
    except argparse.ArgumentTypeError as error:
        parser.error(str(error))
    if any(speed <= 0 for speed in speeds):
        parser.error("all speeds must be positive")

    commands = load_commands(args.commands)
    if not commands:
        parser.error("commands file contains no commands")
    session_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    session_dir = args.output_dir / session_id
    wav_dir = session_dir / "wav"
    wav_dir.mkdir(parents=True, exist_ok=False)
    merged_lexicon = session_dir / "lexicon.txt"
    build_merged_lexicon(
        args.model_dir / "lexicon.txt",
        args.pronunciation_overrides,
        merged_lexicon,
    )
    tts = SherpaOnnxVitsTts(
        args.model_dir,
        args.num_threads,
        args.provider,
        lexicon_path=merged_lexicon,
    )
    invalid_speakers = [sid for sid in speakers if not 0 <= sid < tts.num_speakers]
    if invalid_speakers:
        parser.error(
            f"invalid speaker IDs {invalid_speakers}; model supports 0..{tts.num_speakers - 1}"
        )
    metadata = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "kind": "synthetic_tts_command_set",
        "commands_file": str(args.commands.resolve()),
        "model_dir": str(args.model_dir.resolve()),
        "base_lexicon": str((args.model_dir / "lexicon.txt").resolve()),
        "pronunciation_overrides": str(args.pronunciation_overrides.resolve()),
        "merged_lexicon": str(merged_lexicon.resolve()),
        "num_speakers_supported": tts.num_speakers,
        "speakers": speakers,
        "speeds": speeds,
        "sample_rate": tts.sample_rate,
        "num_threads": args.num_threads,
        "provider": args.provider,
    }
    (session_dir / "session.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    manifest_path = session_dir / "manifest.jsonl"
    total = len(commands) * len(speakers) * len(speeds)
    completed = 0
    with manifest_path.open("w", encoding="utf-8") as manifest:
        for command_index, text in enumerate(commands, start=1):
            for speaker_id in speakers:
                for speed in speeds:
                    completed += 1
                    filename = (
                        f"{command_index:03d}_sid{speaker_id}_speed{speed:g}.wav"
                    )
                    wav_path = wav_dir / filename
                    started = perf_counter()
                    audio = tts.synthesize(text, speaker_id, speed)
                    generation_seconds = perf_counter() - started
                    write_pcm16_wav(wav_path, audio.samples, audio.sample_rate)
                    duration = len(audio.samples) / audio.sample_rate
                    row = {
                        "expected": text,
                        "command_index": command_index,
                        "speaker_id": speaker_id,
                        "speed": speed,
                        "wav": str(wav_path),
                        "sample_rate": audio.sample_rate,
                        "duration_seconds": round(duration, 4),
                        "generation_seconds": round(generation_seconds, 4),
                        "generation_rtf": (
                            round(generation_seconds / duration, 4) if duration else None
                        ),
                    }
                    manifest.write(json.dumps(row, ensure_ascii=False) + "\n")
                    manifest.flush()
                    print(f"[{completed}/{total}] {filename}: {text}")

    summary = {"utterances": completed, "commands": len(commands)}
    (session_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Generated dataset: {session_dir}")


if __name__ == "__main__":
    main()
