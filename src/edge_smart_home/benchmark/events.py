from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class BenchmarkEventRecord:
    source: str
    input: str
    status: str
    perception_ms: float | None = None
    processing_ms: float | None = None
    end_to_end_ms: float | None = None
    device_attempts: int = 0
    device_successes: int = 0
    metrics: Mapping[str, Any] = field(default_factory=dict)
    error: str | None = None

    def __post_init__(self) -> None:
        if self.source not in {"text", "voice", "gesture"}:
            raise ValueError(f"unsupported benchmark source: {self.source}")
        if not self.input or not self.status:
            raise ValueError("benchmark input and status must not be empty")
        if not 0 <= self.device_successes <= self.device_attempts:
            raise ValueError("device successes must be between zero and attempts")
        for name in ("perception_ms", "processing_ms", "end_to_end_ms"):
            value = getattr(self, name)
            if value is not None and value < 0:
                raise ValueError(f"{name} must not be negative")

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["metrics"] = dict(self.metrics)
        return value


def device_counts_from_serialized_response(response: Mapping[str, Any]) -> tuple[int, int]:
    result = response.get("result")
    if not isinstance(result, Mapping):
        return 0, 0
    if result.get("type") == "device_state":
        return 1, 1
    items = result.get("results", [])
    if not isinstance(items, list):
        return 0, 0
    return len(items), sum(bool(item.get("succeeded")) for item in items)
