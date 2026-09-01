from __future__ import annotations

from collections import defaultdict
import hashlib
import heapq
import json
from pathlib import Path
from statistics import mean
from typing import Any, Iterable, Mapping, Sequence


PRODUCT_LABELS = {
    "Open_Palm", "Closed_Fist", "Thumb_Up", "Thumb_Down", "Pointing_Up", "Victory"
}

HAGRID_DERIVED_CLASS_NAMES = (
    "call", "dislike", "fist", "four", "like", "mute", "no_gesture", "ok",
    "one", "palm", "peace", "peace_inverted", "rock", "stop", "stop_inverted",
    "three", "three2", "two_up", "two_up_inverted",
)


def load_label_mapping(path: Path) -> dict[str, str | None]:
    value = json.loads(path.read_text(encoding="utf-8"))
    labels = value.get("labels") if isinstance(value, Mapping) else None
    if not isinstance(labels, Mapping):
        raise ValueError("HaGRID mapping must contain a labels object")
    included: dict[str, str | None] = {}
    for dataset_label, rule in labels.items():
        if not isinstance(rule, Mapping) or not isinstance(rule.get("include"), bool):
            raise ValueError(f"invalid HaGRID mapping rule: {dataset_label}")
        if not rule["include"]:
            continue
        product_label = rule.get("product_label")
        if product_label is not None and product_label not in PRODUCT_LABELS:
            raise ValueError(f"unsupported product gesture label: {product_label}")
        included[str(dataset_label)] = product_label
    if not included:
        raise ValueError("HaGRID mapping must include at least one label")
    return included


def build_hagrid_manifest(
    annotations_dir: Path,
    dataset_dir: Path,
    mapping: Mapping[str, str | None],
    split: str,
    limit_per_class: int | None = None,
    seed: int = 42,
) -> list[dict[str, Any]]:
    if limit_per_class is not None and limit_per_class <= 0:
        raise ValueError("limit_per_class must be positive")
    rows: list[dict[str, Any]] = []
    for annotation_path in sorted(annotations_dir.glob("*.json")):
        dataset_label = annotation_path.stem
        annotations = json.loads(annotation_path.read_text(encoding="utf-8"))
        if not isinstance(annotations, Mapping):
            raise ValueError(f"HaGRID annotation must be an object: {annotation_path}")
        candidates = []
        for image_id, annotation in annotations.items():
            if not isinstance(annotation, Mapping):
                continue
            labels = annotation.get("labels", [])
            if (
                not isinstance(labels, list)
                or not labels
                or set(labels) != {dataset_label}
            ):
                continue
            candidates.append(
                {
                    "image_id": str(image_id),
                    "image_path": str(
                        _resolve_image(dataset_dir, dataset_label, str(image_id)).resolve()
                    ),
                    "dataset_label": dataset_label,
                    "expected_label": mapping.get(dataset_label),
                    "evaluation_included": dataset_label in mapping,
                    "user_id": str(annotation.get("user_id", "unknown")),
                    "split": split,
                }
            )
        candidates.sort(
            key=lambda row: hashlib.sha256(
                f"{seed}:{row['user_id']}:{row['image_id']}".encode("utf-8")
            ).hexdigest()
        )
        rows.extend(candidates[:limit_per_class] if limit_per_class else candidates)
    return rows


def load_manifest(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict) or not row.get("image_path"):
                raise ValueError(f"invalid manifest row at {path}:{line_number}")
            rows.append(row)
    if not rows:
        raise ValueError(f"manifest is empty: {path}")
    return rows


def summarize_hagrid(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    materialized = list(rows)
    included = [row for row in materialized if row.get("evaluation_included")]
    successful = [row for row in included if row.get("error") is None]
    correct = sum(
        row.get("predicted_label") == row.get("expected_label") for row in successful
    )
    labels = sorted(
        {row.get("expected_label") for row in successful}
        | {row.get("predicted_label") for row in successful},
        key=_label_key,
    )
    confusion = {
        _label_key(expected): {
            _label_key(predicted): sum(
                row.get("expected_label") == expected
                and row.get("predicted_label") == predicted
                for row in successful
            )
            for predicted in labels
        }
        for expected in labels
    }
    per_class = {}
    f1_values = []
    for label in labels:
        true_positive = sum(
            row.get("expected_label") == label and row.get("predicted_label") == label
            for row in successful
        )
        false_positive = sum(
            row.get("expected_label") != label and row.get("predicted_label") == label
            for row in successful
        )
        false_negative = sum(
            row.get("expected_label") == label and row.get("predicted_label") != label
            for row in successful
        )
        precision = _ratio(true_positive, true_positive + false_positive)
        recall = _ratio(true_positive, true_positive + false_negative)
        f1 = (
            round(2 * precision * recall / (precision + recall), 4)
            if precision is not None and recall is not None and precision + recall
            else None
        )
        support = sum(row.get("expected_label") == label for row in successful)
        if f1 is not None and support:
            f1_values.append(f1)
        per_class[_label_key(label)] = {
            "support": support, "precision": precision, "recall": recall, "f1": f1
        }
    latencies = [float(row["inference_ms"]) for row in successful]
    return {
        "manifest_samples": len(materialized),
        "mapped_samples": len(included),
        "mapping_coverage": _ratio(len(included), len(materialized)),
        "evaluated_samples": len(successful),
        "failed_samples": len(included) - len(successful),
        "accuracy": _ratio(correct, len(successful)),
        "macro_f1": round(mean(f1_values), 4) if f1_values else None,
        "hand_detection_coverage": _ratio(
            sum(bool(row.get("hand_detected")) for row in successful), len(successful)
        ),
        "mean_inference_ms": round(mean(latencies), 3) if latencies else None,
        "p95_inference_ms": _percentile(latencies, 0.95),
        "per_class": per_class,
        "confusion_matrix": confusion,
        "dataset_labels": _counts(materialized, "dataset_label"),
        "predicted_labels": _counts(successful, "predicted_label", True),
    }


def summarize_hagrid_threshold(
    rows: Iterable[Mapping[str, Any]], threshold: float
) -> dict[str, Any]:
    """Replay a score threshold from saved raw predictions without inference."""
    if not 0.0 <= threshold <= 1.0:
        raise ValueError("threshold must be between 0 and 1")
    replayed = []
    for source in rows:
        row = dict(source)
        raw_label = row.get("raw_predicted_label")
        score = float(row.get("score", 0.0))
        row["predicted_label"] = (
            raw_label if raw_label is not None and score >= threshold else None
        )
        replayed.append(row)

    summary = summarize_hagrid(replayed)
    successful = [
        row
        for row in replayed
        if row.get("evaluation_included") and row.get("error") is None
    ]
    gesture_rows = [row for row in successful if row.get("expected_label") is not None]
    neutral_rows = [row for row in successful if row.get("expected_label") is None]
    summary.update(
        {
            "threshold": round(threshold, 4),
            "gesture_recall": _ratio(
                sum(
                    row.get("predicted_label") == row.get("expected_label")
                    for row in gesture_rows
                ),
                len(gesture_rows),
            ),
            "neutral_false_trigger_rate": _ratio(
                sum(row.get("predicted_label") is not None for row in neutral_rows),
                len(neutral_rows),
            ),
        }
    )
    return summary


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def select_ranked_samples(
    samples: Iterable[Mapping[str, Any]],
    target_labels: set[str],
    limit_per_class: int,
    seed: int,
) -> dict[str, list[Mapping[str, Any]]]:
    """Keep the deterministic lowest hash ranks without retaining the dataset."""
    if limit_per_class <= 0:
        raise ValueError("limit_per_class must be positive")
    heaps: dict[str, list[tuple[int, str, Mapping[str, Any]]]] = {
        label: [] for label in target_labels
    }
    seen_paths: dict[str, set[str]] = {label: set() for label in target_labels}
    for sample in samples:
        label = str(sample["dataset_label"])
        if label not in heaps:
            continue
        source_path = str(sample["source_path"])
        if source_path in seen_paths[label]:
            continue
        seen_paths[label].add(source_path)
        rank = int.from_bytes(
            hashlib.sha256(f"{seed}:{label}:{source_path}".encode("utf-8")).digest(),
            "big",
        )
        item = (-rank, source_path, sample)
        heap = heaps[label]
        if len(heap) < limit_per_class:
            heapq.heappush(heap, item)
        elif rank < -heap[0][0]:
            heapq.heapreplace(heap, item)
    return {
        label: [item[2] for item in sorted(heap, key=lambda item: (-item[0], item[1]))]
        for label, heap in sorted(heaps.items())
    }


def _resolve_image(dataset_dir: Path, label: str, image_id: str) -> Path:
    for extension in (".jpg", ".jpeg", ".png"):
        candidate = dataset_dir / label / f"{image_id}{extension}"
        if candidate.is_file():
            return candidate
    return dataset_dir / label / f"{image_id}.jpg"


def _counts(
    rows: Sequence[Mapping[str, Any]], key: str, label_keys: bool = False
) -> dict[str, int]:
    values: defaultdict[str, int] = defaultdict(int)
    for row in rows:
        value = row.get(key)
        values[_label_key(value) if label_keys else str(value)] += 1
    return dict(sorted(values.items()))


def _label_key(value: Any) -> str:
    return "<none>" if value is None else str(value)


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def _percentile(values: Sequence[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * fraction
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    return round(
        ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower), 3
    )
