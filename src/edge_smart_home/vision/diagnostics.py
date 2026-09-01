from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
from statistics import mean
from typing import Any, Mapping

from edge_smart_home.benchmark.events import (
    BenchmarkEventRecord,
    device_counts_from_serialized_response,
)


class GestureDiagnosticRecorder:
    """Persist metrics measured by a real camera/recognizer execution."""

    def __init__(self, output_root: Path, metadata: Mapping[str, Any]) -> None:
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        self.session_dir = output_root / run_id
        self.session_dir.mkdir(parents=True, exist_ok=False)
        self._results = (self.session_dir / "frames.jsonl").open(
            "w", encoding="utf-8"
        )
        self._events = (self.session_dir / "events.jsonl").open(
            "w", encoding="utf-8"
        )
        self._latencies: list[float] = []
        self._statuses: Counter[str] = Counter()
        self._frames = 0
        self._observations = 0
        _write_json(
            self.session_dir / "metadata.json",
            {
                "run_id": run_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
                **metadata,
            },
        )

    def record_frame(
        self,
        timestamp_ms: int,
        inference_ms: float,
        observations: list[Mapping[str, Any]],
        responses: list[Mapping[str, Any]],
        video_frame_index: int | None = None,
    ) -> None:
        self._frames += 1
        self._observations += len(observations)
        self._latencies.append(inference_ms)
        self._statuses.update(str(item["status"]) for item in responses)
        for response in responses:
            attempts, successes = device_counts_from_serialized_response(response)
            event = BenchmarkEventRecord(
                source="gesture",
                input=str(response.get("label", "gesture")),
                status=str(response["status"]),
                perception_ms=inference_ms,
                processing_ms=float(response.get("processing_ms", 0.0)),
                end_to_end_ms=round(
                    inference_ms + float(response.get("processing_ms", 0.0)), 3
                ),
                device_attempts=attempts,
                device_successes=successes,
                metrics={"timestamp_ms": timestamp_ms},
                error=response.get("error"),
            )
            self._events.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")
        self._results.write(
            json.dumps(
                {
                    "timestamp_ms": timestamp_ms,
                    "inference_ms": inference_ms,
                    "observations": observations,
                    "responses": responses,
                    "video_frame_index": video_frame_index,
                },
                ensure_ascii=False,
            )
            + "\n"
        )

    def close(
        self,
        elapsed_seconds: float,
        filter_counts: Mapping[str, int],
        camera_read_failures: int,
    ) -> None:
        if self._results.closed:
            return
        self._results.close()
        self._events.close()
        ordered = sorted(self._latencies)
        summary = {
            "frames": self._frames,
            "observations": self._observations,
            "elapsed_seconds": round(elapsed_seconds, 3),
            "effective_fps": round(self._frames / elapsed_seconds, 3)
            if elapsed_seconds > 0
            else None,
            "mean_inference_ms": round(mean(ordered), 3) if ordered else None,
            "p95_inference_ms": _percentile(ordered, 0.95),
            "max_inference_ms": round(max(ordered), 3) if ordered else None,
            "response_statuses": dict(sorted(self._statuses.items())),
            "filter_decisions": dict(filter_counts),
            "camera_read_failures": camera_read_failures,
            "false_trigger_rate": None,
            "false_trigger_note": "Requires a labeled gesture timeline; not inferred.",
        }
        _write_json(self.session_dir / "summary.json", summary)


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    index = (len(values) - 1) * fraction
    lower = int(index)
    upper = min(lower + 1, len(values) - 1)
    value = values[lower] + (values[upper] - values[lower]) * (index - lower)
    return round(value, 3)


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as file:
        json.dump(value, file, ensure_ascii=False, indent=2)
        file.write("\n")
