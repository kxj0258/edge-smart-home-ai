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
    def accept(self, chunk: AudioChunk) -> Sequence[str]:
        """Accept audio and return final texts completed at endpoints."""
