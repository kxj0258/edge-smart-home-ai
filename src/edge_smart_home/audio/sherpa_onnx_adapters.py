from __future__ import annotations

from pathlib import Path
from time import perf_counter, monotonic_ns
from edge_smart_home.common.observability import Trace, activate, span
from typing import Any, Mapping

from .hotwords import HotwordValidationResult, create_validated_hotwords_file
from .interfaces import (
    AudioChunk,
    StreamingRecognition,
    StreamingSpeechRecognizer,
    VoiceActivityDetector,
)


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
        self.hotword_validation = HotwordValidationResult((), ())
        validated_hotwords: Path | None = None
        if hotwords_file:
            validated_hotwords, self.hotword_validation = create_validated_hotwords_file(
                tokens, hotwords_file
            )
        endpoint = config["endpoint"]
        try:
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
                hotwords_file=str(validated_hotwords) if validated_hotwords else "",
                hotwords_score=float(config["hotwords_score"]),
                modeling_unit=str(config["modeling_unit"]),
            )
        finally:
            if validated_hotwords:
                validated_hotwords.unlink(missing_ok=True)
        self._stream = self._recognizer.create_stream()
        self._audio_seconds = 0.0
        self._decode_seconds = 0.0
        self._last_input_sample_rate = sample_rate
        self._finished = False
        self._trace = Trace()

    def accept(self, chunk: AudioChunk) -> tuple[StreamingRecognition, ...]:
        if self._finished:
            raise RuntimeError("recognizer stream is already finished")
        # sherpa-onnx performs native low-pass resampling when chunk.sample_rate
        # differs from the recognizer feature sample rate.
        if chunk.callback_ns is not None:
            if "capture_callback" not in self._trace.marks:
                self._trace.mark("capture_callback", chunk.callback_ns)
            self._trace.mark("last_capture_callback", chunk.callback_ns)
        if chunk.callback_ns is not None and chunk.dequeued_ns is not None:
            self._trace.add("audio_queue_wait", chunk.callback_ns, chunk.dequeued_ns,
                            sequence=chunk.sequence, discontinuity=chunk.discontinuity,
                            queue_depth=chunk.queue_depth)
        with activate(self._trace), span("asr_accept_resample"):
            self._stream.accept_waveform(chunk.sample_rate, chunk.samples)
        self._audio_seconds += len(chunk.samples) / chunk.sample_rate
        self._last_input_sample_rate = chunk.sample_rate
        with activate(self._trace), span("asr_decode"):
            self._decode_ready()
        if not self._recognizer.is_endpoint(self._stream):
            return ()

        text = self._recognizer.get_result_all(self._stream).text.strip()
        result = self._build_result(text)
        self._recognizer.reset(self._stream)
        self._reset_metrics()
        return (result,) if text else ()

    def finish(self) -> tuple[StreamingRecognition, ...]:
        if self._finished:
            return ()
        self._finished = True
        with activate(self._trace), span("asr_finish"):
            self._append_tail_padding(self._last_input_sample_rate)
            self._stream.input_finished()
            self._decode_ready()
        text = self._recognizer.get_result_all(self._stream).text.strip()
        result = self._build_result(text)
        self._reset_metrics()
        return (result,) if text else ()

    def reset_current(self) -> None:
        """Discard an incomplete utterance, used by bounded evaluation capture."""
        if self._finished:
            raise RuntimeError("recognizer stream is already finished")
        self._recognizer.reset(self._stream)
        self._reset_metrics()

    def transcribe_waveform(
        self, samples: Any, input_sample_rate: int, chunk_duration_ms: int = 100
    ) -> StreamingRecognition:
        """Decode one independent waveform while reusing the loaded model."""
        if input_sample_rate <= 0 or chunk_duration_ms <= 0:
            raise ValueError("sample rate and chunk duration must be positive")
        try:
            import numpy as np
        except ImportError as error:
            raise VoiceDependencyError("numpy is required for ASR evaluation") from error

        waveform = np.asarray(samples, dtype=np.float32)
        stream = self._recognizer.create_stream()
        chunk_size = max(1, input_sample_rate * chunk_duration_ms // 1000)
        texts: list[str] = []
        decode_seconds = 0.0

        def decode_ready() -> None:
            nonlocal decode_seconds
            started = perf_counter()
            while self._recognizer.is_ready(stream):
                self._recognizer.decode_stream(stream)
            decode_seconds += perf_counter() - started

        for offset in range(0, len(waveform), chunk_size):
            stream.accept_waveform(
                input_sample_rate, waveform[offset : offset + chunk_size]
            )
            decode_ready()
            if self._recognizer.is_endpoint(stream):
                text = self._recognizer.get_result_all(stream).text.strip()
                if text:
                    texts.append(text)
                self._recognizer.reset(stream)

        tail = np.zeros(
            int(input_sample_rate * self._tail_padding_seconds), dtype=np.float32
        )
        stream.accept_waveform(input_sample_rate, tail)
        stream.input_finished()
        decode_ready()
        text = self._recognizer.get_result_all(stream).text.strip()
        if text:
            texts.append(text)
        return StreamingRecognition(
            text="".join(texts),
            audio_seconds=len(waveform) / input_sample_rate,
            decode_seconds=decode_seconds,
        )

    def _decode_ready(self) -> None:
        started = perf_counter()
        while self._recognizer.is_ready(self._stream):
            self._recognizer.decode_stream(self._stream)
        self._decode_seconds += perf_counter() - started

    def _append_tail_padding(self, input_sample_rate: int) -> None:
        try:
            import numpy as np
        except ImportError as error:
            raise VoiceDependencyError("numpy is required for ASR tail padding") from error
        tail = np.zeros(
            int(input_sample_rate * self._tail_padding_seconds), dtype=np.float32
        )
        self._stream.accept_waveform(input_sample_rate, tail)

    def _build_result(self, text: str) -> StreamingRecognition:
        self._trace.mark("recognition_ready")
        return StreamingRecognition(text, self._audio_seconds, self._decode_seconds, self._trace)

    def _reset_metrics(self) -> None:
        self._trace = Trace()
        self._audio_seconds = 0.0
        self._decode_seconds = 0.0
