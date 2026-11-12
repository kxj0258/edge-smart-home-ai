"""Check the normalized mono boundary before passing audio to native code.

The ASR/VAD adapters accept floating samples in [-1, 1]. Integer PCM needs an
explicit, format-aware conversion at the decoder (PCM16 / 32768, for example);
silently casting it to float32 does not normalize it. This module neither
resamples nor downmixes, clips, denoises or repairs missing capture blocks.
"""
from __future__ import annotations

from numbers import Integral
from typing import Any


class AudioInputError(ValueError):
    """A rejected audio input, separate from intent/model/execution errors."""

    stage = "audio_input"

    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.code = code


def validate_sample_rate(sample_rate: Any) -> int:
    """Return a positive rate representable by sherpa-onnx's int32 API."""
    if (isinstance(sample_rate, bool) or not isinstance(sample_rate, Integral)
            or sample_rate <= 0 or sample_rate > 2**31 - 1):
        raise AudioInputError(
            "sample_rate must be a positive 32-bit integer in Hz",
            "AUDIO_SAMPLE_RATE_INVALID",
        )
    return int(sample_rate)


def validate_audio_samples(samples: Any, sample_rate: Any) -> Any:
    """Return finite, normalized, contiguous float32 mono samples.

    Floating lists and float arrays are supported. Multi-channel data must be
    explicitly downmixed by its owner; PCM integers, booleans and byte strings
    are rejected rather than guessed. NumPy remains a lazy voice dependency.
    """
    validate_sample_rate(sample_rate)
    try:
        import numpy as np
    except ImportError as error:
        # Keep the existing voice dependency exception instead of making basic
        # package import require NumPy or creating an intent error code.
        from .sherpa_onnx_adapters import VoiceDependencyError
        raise VoiceDependencyError("numpy is required for audio validation") from error

    try:
        waveform = np.asarray(samples)
    except (ValueError, TypeError) as error:
        raise AudioInputError(
            "audio samples must be a one-dimensional floating array",
            "AUDIO_SAMPLE_TYPE_INVALID",
        ) from error
    if waveform.ndim != 1:
        raise AudioInputError("audio samples must be mono (one dimension)", "AUDIO_SHAPE_INVALID")
    if waveform.size == 0:
        raise AudioInputError("audio samples must not be empty", "AUDIO_EMPTY")
    if waveform.dtype.kind != "f":
        raise AudioInputError(
            "audio samples must be normalized floats; convert encoded PCM explicitly",
            "AUDIO_SAMPLE_TYPE_INVALID",
        )
    if not np.isfinite(waveform).all():
        raise AudioInputError("audio samples must be finite", "AUDIO_NONFINITE")
    if np.any(waveform < -1.0) or np.any(waveform > 1.0):
        raise AudioInputError("audio samples must lie in [-1, 1]", "AUDIO_RANGE_INVALID")
    return np.ascontiguousarray(waveform, dtype=np.float32)
