from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from numbers import Real
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
        *,
        rule_far_paths: tuple[Path, ...] = (),
        noise_scale: float | None = None,
        noise_scale_w: float | None = None,
        debug: bool = False,
    ) -> None:
        model_dir = model_dir.resolve()
        required = ("model.onnx", "lexicon.txt", "tokens.txt")
        missing = [name for name in required if not (model_dir / name).is_file()]
        if missing:
            raise FileNotFoundError(f"missing TTS model files: {', '.join(missing)}")

        selected_lexicon = (lexicon_path or model_dir / "lexicon.txt").resolve()
        if not selected_lexicon.is_file():
            raise FileNotFoundError(f"TTS lexicon does not exist: {selected_lexicon}")
        if not isinstance(rule_far_paths, tuple):
            raise ValueError("rule_far_paths must be a tuple of file paths")
        fars = tuple(Path(path).resolve() for path in rule_far_paths)
        if len(set(fars)) != len(fars):
            raise ValueError("duplicate TTS FAR files are not allowed")
        for path in fars:
            if not path.is_file():
                raise FileNotFoundError(f"TTS FAR does not exist: {path}")
        if type(debug) is not bool:
            raise ValueError("debug must be boolean")
        noise_options = {}
        for name, value in (("noise_scale", noise_scale), ("noise_scale_w", noise_scale_w)):
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, Real) or not isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and nonnegative")
            noise_options[name] = float(value)

        try:
            import sherpa_onnx
        except ImportError as error:
            raise RuntimeError("sherpa-onnx is required for TTS generation") from error

        vits = sherpa_onnx.OfflineTtsVitsModelConfig(
            model=str(model_dir / "model.onnx"),
            lexicon=str(selected_lexicon),
            tokens=str(model_dir / "tokens.txt"),
            **noise_options,
        )
        model = sherpa_onnx.OfflineTtsModelConfig(
            vits=vits,
            num_threads=num_threads,
            provider=provider,
            **({"debug": True} if debug else {}),
        )
        rule_fsts = [model_dir / name for name in ("phone.fst", "number.fst")]
        config = sherpa_onnx.OfflineTtsConfig(
            model=model,
            rule_fsts=",".join(str(path) for path in rule_fsts if path.is_file()),
            **({"rule_fars": ",".join(str(path) for path in fars)} if fars else {}),
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
