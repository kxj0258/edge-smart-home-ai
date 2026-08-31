from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import wave
from typing import Any, Mapping

from .interfaces import AudioChunk


class AudioDiagnosticRecorder:
    """Persist one reproducible microphone/ASR session without model inference."""

    def __init__(self, output_root: Path, metadata: Mapping[str, Any]) -> None:
        session_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        self.session_dir = output_root / session_id
        self.session_dir.mkdir(parents=True, exist_ok=False)
        self._metadata = dict(metadata)
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
        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "text": text,
            "status": status,
            **dict(detail),
        }
        self._events.write(json.dumps(event, ensure_ascii=False) + "\n")
        self._events.flush()

    def close(self) -> None:
        if self._wave is not None:
            self._wave.close()
            self._wave = None
        self._events.close()

