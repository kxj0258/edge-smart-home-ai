from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Iterable, Sequence


@dataclass(frozen=True, slots=True)
class AudioChunk:
    samples: Any
    sample_rate: int

    def __post_init__(self) -> None:
        if self.sample_rate <= 0:
            raise ValueError("sample_rate must be positive")


@dataclass(frozen=True, slots=True)
class StreamingRecognition:
    text: str
    audio_seconds: float
    decode_seconds: float

    @property
    def rtf(self) -> float | None:
        if self.audio_seconds <= 0:
            return None
        return self.decode_seconds / self.audio_seconds


class AudioSource(ABC):
    @abstractmethod
    def chunks(self) -> Iterable[AudioChunk]:
        """Yield mono float32 audio chunks."""


class VoiceActivityDetector(ABC):
    @abstractmethod
    def accept(self, chunk: AudioChunk) -> Sequence[AudioChunk]:
        """Accept audio and return zero or more completed speech segments."""


class StreamingSpeechRecognizer(ABC):
    @abstractmethod
    def accept(self, chunk: AudioChunk) -> Sequence[StreamingRecognition]:
        """Accept audio and return final recognitions completed at endpoints."""

    @abstractmethod
    def finish(self) -> Sequence[StreamingRecognition]:
        """Flush the final stream with tail padding and finish input."""
