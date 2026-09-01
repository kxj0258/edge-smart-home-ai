from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np


@dataclass(frozen=True)
class SynthesizedAudio:
    samples: np.ndarray
    sample_rate: int


class TextToSpeech(Protocol):
    @property
    def num_speakers(self) -> int: ...

    def synthesize(self, text: str, speaker_id: int, speed: float) -> SynthesizedAudio: ...


class SherpaOnnxVitsTts:
    """Small adapter around sherpa-onnx's version-specific OfflineTts API."""

    def __init__(
        self,
        model_dir: Path,
        num_threads: int = 2,
        provider: str = "cpu",
        lexicon_path: Path | None = None,
    ) -> None:
        try:
            import sherpa_onnx
        except ImportError as error:
            raise RuntimeError("sherpa-onnx is required for TTS generation") from error

        model_dir = model_dir.resolve()
        required = ("model.onnx", "lexicon.txt", "tokens.txt")
        missing = [name for name in required if not (model_dir / name).is_file()]
        if missing:
            raise FileNotFoundError(f"missing TTS model files: {', '.join(missing)}")

        vits = sherpa_onnx.OfflineTtsVitsModelConfig(
            model=str(model_dir / "model.onnx"),
            lexicon=str((lexicon_path or model_dir / "lexicon.txt").resolve()),
            tokens=str(model_dir / "tokens.txt"),
        )
        model = sherpa_onnx.OfflineTtsModelConfig(
            vits=vits,
            num_threads=num_threads,
            provider=provider,
        )
        rule_fsts = [model_dir / name for name in ("phone.fst", "number.fst")]
        config = sherpa_onnx.OfflineTtsConfig(
            model=model,
            rule_fsts=",".join(str(path) for path in rule_fsts if path.is_file()),
        )
        if not config.validate():
            raise ValueError(f"invalid sherpa-onnx TTS configuration: {model_dir}")
        self._tts = sherpa_onnx.OfflineTts(config)

    @property
    def num_speakers(self) -> int:
        return int(self._tts.num_speakers)

    @property
    def sample_rate(self) -> int:
        return int(self._tts.sample_rate)

    def synthesize(self, text: str, speaker_id: int, speed: float) -> SynthesizedAudio:
        if not 0 <= speaker_id < self.num_speakers:
            raise ValueError(
                f"speaker ID {speaker_id} is outside [0, {self.num_speakers - 1}]"
            )
        if speed <= 0:
            raise ValueError("speed must be positive")
        audio = self._tts.generate(text=text, sid=speaker_id, speed=speed)
        return SynthesizedAudio(
            samples=np.asarray(audio.samples, dtype=np.float32),
            sample_rate=int(audio.sample_rate),
        )
