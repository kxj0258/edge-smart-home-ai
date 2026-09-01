from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import wave
from typing import Any, Mapping

from edge_smart_home.benchmark.events import BenchmarkEventRecord

from .interfaces import AudioChunk


class AudioDiagnosticRecorder:
    """Persist one reproducible microphone/ASR session without model inference."""

    def __init__(self, output_root: Path, metadata: Mapping[str, Any]) -> None:
        session_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        self.session_dir = output_root / session_id
        self.session_dir.mkdir(parents=True, exist_ok=False)
        self._metadata = dict(metadata)
        self._summary: dict[str, Any] = {}
        self._event_count = 0
        self._status_counts: dict[str, int] = {}
        self._rtfs: list[float] = []
        self._decode_seconds: list[float] = []
        self._wave: wave.Wave_write | None = None
        self._sample_rate: int | None = None
        self._events = (self.session_dir / "events.jsonl").open("w", encoding="utf-8")
        (self.session_dir / "session.json").write_text(
            json.dumps(self._metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def record_audio(self, chunk: AudioChunk) -> None:
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
