from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import wave
from copy import deepcopy

import numpy as np

from edge_smart_home.app.pipeline import _load_json
from edge_smart_home.audio.sherpa_onnx_adapters import SherpaOnnxStreamingAsr
from edge_smart_home.benchmark.asr_evaluation import (
    classify_route_mismatch,
    markdown_report,
    route_signature,
    summarize,
    text_metrics,
)
from edge_smart_home.router import FastPathRouter, SceneRouter


def read_pcm16_mono(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as wav_file:
        if wav_file.getnchannels() != 1 or wav_file.getsampwidth() != 2:
            raise ValueError(f"expected mono 16-bit PCM WAV: {path}")
        sample_rate = wav_file.getframerate()
        samples = np.frombuffer(wav_file.readframes(wav_file.getnframes()), dtype="<i2")
    return samples.astype(np.float32) / 32768.0, sample_rate


def load_manifest(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as manifest:
        for line_number, line in enumerate(manifest, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"invalid JSON at {path}:{line_number}") from error
            if not isinstance(row.get("expected"), str) or not isinstance(row.get("wav"), str):
                raise ValueError(f"missing expected/wav at {path}:{line_number}")
            rows.append(row)
    return rows


def resolve_wav(path_text: str, manifest: Path) -> Path:
    path = Path(path_text)
    if path.is_absolute() or path.is_file():
        return path
    relative_to_manifest = manifest.parent / path
    if relative_to_manifest.is_file():
        return relative_to_manifest
    raise FileNotFoundError(f"WAV does not exist: {path_text}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate a labeled WAV manifest with ASR")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--tokens", type=Path, required=True)
    parser.add_argument("--encoder", type=Path, required=True)
    parser.add_argument("--decoder", type=Path, required=True)
    parser.add_argument("--joiner", type=Path, required=True)
    parser.add_argument("--hotwords", type=Path, default=Path("configs/hotwords.txt"))
    parser.add_argument(
        "--no-hotwords",
        action="store_true",
        help="disable hotwords and use greedy_search unless decoding-method is set",
    )
    parser.add_argument("--hotwords-score", type=float)
    parser.add_argument(
        "--decoding-method", choices=("greedy_search", "modified_beam_search")
    )
    parser.add_argument("--config-dir", type=Path, default=Path("configs"))
    parser.add_argument("--output-dir", type=Path, default=Path("evaluation/asr_runs"))
    parser.add_argument("--limit", type=int, help="evaluate only the first N rows")
    args = parser.parse_args()
    if args.limit is not None and args.limit <= 0:
        parser.error("limit must be positive")

    source_rows = load_manifest(args.manifest)
    if args.limit is not None:
        source_rows = source_rows[: args.limit]
    if not source_rows:
        parser.error("manifest contains no selected rows")

    audio_config = _load_json(args.config_dir / "audio.json")
    audio_config = deepcopy(audio_config)
    if args.hotwords_score is not None:
        audio_config["asr"]["hotwords_score"] = args.hotwords_score
    if args.decoding_method:
        audio_config["asr"]["decoding_method"] = args.decoding_method
    elif args.no_hotwords:
        audio_config["asr"]["decoding_method"] = "greedy_search"
    effective_hotwords = None if args.no_hotwords else args.hotwords
    asr = SherpaOnnxStreamingAsr(
        args.tokens,
        args.encoder,
        args.decoder,
        args.joiner,
        int(audio_config["sample_rate"]),
        audio_config["asr"],
        effective_hotwords,
    )
    devices = _load_json(args.config_dir / "devices.json")
    intents = _load_json(args.config_dir / "intents.json")
    routing = _load_json(args.config_dir / "routing.json")
    scenes = _load_json(args.config_dir / "scenes.json")
    fast_router = FastPathRouter(devices, intents, routing.get("default_devices", {}))
    scene_router = SceneRouter(scenes)

    session_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    session_dir = args.output_dir / session_id
    session_dir.mkdir(parents=True, exist_ok=False)
    metadata = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_manifest": str(args.manifest.resolve()),
        "selected_rows": len(source_rows),
        "limit": args.limit,
        "model": {
            "tokens": str(args.tokens.resolve()),
            "encoder": str(args.encoder.resolve()),
            "decoder": str(args.decoder.resolve()),
            "joiner": str(args.joiner.resolve()),
        },
        "hotwords": {
            "enabled": effective_hotwords is not None,
            "source": str(effective_hotwords.resolve()) if effective_hotwords else None,
            "score": audio_config["asr"]["hotwords_score"],
            "valid": list(asr.hotword_validation.valid),
            "invalid": list(asr.hotword_validation.invalid),
        },
        "decoding_method": audio_config["asr"]["decoding_method"],
        "audio_config": audio_config,
    }
    (session_dir / "session.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    result_rows = []
    results_path = session_dir / "results.jsonl"
    with results_path.open("w", encoding="utf-8") as results_file:
        for index, source in enumerate(source_rows, start=1):
            wav_path = resolve_wav(source["wav"], args.manifest)
            samples, sample_rate = read_pcm16_mono(wav_path)
            recognition = asr.transcribe_waveform(
                samples, sample_rate, int(audio_config["chunk_duration_ms"])
            )
            row = dict(source)
            row.update(
                {
                    "recognized": recognition.text,
                    **text_metrics(source["expected"], recognition.text),
                    "decode_seconds": round(recognition.decode_seconds, 4),
                    "asr_audio_seconds": round(recognition.audio_seconds, 4),
                    "rtf": round(recognition.rtf, 4) if recognition.rtf is not None else None,
                }
            )
            try:
                expected_route = route_signature(source["expected"], fast_router, scene_router)
            except (ValueError, KeyError) as error:
                row.update(
                    {
                        "router_match": False,
                        "expected_route": None,
                        "recognized_route": None,
                        "router_outcome": "expected_unroutable",
                        "semantic_mismatch_type": None,
                        "router_error": str(error),
                    }
                )
            else:
                try:
                    recognized_route = route_signature(
                        recognition.text, fast_router, scene_router
                    )
                except (ValueError, KeyError) as error:
                    row.update(
                        {
                            "router_match": False,
                            "expected_route": expected_route,
                            "recognized_route": None,
                            "router_outcome": "recognized_unroutable",
                            "semantic_mismatch_type": None,
                            "router_error": str(error),
                        }
                    )
                else:
                    router_match = expected_route == recognized_route
                    row.update(
                        {
                            "router_match": router_match,
                            "expected_route": expected_route,
                            "recognized_route": recognized_route,
                            "router_outcome": (
                                "semantic_match" if router_match else "semantic_mismatch"
                            ),
                            "semantic_mismatch_type": (
                                None
                                if router_match
                                else classify_route_mismatch(expected_route, recognized_route)
                            ),
                            "router_error": None,
                        }
                    )
            result_rows.append(row)
            results_file.write(json.dumps(row, ensure_ascii=False) + "\n")
            results_file.flush()
            print(
                f"[{index}/{len(source_rows)}] exact={row['exact_match']} "
                f"router={row['router_match']} {source['expected']} -> "
                f"{recognition.text or '<empty>'}"
            )

    summary = summarize(result_rows)
    (session_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (session_dir / "report.md").write_text(
        markdown_report(summary), encoding="utf-8"
    )
    print(f"Evaluation complete: {session_dir}")
    print(json.dumps(summary["overall"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
