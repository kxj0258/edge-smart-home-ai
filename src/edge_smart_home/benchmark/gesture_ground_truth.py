from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from statistics import mean
from typing import Any, Mapping, Sequence


@dataclass(frozen=True, slots=True)
class GestureSegment:
    start_ms: int
    end_ms: int
    label: str | None
    note: str = ""
    hand: str | None = None

    def __post_init__(self) -> None:
        if self.start_ms < 0 or self.end_ms <= self.start_ms:
            raise ValueError("gesture segment requires 0 <= start_ms < end_ms")
        if self.label == "":
            raise ValueError("gesture segment label must be null or non-empty")
        if self.hand not in {None, "left", "right"}:
            raise ValueError("gesture segment hand must be left, right, or null")


def load_frames(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict) or not isinstance(value.get("timestamp_ms"), int):
                raise ValueError(f"invalid gesture frame at {path}:{line_number}")
            rows.append(value)
    if not rows:
        raise ValueError(f"gesture frame file is empty: {path}")
    return rows


def load_ground_truth(path: Path) -> list[GestureSegment]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping) or not isinstance(value.get("segments"), list):
        raise ValueError("ground truth must contain a segments list")
    if not value["segments"]:
        raise ValueError(
            "ground truth segments are empty; complete the manual annotation before evaluation"
        )
    segments = [
        GestureSegment(
            start_ms=int(item["start_ms"]),
            end_ms=int(item["end_ms"]),
            label=item.get("label"),
            note=str(item.get("note", "")),
            hand=item.get("hand"),
        )
        for item in value["segments"]
    ]
    ordered = sorted(segments, key=lambda item: item.start_ms)
    for previous, current in zip(ordered, ordered[1:]):
        if current.start_ms < previous.end_ms:
            raise ValueError("ground truth segments must not overlap")
    return ordered


def build_template(frames: Sequence[Mapping[str, Any]], session_dir: Path) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "session_dir": str(session_dir.resolve()),
        "timeline_start_ms": int(frames[0]["timestamp_ms"]),
        "timeline_end_ms": int(frames[-1]["timestamp_ms"]),
        "annotation_note": (
            "Add non-overlapping [start_ms, end_ms) segments. Use a MediaPipe gesture "
            "label for one intended gesture instance, or null for an explicit neutral interval."
        ),
        "segments": [],
    }


def evaluate_gesture_session(
    frames: Sequence[Mapping[str, Any]], segments: Sequence[GestureSegment]
) -> dict[str, Any]:
    frame_rows = []
    triggers = []
    for frame in frames:
        timestamp = int(frame["timestamp_ms"])
        predicted = _best_observation_label(frame.get("observations", []))
        frame_rows.append((timestamp, predicted))
        if frame.get("responses"):
            triggers.append((timestamp, predicted))

    classified = correct = 0
    expected = matched = duplicates = 0
    false_triggers = 0
    neutral_false_triggers = 0
    neutral_ms = 0
    latencies: list[int] = []
    by_label: dict[str, dict[str, int]] = {}
    by_hand: dict[str, dict[str, int]] = {}
    covered_trigger_indexes: set[int] = set()

    for segment in segments:
        inside_frames = [row for row in frame_rows if segment.start_ms <= row[0] < segment.end_ms]
        classified += len(inside_frames)
        correct += sum(predicted == segment.label for _, predicted in inside_frames)
        inside_triggers = [
            (index, timestamp, label)
            for index, (timestamp, label) in enumerate(triggers)
            if segment.start_ms <= timestamp < segment.end_ms
        ]
        covered_trigger_indexes.update(index for index, _, _ in inside_triggers)
        if segment.label is None:
            neutral_ms += segment.end_ms - segment.start_ms
            false_triggers += len(inside_triggers)
            neutral_false_triggers += len(inside_triggers)
            continue
        expected += 1
        counts = by_label.setdefault(segment.label, {"expected": 0, "matched": 0, "missed": 0})
        counts["expected"] += 1
        hand_counts = None
        if segment.hand is not None:
            hand_counts = by_hand.setdefault(
                segment.hand, {"expected": 0, "matched": 0, "missed": 0}
            )
            hand_counts["expected"] += 1
        matching = [(timestamp, label) for _, timestamp, label in inside_triggers if label == segment.label]
        wrong = len(inside_triggers) - len(matching)
        false_triggers += wrong
        if matching:
            matched += 1
            counts["matched"] += 1
            if hand_counts is not None:
                hand_counts["matched"] += 1
            latencies.append(matching[0][0] - segment.start_ms)
            duplicates += len(matching) - 1
        else:
            counts["missed"] += 1
            if hand_counts is not None:
                hand_counts["missed"] += 1

    outside = len(triggers) - len(covered_trigger_indexes)
    false_triggers += outside
    total_trigger_errors = false_triggers + duplicates
    precision_denominator = matched + total_trigger_errors
    return {
        "annotated_segments": len(segments),
        "expected_gesture_instances": expected,
        "matched_instances": matched,
        "missed_instances": expected - matched,
        "duplicate_triggers": duplicates,
        "false_triggers": false_triggers,
        "outside_annotation_triggers": outside,
        "trigger_precision": _ratio(matched, precision_denominator),
        "trigger_recall": _ratio(matched, expected),
        "classification_accuracy": _ratio(correct, classified),
        "classified_frames": classified,
        "neutral_annotated_seconds": round(neutral_ms / 1000, 3),
        "false_triggers_per_neutral_minute": (
            round(neutral_false_triggers / (neutral_ms / 60000), 4)
            if neutral_ms
            else None
        ),
        "mean_trigger_latency_ms": round(mean(latencies), 3) if latencies else None,
        "by_label": dict(sorted(by_label.items())),
        "by_hand": dict(sorted(by_hand.items())),
    }


def _best_observation_label(observations: Any) -> str | None:
    if not isinstance(observations, list) or not observations:
        return None
    candidates = [item for item in observations if isinstance(item, Mapping)]
    if not candidates:
        return None
    best = max(candidates, key=lambda item: float(item.get("score", 0.0)))
    label = best.get("label")
    return str(label) if label is not None else None


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None
