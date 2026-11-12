from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import wave
from typing import Any, Mapping
from threading import RLock
from time import monotonic_ns
from edge_smart_home.benchmark.run_record import build_run_record, metadata_artifacts, resource_sample

from edge_smart_home.benchmark.events import BenchmarkEventRecord

from .interfaces import AudioChunk


class AudioDiagnosticRecorder:
    """Persist one reproducible microphone/ASR session without model inference."""

    def __init__(self, output_root: Path, metadata: Mapping[str, Any]) -> None:
        session_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        self.session_id = session_id
        self._io_lock = RLock()
        self._closed = False
        self._started_ns = monotonic_ns()
        self._resources_start = resource_sample()
        self.session_dir = output_root / session_id
        self.session_dir.mkdir(parents=True, exist_ok=False)
        self._metadata = dict(metadata)
        self._traces = (self.session_dir / "traces.jsonl").open("w", encoding="utf-8")
        self._chunks = (self.session_dir / "audio_chunks.jsonl").open("w", encoding="utf-8")
        self._resources = (self.session_dir / "resources.jsonl").open("w", encoding="utf-8")
        self._last_resource_ns = self._started_ns
        self._last_cpu_seconds = self._resources_start["cpu_seconds"]
        root = Path(__file__).resolve().parents[3]
        record = build_run_record(root, session_id, self._metadata, metadata_artifacts(self._metadata))
        (self.session_dir / "run_record.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self._summary: dict[str, Any] = {}
        self._event_count = 0
        self._status_counts: dict[str, int] = {}
        self._rtfs: list[float] = []
        self._decode_seconds: list[float] = []
        self._wave: wave.Wave_write | None = None
        self._sample_rate: int | None = None
        self._summary["initialization_ms"] = (monotonic_ns() - self._started_ns) / 1e6
        self._started_ns = monotonic_ns()
        self._last_resource_ns = self._started_ns
        self._resources_start = resource_sample()
        self._last_cpu_seconds = self._resources_start["cpu_seconds"]
        self._events = (self.session_dir / "events.jsonl").open("w", encoding="utf-8")
        (self.session_dir / "session.json").write_text(
            json.dumps(self._metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def record_trace(self, record: dict) -> None:
        with self._io_lock:
            if self._closed:
                return
            self._traces.write(json.dumps(record, ensure_ascii=False) + "\n")
            self._traces.flush()

    def record_audio(self, chunk: AudioChunk) -> None:
        with self._io_lock:
            if not self._closed:
                self._record_audio(chunk)

    def _record_audio(self, chunk: AudioChunk) -> None:
        now = monotonic_ns()
        if now - self._last_resource_ns >= 1_000_000_000:
            sample = resource_sample()
            cpu = sample["cpu_seconds"]
            sample["cpu_percent_one_core"] = (
                (cpu - self._last_cpu_seconds) / ((now - self._last_resource_ns) / 1e9) * 100
                if cpu is not None and self._last_cpu_seconds is not None else None)
            self._resources.write(json.dumps({"monotonic_ns": now, **sample}) + "\n")
            self._last_resource_ns = now
            self._last_cpu_seconds = cpu
        self._chunks.write(json.dumps({
            "sequence": chunk.sequence, "callback_ns": chunk.callback_ns,
            "dequeued_ns": chunk.dequeued_ns, "discontinuity": chunk.discontinuity,
            "queue_depth": chunk.queue_depth, "sample_rate": chunk.sample_rate,
            "sample_count": len(chunk.samples),
        }) + "\n")
        if self._wave is None:
            self._sample_rate = chunk.sample_rate
            self._wave = wave.open(str(self.session_dir / "microphone_raw.wav"), "wb")
            self._wave.setnchannels(1)
            self._wave.setsampwidth(2)
            self._wave.setframerate(chunk.sample_rate)
        if chunk.sample_rate != self._sample_rate:
            raise ValueError("diagnostic session sample rate changed")

        try:
            import numpy as np
        except ImportError as error:
            raise RuntimeError("numpy is required for diagnostic WAV output") from error
        samples = np.asarray(chunk.samples, dtype=np.float32)
        pcm16 = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2")
        self._wave.writeframes(pcm16.tobytes())

    def record_result(self, text: str, status: str, detail: Mapping[str, Any]) -> None:
        with self._io_lock:
            if not self._closed:
                self._record_result(text, status, detail)

    def _record_result(self, text: str, status: str, detail: Mapping[str, Any]) -> None:
        device_attempts, device_successes = _device_counts(detail)
        event = BenchmarkEventRecord(
            source="voice",
            input=text,
            status=status,
            perception_ms=(
                round(float(detail["decode_seconds"]) * 1000, 3)
                if isinstance(detail.get("decode_seconds"), (int, float))
                else None
            ),
            processing_ms=_optional_float(detail.get("processing_ms")),
            end_to_end_ms=None,
            device_attempts=device_attempts,
            device_successes=device_successes,
            metrics={
                "audio_seconds": detail.get("audio_seconds"),
                "decode_seconds": detail.get("decode_seconds"),
                "rtf": detail.get("rtf"),
            },
            error=detail.get("error"),
        ).to_dict()
        event.update({
            "schema_version": 2,
            "session_id": self.session_id,
            "trace_id": detail.get("trace", {}).get("trace_id"),
            "event_id": detail.get("trace", {}).get("event_id"),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "text": text,
            **dict(detail),
        })
        self._events.write(json.dumps(event, ensure_ascii=False) + "\n")
        self._events.flush()
        self._event_count += 1
        self._status_counts[status] = self._status_counts.get(status, 0) + 1
        if isinstance(detail.get("rtf"), (int, float)):
            self._rtfs.append(float(detail["rtf"]))
        if isinstance(detail.get("decode_seconds"), (int, float)):
            self._decode_seconds.append(float(detail["decode_seconds"]))

    def close(self) -> None:
        with self._io_lock:
            if self._closed:
                return
            self._closed = True
            self._close()

    def _close(self) -> None:
        self._traces.close()
        self._chunks.close()
        self._resources.close()
        self._summary["schema_version"] = 2
        self._summary["session_id"] = self.session_id
        self._summary["elapsed_seconds"] = (monotonic_ns() - self._started_ns) / 1e9
        self._summary["resources_start"] = self._resources_start
        self._summary["resources_end"] = resource_sample()
        if self._wave is not None:
            self._wave.close()
            self._wave = None
        self._events.close()
        self._summary.update({
            "endpoint_count": self._event_count,
            "status_counts": self._status_counts,
            "mean_rtf": (
                sum(self._rtfs) / len(self._rtfs) if self._rtfs else None
            ),
            "total_decode_seconds": sum(self._decode_seconds),
        })
        (self.session_dir / "summary.json").write_text(
            json.dumps(self._summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def update_summary(self, values: Mapping[str, Any]) -> None:
        self._summary.update(values)


def _device_counts(detail: Mapping[str, Any]) -> tuple[int, int]:
    if "device_id" in detail:
        return 1, 1
    commands = detail.get("commands")
    if isinstance(commands, list):
        return len(commands), sum(bool(item.get("succeeded")) for item in commands)
    return 0, 0


def _optional_float(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None
