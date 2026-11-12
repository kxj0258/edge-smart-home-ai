"""Compare completed, compatible incremental-load sessions; never infer accuracy."""
import argparse
import csv
import json
from pathlib import Path
from edge_smart_home.benchmark.audio_load import compare


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("runs", nargs="+", type=Path, help="load_summary.json files")
    p.add_argument("--output-dir", required=True, type=Path)
    args = p.parse_args()
    report = compare([json.loads(path.read_text()) for path in args.runs])
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "comparison.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    with (args.output_dir / "comparison.csv").open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(report["rows"][0]))
        writer.writeheader()
        writer.writerows(report["rows"])
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
