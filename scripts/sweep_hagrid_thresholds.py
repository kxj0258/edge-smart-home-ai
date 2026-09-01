from __future__ import annotations

import argparse
import json
from pathlib import Path

from edge_smart_home.benchmark.hagrid import (
    load_manifest,
    sha256_file,
    summarize_hagrid_threshold,
)


DEFAULT_THRESHOLDS = (0.40, 0.45, 0.50, 0.55, 0.60, 0.65)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Replay HaGRID score thresholds from saved results"
    )
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--thresholds", type=float, nargs="+", default=DEFAULT_THRESHOLDS)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    rows = load_manifest(args.results)
    thresholds = sorted(set(args.thresholds))
    report = {
        "source_results": str(args.results.resolve()),
        "source_results_sha256": sha256_file(args.results),
        "sample_count": len(rows),
        "thresholds": [
            summarize_hagrid_threshold(rows, threshold) for threshold in thresholds
        ],
    }
    output = args.output or args.results.with_name("threshold_sweep.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    print(f"HaGRID threshold sweep: {output}")
    print("threshold  accuracy  macro_f1  gesture_recall  neutral_false_trigger_rate")
    for summary in report["thresholds"]:
        print(
            f"{summary['threshold']:<9.2f}  "
            f"{_format(summary['accuracy']):<8}  "
            f"{_format(summary['macro_f1']):<8}  "
            f"{_format(summary['gesture_recall']):<14}  "
            f"{_format(summary['neutral_false_trigger_rate'])}"
        )


def _format(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.4f}"


if __name__ == "__main__":
    main()
