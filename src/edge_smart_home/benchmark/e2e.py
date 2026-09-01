from __future__ import annotations

from collections import Counter
from statistics import mean
from typing import Any, Iterable, Mapping


SUCCESS_STATUSES = frozenset({"executed", "partial_failure"})


def summarize_e2e(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    materialized = list(rows)
    latencies = [
        float(row["end_to_end_ms"])
        for row in materialized
        if isinstance(row.get("end_to_end_ms"), (int, float))
    ]
    processing = [
        float(row["processing_ms"])
        for row in materialized
        if isinstance(row.get("processing_ms"), (int, float))
    ]
    statuses = Counter(str(row["status"]) for row in materialized)
    execution_statuses = {
        "executed",
        "partial_failure",
        "device_error",
        "confirmation_required",
        "duplicate_suppressed",
    }
    execution_rows = [row for row in materialized if row["status"] in execution_statuses]
    succeeded = sum(row["status"] == "executed" for row in execution_rows)
    device_attempts = sum(int(row.get("device_attempts", 0)) for row in materialized)
    device_successes = sum(int(row.get("device_successes", 0)) for row in materialized)
    return {
        "inputs": len(materialized),
        "execution_evaluated": len(execution_rows),
        "executed": succeeded,
        "command_success_rate": round(succeeded / len(execution_rows), 4)
        if execution_rows
        else None,
        "status_counts": dict(sorted(statuses.items())),
        "device_attempts": device_attempts,
        "device_successes": device_successes,
        "device_command_success_rate": round(device_successes / device_attempts, 4)
        if device_attempts
        else None,
        "mean_processing_ms": _rounded_mean(processing),
        "mean_end_to_end_ms": _rounded_mean(latencies),
        "p50_end_to_end_ms": _percentile(latencies, 0.50),
        "p95_end_to_end_ms": _percentile(latencies, 0.95),
        "max_end_to_end_ms": round(max(latencies), 3) if latencies else None,
    }


def _rounded_mean(values: list[float]) -> float | None:
    return round(mean(values), 3) if values else None


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * fraction
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    interpolated = ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)
    return round(interpolated, 3)
