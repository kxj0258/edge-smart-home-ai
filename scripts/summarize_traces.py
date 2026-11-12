"""Summarize observed trace durations without inventing missing E2E boundaries."""
import argparse
from collections import defaultdict
import csv
import json
from pathlib import Path


def percentile(values, fraction):
    if not values:
        return None
    values = sorted(values)
    pos = (len(values) - 1) * fraction
    lower = int(pos)
    upper = min(lower + 1, len(values) - 1)
    return values[lower] + (values[upper] - values[lower]) * (pos - lower)


def summarize(records):
    # Multiple snapshots (response, voice result, feedback) are not new requests.
    traces = {}
    for record in records:
        key = (record.get("session_id"), record["trace_id"])
        previous = traces.get(key, {})
        # A late async snapshot must not replace a newer, more complete snapshot.
        previous_end = previous.get("snapshot_ns", 0)
        current_end = record.get("snapshot_ns", 0)
        if current_end >= previous_end:
            traces[key] = {**previous, **record}
    metrics = defaultdict(list)
    for trace in traces.values():
        totals = defaultdict(float)
        for item in trace.get("spans", []):
            totals[item["name"]] += item["duration_ms"]
        if trace.get("stage_totals"):
            totals = {k: v["duration_ms"] for k, v in trace["stage_totals"].items()}
        for name, value in totals.items():
            metrics["stage_total." + name].append(value)
        for name, value in trace.get("latency_ms", {}).items():
            if value is not None:
                metrics[name].append(value)
    for name in ("speech_end_to_device_confirmed", "intent_start_to_gesture_event"):
        metrics[name]
    rows = [{"metric": key, "sample_count": len(values),
             "mean_ms": sum(values) / len(values) if values else None,
             "p50_ms": percentile(values, .5), "p95_ms": percentile(values, .95)}
            for key, values in sorted(metrics.items())]
    return {"schema_version": 2, "trace_count": len(traces),
            "truncated_trace_count": sum(bool(t.get("dropped_spans")) for t in traces.values()),
            "metrics": rows,
            "note": "One sample per trace. Stages can nest: never sum rows into E2E. "
                    "Stage totals survive bounded span detail truncation. Missing measurements stay null."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="traces.jsonl")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    with args.input.open(encoding="utf-8") as file:
        result = summarize(json.loads(line) for line in file if line.strip())
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "trace_summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with (args.output_dir / "trace_summary.csv").open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=["metric", "sample_count", "mean_ms", "p50_ms", "p95_ms"])
        writer.writeheader()
        writer.writerows(result["metrics"])
    print(args.output_dir / "trace_summary.json")


if __name__ == "__main__":
    main()
