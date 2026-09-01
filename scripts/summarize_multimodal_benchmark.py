from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path

from edge_smart_home.benchmark.e2e import summarize_e2e


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Summarize comparable fields from Text, Voice, and Gesture runs"
    )
    parser.add_argument(
        "--run",
        type=Path,
        action="append",
        required=True,
        help="Run directory containing events/results/frames JSONL; repeatable",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("evaluation/multimodal_reports")
    )
    args = parser.parse_args()

    grouped = defaultdict(list)
    artifacts = []
    for run_dir in args.run:
        event_path = _event_path(run_dir)
        rows = _load_jsonl(event_path)
        sources = sorted({str(row.get("source", "unknown")) for row in rows})
        for row in rows:
            source = str(row.get("source", "unknown"))
            if source not in {"text", "voice", "gesture"}:
                raise ValueError(f"unsupported or missing source in {event_path}: {source}")
            grouped[source].append(row)
        artifacts.append(
            {
                "run_dir": str(run_dir.resolve()),
                "events": str(event_path.resolve()),
                "sources": sources,
                "event_count": len(rows),
                "native_summary": _optional_json(run_dir / "summary.json"),
            }
        )

    report = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "comparability_note": (
            "Control and latency fields are summarized per source. ASR accuracy, "
            "gesture FPS, and other native metrics remain attached to their source "
            "artifact and are not averaged across modalities."
        ),
        "by_source": {
            source: summarize_e2e(rows) for source, rows in sorted(grouped.items())
        },
        "artifacts": artifacts,
    }
    report_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    report_dir = args.output_dir / report_id
    report_dir.mkdir(parents=True, exist_ok=False)
    output = report_dir / "summary.json"
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Multimodal report: {output}")
    print(json.dumps(report["by_source"], ensure_ascii=False, indent=2))


def _event_path(run_dir: Path) -> Path:
    for name in ("events.jsonl", "results.jsonl", "frames.jsonl"):
        candidate = run_dir / name
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"no benchmark JSONL artifact in {run_dir}")


def _load_jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"JSON object required at {path}:{line_number}")
            rows.extend(_normalize_rows(value))
    return rows


def _normalize_rows(row: dict) -> list[dict]:
    if row.get("source") in {"text", "voice", "gesture"}:
        return [row]
    if "expected" in row and "recognized" in row:
        return [{
            "source": "voice",
            "input": row.get("recognized") or "<empty>",
            "status": row.get("router_outcome", "not_evaluated"),
            "perception_ms": (
                round(float(row["decode_seconds"]) * 1000, 3)
                if isinstance(row.get("decode_seconds"), (int, float))
                else None
            ),
            "processing_ms": None,
            "end_to_end_ms": None,
            "device_attempts": 0,
            "device_successes": 0,
            "metrics": {
                "expected": row.get("expected"),
                "exact_match": row.get("exact_match"),
                "cer": row.get("cer"),
                "router_match": row.get("router_match"),
                "rtf": row.get("rtf"),
            },
            "error": row.get("router_error"),
        }]
    if isinstance(row.get("responses"), list):
        normalized = []
        inference_ms = row.get("inference_ms")
        for response in row["responses"]:
            result = response.get("result")
            attempts = successes = 0
            if isinstance(result, dict) and result.get("type") == "device_state":
                attempts = successes = 1
            elif isinstance(result, dict) and isinstance(result.get("results"), list):
                attempts = len(result["results"])
                successes = sum(bool(item.get("succeeded")) for item in result["results"])
            processing_ms = response.get("processing_ms")
            normalized.append(
                {
                    "source": "gesture",
                    "input": response.get("label", "gesture"),
                    "status": response.get("status", "unknown"),
                    "perception_ms": inference_ms,
                    "processing_ms": processing_ms,
                    "end_to_end_ms": (
                        round(float(inference_ms) + float(processing_ms), 3)
                        if isinstance(inference_ms, (int, float))
                        and isinstance(processing_ms, (int, float))
                        else None
                    ),
                    "device_attempts": attempts,
                    "device_successes": successes,
                    "metrics": {"timestamp_ms": row.get("timestamp_ms")},
                    "error": response.get("error"),
                }
            )
        return normalized
    return [row]


def _optional_json(path: Path):
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
