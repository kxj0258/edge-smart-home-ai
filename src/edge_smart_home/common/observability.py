"""Process-local monotonic traces. No inference, network or device dependencies."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import wraps
from threading import RLock
from time import monotonic_ns
from typing import Any, Callable
from uuid import uuid4


_active: ContextVar[Trace | None] = ContextVar("assistant_trace", default=None)


@dataclass
class Trace:
    trace_id: str = field(default_factory=lambda: str(uuid4()))
    session_id: str | None = None
    event_id: str | None = None
    sink: Callable[[dict], None] | None = field(default=None, repr=False)
    marks: dict[str, int] = field(default_factory=dict)
    spans: list[dict] = field(default_factory=list)
    stage_totals: dict[str, dict] = field(default_factory=dict)
    dropped_spans: int = 0
    sink_errors: int = 0
    _lock: RLock = field(default_factory=RLock, repr=False)

    def mark(self, name: str, at_ns: int | None = None) -> None:
        with self._lock:
            self.marks[name] = monotonic_ns() if at_ns is None else at_ns

    def add(self, name: str, start_ns: int, end_ns: int, **details: Any) -> None:
        if end_ns < start_ns:
            raise ValueError("trace end precedes start")
        item = {"name": name, "start_ns": start_ns, "end_ns": end_ns,
                "duration_ms": (end_ns - start_ns) / 1_000_000, **details}
        with self._lock:
            total = self.stage_totals.setdefault(name, {"count": 0, "duration_ms": 0.0})
            total["count"] += 1
            total["duration_ms"] += item["duration_ms"]
            if len(self.spans) < 256:
                self.spans.append(item)
            else:
                self.spans.pop(0)
                self.spans.append(item)
                self.dropped_spans += 1

    def elapsed(self, start: str, end: str) -> float | None:
        with self._lock:
            a, b = self.marks.get(start), self.marks.get(end)
        return (b - a) / 1_000_000 if a is not None and b is not None and b >= a else None

    def snapshot(self) -> dict:
        with self._lock:
            return {"snapshot_ns": monotonic_ns(), "schema_version": 2, "session_id": self.session_id,
                    "trace_id": self.trace_id, "event_id": self.event_id,
                    "clock": "process_monotonic_ns", "marks": dict(self.marks),
                    "spans": [dict(s) for s in self.spans],
                    "stage_totals": {k: dict(v) for k, v in self.stage_totals.items()},
                    "dropped_spans": self.dropped_spans, "sink_errors": self.sink_errors,
                    "latency_ms": {
                        "capture_to_response": self.elapsed("capture_callback", "response"),
                        "frame_received_to_response": self.elapsed("frame_received", "response"),
                        "recognition_to_response": self.elapsed("recognition_ready", "response"),
                        "speech_end_to_device_confirmed": None,
                        "intent_start_to_gesture_event": None,
                    }}

    def emit(self, kind: str, **details: Any) -> None:
        if self.sink:
            try:
                self.sink({"kind": kind, "timestamp": datetime.now(timezone.utc).isoformat(),
                           **self.snapshot(), **details})
            except Exception:
                # Observability must not change a successfully executed command.
                self.sink_errors += 1


def current_trace() -> Trace | None:
    return _active.get()


@contextmanager
def activate(trace: Trace | None):
    token = _active.set(trace)
    try:
        yield
    finally:
        _active.reset(token)


@contextmanager
def span(name: str, **details: Any):
    trace = current_trace()
    start = monotonic_ns()
    error = None
    try:
        yield
    except BaseException as exc:
        error = type(exc).__name__
        raise
    finally:
        if trace:
            trace.add(name, start, monotonic_ns(), error_type=error, **details)


def observed(name: str):
    def decorate(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            with span(name):
                return fn(*args, **kwargs)
        return wrapped
    return decorate
