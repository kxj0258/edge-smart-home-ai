from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from time import perf_counter

from edge_smart_home.app.pipeline import _load_json
from edge_smart_home.benchmark.hagrid import load_manifest, sha256_file, summarize_hagrid
from edge_smart_home.vision import MediaPipeImageGestureRecognizer


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate MediaPipe on a HaGRID manifest")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--config-dir", type=Path, default=Path("configs"))
    parser.add_argument(
        "--output-dir", type=Path, default=Path("evaluation/hagrid_runs")
    )
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    if args.limit is not None and args.limit <= 0:
        raise ValueError("limit must be positive")
    try:
        import cv2
    except ImportError as error:
        raise RuntimeError("opencv-python is required for HaGRID evaluation") from error
    rows = load_manifest(args.manifest)
    if args.limit:
        rows = rows[: args.limit]
    config = _load_json(args.config_dir / "vision.json")["gesture"]
    results = []
    with MediaPipeImageGestureRecognizer(args.model, config) as recognizer:
        for index, row in enumerate(rows, start=1):
            result = dict(row)
            image = cv2.imread(str(row["image_path"]))
            if image is None:
                result["error"] = "image_read_failed"
                results.append(result)
                continue
            rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
            started = perf_counter()
            observations = recognizer.recognize_rgb(rgb, index)
            inference_ms = round((perf_counter() - started) * 1000, 3)
            best = max(observations, key=lambda item: item.score)
            predicted_label = (
                best.label
                if best.score >= float(config["score_threshold"])
                else None
            )
            result.update(
                {
                    "predicted_label": predicted_label,
                    "raw_predicted_label": best.label,
                    "score": round(best.score, 6),
                    "hand_detected": best.handedness is not None,
                    "handedness": best.handedness,
                    "inference_ms": inference_ms,
                    "error": None,
                }
            )
            results.append(result)
            print(
                f"[{index}/{len(rows)}] {row['dataset_label']} -> "
                f"{predicted_label or '<none>'} ({inference_ms:.3f} ms)"
            )
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    run_dir = args.output_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    with (run_dir / "results.jsonl").open("w", encoding="utf-8") as file:
        for row in results:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")
    session = {
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "manifest": str(args.manifest.resolve()),
        "manifest_sha256": sha256_file(args.manifest),
        "model": str(args.model.resolve()),
        "model_sha256": sha256_file(args.model),
        "vision_config": config,
        "limit": args.limit,
    }
    summary = summarize_hagrid(results)
    _write_json(run_dir / "session.json", session)
    _write_json(run_dir / "summary.json", summary)
    print(f"HaGRID evaluation: {run_dir}")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def _write_json(path: Path, value: dict) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
