from __future__ import annotations

import argparse
import json
from pathlib import Path

from edge_smart_home.benchmark.hagrid import (
    HAGRID_DERIVED_CLASS_NAMES,
    load_label_mapping,
    select_ranked_samples,
    sha256_file,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Prepare a deterministic smoke subset from the HaGRID-derived Parquet dataset"
    )
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--images-dir", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument(
        "--mapping", type=Path, default=Path("configs/hagrid_mapping.json")
    )
    parser.add_argument("--limit-per-class", type=int, default=20)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    try:
        import pyarrow.parquet as parquet
    except ImportError as error:
        raise RuntimeError("pyarrow is required to prepare the Parquet smoke subset") from error

    parquet_paths = sorted((args.source_dir / "data").glob("*.parquet"))
    if not parquet_paths:
        raise FileNotFoundError(f"no Parquet shards found under {args.source_dir / 'data'}")
    mapping = load_label_mapping(args.mapping)
    target_labels = set(mapping)

    def samples():
        for parquet_path in parquet_paths:
            parquet_file = parquet.ParquetFile(parquet_path)
            for batch in parquet_file.iter_batches(columns=["image", "label"], batch_size=256):
                for row in batch.to_pylist():
                    label_index = int(row["label"])
                    if not 0 <= label_index < len(HAGRID_DERIVED_CLASS_NAMES):
                        raise ValueError(f"unexpected HaGRID-derived label index: {label_index}")
                    image = row["image"]
                    yield {
                        "dataset_label": HAGRID_DERIVED_CLASS_NAMES[label_index],
                        "source_path": str(image.get("path") or ""),
                        "image_bytes": image.get("bytes"),
                    }

    selected = select_ranked_samples(
        samples(), target_labels, args.limit_per_class, args.seed
    )
    missing = sorted(label for label, rows in selected.items() if not rows)
    if missing:
        raise ValueError(f"no samples found for mapped labels: {missing}")

    revision = _source_revision(args.source_dir)
    manifest_rows = []
    for dataset_label, rows in selected.items():
        label_dir = args.images_dir / dataset_label
        label_dir.mkdir(parents=True, exist_ok=True)
        for index, row in enumerate(rows, start=1):
            image_bytes = row["image_bytes"]
            if not isinstance(image_bytes, bytes):
                raise ValueError(f"missing image bytes: {row['source_path']}")
            suffix = Path(row["source_path"]).suffix.lower()
            if suffix not in {".jpg", ".jpeg", ".png"}:
                suffix = ".jpg"
            image_id = Path(row["source_path"]).stem
            image_path = label_dir / f"{index:03d}_{image_id}{suffix}"
            image_path.write_bytes(image_bytes)
            manifest_rows.append(
                {
                    "image_id": image_id,
                    "image_path": str(image_path.resolve()),
                    "dataset_label": dataset_label,
                    "expected_label": mapping[dataset_label],
                    "evaluation_included": True,
                    "user_id": "unavailable",
                    "split": "derived_smoke",
                    "source_dataset": "cj-mills/hagrid-classification-512p-no-gesture-150k",
                    "source_revision": revision,
                    "seed": args.seed,
                }
            )
    manifest_rows.sort(key=lambda row: (row["dataset_label"], row["image_id"]))
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    with args.manifest.open("w", encoding="utf-8") as file:
        for row in manifest_rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"HaGRID-derived smoke images: {args.images_dir}")
    print(f"Manifest: {args.manifest}")
    print(f"Samples: {len(manifest_rows)}")
    print(f"Source revision: {revision}")
    print(f"Manifest SHA256: {sha256_file(args.manifest)}")


def _source_revision(source_dir: Path) -> str:
    metadata = source_dir / ".cache" / "huggingface" / "download" / "README.md.metadata"
    if not metadata.is_file():
        return "unknown"
    first_line = metadata.read_text(encoding="utf-8").splitlines()[0].strip()
    return first_line or "unknown"


if __name__ == "__main__":
    main()
