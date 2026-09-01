from __future__ import annotations

import argparse
import json
from pathlib import Path

from edge_smart_home.benchmark.hagrid import (
    build_hagrid_manifest,
    load_label_mapping,
    sha256_file,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a deterministic HaGRID manifest")
    parser.add_argument("--annotations-dir", type=Path, required=True)
    parser.add_argument("--dataset-dir", type=Path, required=True)
    parser.add_argument("--split", default="test")
    parser.add_argument(
        "--mapping", type=Path, default=Path("configs/hagrid_mapping.json")
    )
    parser.add_argument("--limit-per-class", type=int)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows = build_hagrid_manifest(
        args.annotations_dir,
        args.dataset_dir,
        load_label_mapping(args.mapping),
        args.split,
        args.limit_per_class,
        args.seed,
    )
    if not rows:
        raise ValueError("no HaGRID annotation samples found")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"HaGRID manifest: {args.output}")
    print(f"Samples: {len(rows)}")
    print(f"SHA256: {sha256_file(args.output)}")


if __name__ == "__main__":
    main()
