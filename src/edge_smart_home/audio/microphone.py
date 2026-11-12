from __future__ import annotations

import logging
from queue import Empty, Full, Queue
from threading import Event
from time import monotonic_ns
from dataclasses import replace
from typing import Any, Callable, Iterable

from .interfaces import AudioChunk, AudioSource
from .sherpa_onnx_adapters import VoiceDependencyError


LOGGER = logging.getLogger(__name__)


class SoundDeviceMicrophone(AudioSource):
    def __init__(
        self,
        chunk_duration_ms: int,
        device: int | None = None,
        microphone_sample_rate: int | None = None,
        capture_blocked: Callable[[], bool] | None = None,
    ) -> None:
        self.chunk_duration_ms = chunk_duration_ms
        self.device = device
        self.microphone_sample_rate = microphone_sample_rate
        self.capture_blocked = capture_blocked
        self.portaudio_overflow_count = 0
        self.queue_drop_count = 0
        self.blocked_chunk_count = 0
        self.blocked_audio_seconds = 0.0
        self._consumer_blocked_count = 0
        self._consumer_blocked_seconds = 0.0
        self.queue_high_water = 0
        self.max_queue_wait_ms = 0.0
        self.callback_count = 0
        self.callback_max_ms = 0.0
        self.actual_sample_rate = None
        self._stop_event = Event()

    def metrics(self) -> dict:
        result = {key: getattr(self, key) for key in (
            "portaudio_overflow_count", "queue_drop_count", "blocked_chunk_count",
            "blocked_audio_seconds", "queue_high_water", "max_queue_wait_ms",
            "callback_count", "callback_max_ms", "actual_sample_rate")}
        result["blocked_chunk_count"] += self._consumer_blocked_count
        result["blocked_audio_seconds"] += self._consumer_blocked_seconds
        return result

    def stop(self) -> None:
        """Request shutdown of an active microphone stream."""

        self._stop_event.set()

    def chunks(self) -> Iterable[AudioChunk]:
        try:
            import sounddevice as sd
        except ImportError as error:
            raise VoiceDependencyError(
                "sounddevice is not installed; install the project's voice extra"
            ) from error
        input_rate = self.microphone_sample_rate
        if input_rate is None:
            device_info = sd.query_devices(self.device, "input")
            input_rate = int(round(float(device_info["default_samplerate"])))
        self.actual_sample_rate = input_rate
        frames_per_chunk = input_rate * self.chunk_duration_ms // 1000
        audio_queue: Queue[Any] = Queue(maxsize=50)
        dropped_chunks = 0
        last_sequence = None
        reported_overflows = 0

        def capture_callback(indata: Any, frames: int, time: Any, status: Any) -> None:
            nonlocal dropped_chunks
            started = monotonic_ns()
            self.callback_count += 1
            if status:
                if getattr(status, "input_overflow", False):
                    self.portaudio_overflow_count += 1
            if self.capture_blocked and self.capture_blocked():
                self.blocked_chunk_count += 1
                self.blocked_audio_seconds += frames / input_rate
                self.callback_max_ms = max(self.callback_max_ms, (monotonic_ns() - started) / 1e6)
                return
            samples = AudioChunk(indata[:, 0].copy().reshape(-1), input_rate,
                                 sequence=self.callback_count, callback_ns=started,
                                 discontinuity=bool(getattr(status, "input_overflow", False)))
            try:
                audio_queue.put_nowait(samples)
            except Full:
                try:
                    audio_queue.get_nowait()
                except Empty:
                    pass
                else:
                    dropped_chunks += 1
                    self.queue_drop_count = dropped_chunks
                audio_queue.put_nowait(samples)
            self.queue_high_water = max(self.queue_high_water, audio_queue.qsize())
            self.callback_max_ms = max(self.callback_max_ms, (monotonic_ns() - started) / 1e6)

        try:
            with sd.InputStream(
                channels=1,
                dtype="float32",
                samplerate=input_rate,
                device=self.device,
                blocksize=frames_per_chunk,
                callback=capture_callback,
            ) as stream:
                while not self._stop_event.is_set():
                    try:
                        samples = audio_queue.get(timeout=0.1)
                    except Empty:
                        continue
                    if self.capture_blocked and self.capture_blocked():
                        self._consumer_blocked_count += 1
                        self._consumer_blocked_seconds += len(samples.samples) / input_rate
                        continue
                    dequeued = monotonic_ns()
                    self.max_queue_wait_ms = max(self.max_queue_wait_ms,
                                                (dequeued - samples.callback_ns) / 1e6)
                    if self.portaudio_overflow_count != reported_overflows:
                        reported_overflows = self.portaudio_overflow_count
                        LOGGER.warning("microphone input overflow total: %d", reported_overflows)
                    gap = samples.sequence != (last_sequence + 1 if last_sequence is not None else 1)
                    last_sequence = samples.sequence
                    yield replace(samples, dequeued_ns=dequeued,
                                  discontinuity=samples.discontinuity or gap,
                                  queue_depth=audio_queue.qsize())
        except sd.PortAudioError as error:
            raise VoiceDependencyError(
                f"cannot open microphone device {self.device!r} at {input_rate} Hz: {error}"
            ) from error
