from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .interfaces import AudioChunk, StreamingSpeechRecognizer, VoiceActivityDetector


class VoiceDependencyError(RuntimeError):
    """A voice runtime dependency or model is unavailable."""


def _sherpa_onnx() -> Any:
    try:
        import sherpa_onnx
    except ImportError as error:
        raise VoiceDependencyError(
            "sherpa-onnx is not installed; install the project's voice extra"
        ) from error
    return sherpa_onnx


def _require_file(path: Path, label: str) -> str:
    if not path.is_file():
        raise VoiceDependencyError(f"{label} file does not exist: {path}")
    return str(path)


class SherpaOnnxVad(VoiceActivityDetector):
    def __init__(self, model: Path, sample_rate: int, config: Mapping[str, Any]) -> None:
        sherpa_onnx = _sherpa_onnx()
        vad_config = sherpa_onnx.VadModelConfig()
        vad_config.silero_vad.model = _require_file(model, "VAD model")
        vad_config.silero_vad.threshold = float(config["threshold"])
        vad_config.silero_vad.min_silence_duration = float(config["min_silence_duration"])
        vad_config.silero_vad.min_speech_duration = float(config["min_speech_duration"])
        vad_config.silero_vad.max_speech_duration = float(config["max_speech_duration"])
        vad_config.sample_rate = sample_rate
        self._sample_rate = sample_rate
        self._vad = sherpa_onnx.VoiceActivityDetector(
            vad_config,
            buffer_size_in_seconds=float(config["buffer_size_seconds"]),
        )

    def accept(self, chunk: AudioChunk) -> tuple[AudioChunk, ...]:
        if chunk.sample_rate != self._sample_rate:
            raise ValueError("audio source and VAD sample rates must match")
        self._vad.accept_waveform(chunk.samples)
        segments = []
        while not self._vad.empty():
            segments.append(AudioChunk(self._vad.front.samples, self._sample_rate))
            self._vad.pop()
        return tuple(segments)


class SherpaOnnxStreamingAsr(StreamingSpeechRecognizer):
    def __init__(
        self,
        tokens: Path,
        encoder: Path,
        decoder: Path,
        joiner: Path,
        sample_rate: int,
        config: Mapping[str, Any],
        hotwords_file: Path | None = None,
    ) -> None:
        sherpa_onnx = _sherpa_onnx()
        self._sample_rate = sample_rate
        self._tail_padding_seconds = float(config["tail_padding_seconds"])
        endpoint = config["endpoint"]
        self._recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
            tokens=_require_file(tokens, "tokens"),
            encoder=_require_file(encoder, "encoder"),
            decoder=_require_file(decoder, "decoder"),
            joiner=_require_file(joiner, "joiner"),
            num_threads=int(config["num_threads"]),
            sample_rate=sample_rate,
            decoding_method=str(config["decoding_method"]),
            max_active_paths=int(config["max_active_paths"]),
            enable_endpoint_detection=True,
            rule1_min_trailing_silence=float(endpoint["rule1_min_trailing_silence"]),
            rule2_min_trailing_silence=float(endpoint["rule2_min_trailing_silence"]),
            rule3_min_utterance_length=float(endpoint["rule3_min_utterance_length"]),
            hotwords_file=(
                _require_file(hotwords_file, "hotwords") if hotwords_file else ""
            ),
            hotwords_score=float(config["hotwords_score"]),
            modeling_unit=str(config["modeling_unit"]),
        )
        self._stream = self._recognizer.create_stream()

    def accept(self, chunk: AudioChunk) -> tuple[str, ...]:
        # sherpa-onnx performs native low-pass resampling when chunk.sample_rate
        # differs from the recognizer feature sample rate.
        self._stream.accept_waveform(chunk.sample_rate, chunk.samples)
        self._decode_ready()
        if not self._recognizer.is_endpoint(self._stream):
            return ()

        self._append_tail_padding(chunk.sample_rate)
        self._decode_ready()
        text = self._recognizer.get_result_all(self._stream).text.strip()
        self._recognizer.reset(self._stream)
        return (text,) if text else ()

    def _decode_ready(self) -> None:
        while self._recognizer.is_ready(self._stream):
            self._recognizer.decode_stream(self._stream)

    def _append_tail_padding(self, input_sample_rate: int) -> None:
        try:
            import numpy as np
        except ImportError as error:
            raise VoiceDependencyError("numpy is required for ASR tail padding") from error
        tail = np.zeros(
            int(input_sample_rate * self._tail_padding_seconds), dtype=np.float32
        )
        self._stream.accept_waveform(input_sample_rate, tail)
