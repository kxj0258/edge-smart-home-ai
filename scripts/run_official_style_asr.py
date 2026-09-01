"""ASR-only reference following sherpa-onnx's official online examples.

This intentionally bypasses this project's microphone, pipeline, router, and device
code so a diagnostic WAV can be decoded as an independent comparison.
Upstream reference:
https://github.com/k2-fsa/sherpa-onnx/blob/master/python-api-examples/
speech-recognition-from-microphone-with-endpoint-detection.py
"""

from __future__ import annotations

import argparse
from pathlib import Path
import wave

import numpy as np
import sherpa_onnx

from edge_smart_home.audio.hotwords import create_validated_hotwords_file


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("wav", type=Path)
    parser.add_argument("--tokens", type=Path, required=True)
    parser.add_argument("--encoder", type=Path, required=True)
    parser.add_argument("--decoder", type=Path, required=True)
    parser.add_argument("--joiner", type=Path, required=True)
    parser.add_argument("--hotwords", type=Path)
    args = parser.parse_args()

    validated_hotwords = None
    if args.hotwords:
        validated_hotwords, validation = create_validated_hotwords_file(
            args.tokens, args.hotwords
        )
        print(f"Hotwords: {len(validation.valid)} valid, {len(validation.invalid)} skipped")
    recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
        tokens=str(args.tokens),
        encoder=str(args.encoder),
        decoder=str(args.decoder),
        joiner=str(args.joiner),
        num_threads=2,
        sample_rate=16000,
        enable_endpoint_detection=True,
        rule1_min_trailing_silence=2.4,
        rule2_min_trailing_silence=1.2,
        rule3_min_utterance_length=20.0,
        decoding_method="modified_beam_search" if validated_hotwords else "greedy_search",
        hotwords_file=str(validated_hotwords) if validated_hotwords else "",
        hotwords_score=1.5,
        modeling_unit="cjkchar",
    )
    if validated_hotwords:
        validated_hotwords.unlink(missing_ok=True)
    samples, input_rate = read_pcm16_mono(args.wav)
    stream = recognizer.create_stream()
    chunk_size = input_rate // 10
    for offset in range(0, len(samples), chunk_size):
        stream.accept_waveform(input_rate, samples[offset : offset + chunk_size])
        decode_ready(recognizer, stream)
        if recognizer.is_endpoint(stream):
            text = recognizer.get_result_all(stream).text.strip()
            if text:
                print(text)
            recognizer.reset(stream)

    stream.accept_waveform(input_rate, np.zeros(input_rate // 2, dtype=np.float32))
    stream.input_finished()
    decode_ready(recognizer, stream)
    text = recognizer.get_result_all(stream).text.strip()
    if text:
        print(text)


def decode_ready(recognizer, stream) -> None:
    while recognizer.is_ready(stream):
        recognizer.decode_stream(stream)


def read_pcm16_mono(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as wav:
        if wav.getnchannels() != 1 or wav.getsampwidth() != 2:
            raise ValueError("expected mono 16-bit PCM WAV")
        sample_rate = wav.getframerate()
        samples = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2")
    return samples.astype(np.float32) / 32768.0, sample_rate


if __name__ == "__main__":
    main()
