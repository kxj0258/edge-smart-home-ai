from __future__ import annotations

import logging
from queue import Empty, Full, Queue
from typing import Any, Iterable

from .interfaces import AudioChunk, AudioSource
from .sherpa_onnx_adapters import VoiceDependencyError


LOGGER = logging.getLogger(__name__)


class SoundDeviceMicrophone(AudioSource):
    def __init__(
        self,
        chunk_duration_ms: int,
        device: int | None = None,
        microphone_sample_rate: int | None = None,
    ) -> None:
        self.chunk_duration_ms = chunk_duration_ms
        self.device = device
        self.microphone_sample_rate = microphone_sample_rate

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
        frames_per_chunk = input_rate * self.chunk_duration_ms // 1000
        audio_queue: Queue[Any] = Queue(maxsize=50)
        dropped_chunks = 0

        def capture_callback(indata: Any, frames: int, time: Any, status: Any) -> None:
            nonlocal dropped_chunks
            if status:
                LOGGER.warning("microphone status: %s", status)
            samples = indata[:, 0].copy()
            try:
                audio_queue.put_nowait(samples)
            except Full:
                try:
                    audio_queue.get_nowait()
                except Empty:
                    pass
                audio_queue.put_nowait(samples)
                dropped_chunks += 1
                if dropped_chunks == 1 or dropped_chunks % 10 == 0:
                    LOGGER.warning(
                        "audio processing is behind; dropped %d old chunk(s)",
                        dropped_chunks,
                    )

        try:
            with sd.InputStream(
                channels=1,
                dtype="float32",
                samplerate=input_rate,
                device=self.device,
                blocksize=frames_per_chunk,
                callback=capture_callback,
            ) as stream:
                while True:
                    samples = audio_queue.get()
                    yield AudioChunk(samples.reshape(-1), input_rate)
        except sd.PortAudioError as error:
            raise VoiceDependencyError(
                f"cannot open microphone device {self.device!r} at {input_rate} Hz: {error}"
            ) from error
