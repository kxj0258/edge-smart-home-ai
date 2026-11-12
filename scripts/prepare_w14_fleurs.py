#!/usr/bin/env python3
"""Prepare the fixed first five licensed FLEURS Mandarin development clips.

This is a data-preparation tool, not an ASR benchmark. Original IEEE_FLOAT WAV
bytes are retained, then explicitly quantized to mono 16 kHz PCM16 without
resampling. No listening, recognition, trimming or recognition-based selection
is used. Raw audio belongs under ignored evaluation/ and must not enter Git.
"""
from __future__ import annotations

import argparse
import csv
import datetime
import hashlib
import io
import json
from pathlib import Path
import struct
import sys
import tarfile
from typing import Any
import urllib.request
import wave

REPO = Path(__file__).resolve().parents[1]
REVISION = '70bb2e84b976b7e960aa89f1c648e09c59f894dd'
BASE = 'https://huggingface.co/datasets/google/fleurs/resolve/' + REVISION
ARCHIVE_URL = BASE + '/data/cmn_hans_cn/audio/dev.tar.gz'
ARCHIVE_BYTES = 217347747
ARCHIVE_LFS_SHA256 = '3bc33212d5974eef7feb04bc4792458d6cd7e14ff10a1a24772f3c45ea87a822'
LICENSE_URL = 'https://creativecommons.org/licenses/by/4.0/'
ATTRIBUTION = ('Google FLEURS; Conneau et al., FLEURS: Few-shot Learning Evaluation '
               'of Universal Representations of Speech (2022), https://arxiv.org/abs/2205.12446')
PINNED_CLIPS = (
    ('10116334326946811387.wav', 'f3e12a114bedbc867e06bfb7aceb2721701aa729685b9e4be39f2b98305b72ab'),
    ('10149760684641961155.wav', '577a635c2261680c61dda5fecbc4f83bff6f37f453665b8d9d1a68f210f57c0a'),
    ('10291819997967779677.wav', '8234c830aba9500be3c32dab1f91e72599b9bb03c36f962ae8b6bfc174ca13f0'),
    ('10312747577451821721.wav', '28502266dfa1c0c5202d1488015b29ca0d26fafbf3540cae61f112508caf1ded'),
    ('10323412965683910375.wav', 'c786829b65a5bc73b3730cc2d19cbf77008334be75b1cda306a4184259369671'),
)
DOCUMENTS = {
    'fleurs-card.md': (BASE + '/README.md', '688f79f2a5c731af3796e9f683eb02f9b3f09d040decd8c5625d0f37098e71c6'),
    'fleurs-dev.tsv': (BASE + '/data/cmn_hans_cn/dev.tsv', '6b4efd804b543048feb278db06f3b58b5ea171cdd4ba072e328ad630ca25384b'),
}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def dump(path: Path, value: Any) -> None:
    with path.open('x') as file:
        json.dump(value, file, ensure_ascii=False, indent=2, allow_nan=False)
        file.write('\n')


def numpy() -> Any:
    try:
        import numpy as np
    except ImportError as error:
        raise ValueError('numpy is required; use the existing voice environment') from error
    return np


def read_float32_wav(data: bytes) -> tuple[Any, dict[str, Any]]:
    """Strict RIFF/WAVE parser for the pinned source's normalized float32 form."""
    np = numpy()
    if len(data) < 12 or data[:4] != b'RIFF' or data[8:12] != b'WAVE':
        raise ValueError('source must be little-endian RIFF/WAVE')
    if struct.unpack_from('<I', data, 4)[0] != len(data) - 8:
        raise ValueError('RIFF declared length must equal file length')
    pos = 12
    chunks: list[dict[str, Any]] = []
    known: dict[bytes, bytes] = {}
    while pos < len(data):
        if pos + 8 > len(data):
            raise ValueError('truncated chunk header')
        name, size = struct.unpack_from('<4sI', data, pos)
        end = pos + 8 + size
        if end + size % 2 > len(data):
            raise ValueError('chunk length/padding exceeds RIFF boundary')
        if name in (b'fmt ', b'data', b'fact'):
            if name in known:
                raise ValueError('duplicate fmt/data/fact chunk')
            known[name] = data[pos + 8:end]
        chunks.append({'id': name.decode('ascii', errors='replace'), 'bytes': size})
        pos = end + size % 2
    if b'fmt ' not in known or b'data' not in known:
        raise ValueError('fmt and data chunks are required')
    fmt = known[b'fmt ']
    if len(fmt) not in (16, 18) or (len(fmt) == 18 and struct.unpack_from('<H', fmt, 16)[0] != 0):
        raise ValueError('unsupported IEEE_FLOAT fmt extension')
    code, channels, rate, byte_rate, align, bits = struct.unpack_from('<HHIIHH', fmt)
    if (code, channels, rate, byte_rate, align, bits) != (3, 1, 16000, 64000, 4, 32):
        raise ValueError('source must be IEEE_FLOAT LE float32 mono 16000 Hz')
    payload = known[b'data']
    if not payload or len(payload) % 4:
        raise ValueError('float32 data must contain whole nonempty frames')
    samples = np.frombuffer(payload, dtype='<f4').copy()
    if not np.isfinite(samples).all() or np.any(np.abs(samples.astype(np.float64)) > 1.0):
        raise ValueError('source samples must be finite normalized floats in [-1,1]')
    if b'fact' in known:
        if len(known[b'fact']) != 4 or struct.unpack('<I', known[b'fact'])[0] != len(samples):
            raise ValueError('fact sample count differs from data')
    return samples, {'container': 'RIFF/WAVE', 'format_code': code, 'sample_format': 'IEEE_FLOAT_LE_FLOAT32',
                     'channels': channels, 'sample_rate': rate, 'sample_frames': len(samples),
                     'bits_per_sample': bits, 'duration_seconds': len(samples) / rate, 'chunks': chunks}


def pcm16_derivative(data: bytes) -> tuple[bytes, dict[str, Any]]:
    np = numpy()
    samples, info = read_float32_wav(data)
    source = samples.astype(np.float64)
    rounded = np.rint(source * 32768.0)
    clipped = int(np.count_nonzero((rounded < -32768) | (rounded > 32767)))
    quantized = np.clip(rounded, -32768, 32767).astype('<i2')
    output = io.BytesIO()
    with wave.open(output, 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(quantized.tobytes())
    derived = output.getvalue()
    return derived, {'source_wav': info, 'origin_sha256': sha256(data), 'derived_sha256': sha256(derived),
                     'origin_bytes': len(data), 'derived_bytes': len(derived),
                     'derived_format': 'PCM16_LE', 'sample_rate': 16000, 'sample_frames': len(samples),
                     'channels': 1, 'resampled': False, 'trimmed': False,
                     'formula': 'q=clip(rint(float64(x)*32768),-32768,32767); rint ties to even',
                     'origin_peak_abs': float(np.max(np.abs(source))), 'clipped_sample_count': clipped,
                     'derived_peak_abs': float(np.max(np.abs(quantized.astype(np.float64) / 32768.0))),
                     'max_abs_quantization_error': float(np.max(np.abs(source - quantized.astype(np.float64) / 32768.0)))}


def open_url(url: str) -> Any:
    return urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'zte-w14-evidence/1'}), timeout=60)


class CountedReader:
    def __init__(self, source: Any) -> None:
        self.source = source
        self.bytes_read = 0
        self.hash = hashlib.sha256()

    def read(self, size: int = -1) -> bytes:
        data = self.source.read(size)
        self.bytes_read += len(data)
        self.hash.update(data)
        return data


def acquire(raw: Path) -> Path:
    raw.mkdir()
    wavdir = raw / 'fleurs-wavs'
    wavdir.mkdir()
    for name, (url, expected) in DOCUMENTS.items():
        with open_url(url) as response:
            data = response.read()
        if sha256(data) != expected:
            raise ValueError('pinned source document digest mismatch: ' + name)
        (raw / name).write_bytes(data)
    clips = []
    with open_url(ARCHIVE_URL) as response:
        counted = CountedReader(response)
        with tarfile.open(fileobj=counted, mode='r|gz') as archive:
            for member in archive:
                if not member.isfile() or not member.name.lower().endswith('.wav'):
                    continue
                filename, expected = PINNED_CLIPS[len(clips)]
                if member.name != 'dev/' + filename:
                    raise ValueError('pinned archive first-five order changed')
                data = archive.extractfile(member).read()
                if len(data) != member.size or sha256(data) != expected:
                    raise ValueError('pinned source WAV digest mismatch')
                path = wavdir / filename
                path.write_bytes(data)
                clips.append({'filename': filename, 'source_member': member.name, 'local_path': str(path.resolve()),
                              'sha256': expected, 'bytes': len(data)})
                if len(clips) == 5:
                    break
    if len(clips) != 5:
        raise ValueError('archive contains fewer than five fixed clips')
    acquisition = {'dataset': 'google/fleurs', 'dataset_revision': REVISION, 'config': 'cmn_hans_cn',
                   'selection': 'First five regular WAV members in pinned archive order, before listening or ASR',
                   'archive': {'url': ARCHIVE_URL, 'complete_archive_downloaded': False,
                               'compressed_bytes_read': counted.bytes_read,
                               'compressed_prefix_sha256': counted.hash.hexdigest(), 'full_archive_sha256': None},
                   'clips': clips}
    dump(raw / 'acquisition.json', acquisition)
    return raw / 'acquisition.json'


def prepare(outdir: Path, acquisition_existing: Path | None = None) -> dict[str, Any]:
    if outdir.exists():
        raise ValueError('output directory already exists; refusing any read/network/rewrite')
    outdir.mkdir(parents=True)
    acquisition_path = acquisition_existing.resolve() if acquisition_existing else acquire(outdir / 'raw')
    acquisition_bytes = acquisition_path.read_bytes()
    acquisition = json.loads(acquisition_bytes)
    if (acquisition.get('dataset'), acquisition.get('dataset_revision'), acquisition.get('config')) != ('google/fleurs', REVISION, 'cmn_hans_cn'):
        raise ValueError('acquisition must describe the pinned FLEURS Mandarin source')
    if len(acquisition.get('clips', [])) != 5 or acquisition.get('archive', {}).get('url') != ARCHIVE_URL:
        raise ValueError('acquisition must contain exactly the fixed five clips')
    docs = outdir / 'sources'
    docs.mkdir()
    for name, (_, expected) in DOCUMENTS.items():
        data = (acquisition_path.parent / name).read_bytes()
        if sha256(data) != expected:
            raise ValueError('pinned source document digest mismatch: ' + name)
        (docs / name).write_bytes(data)
    labels = {}
    for row in csv.reader(io.StringIO((docs / 'fleurs-dev.tsv').read_text()), delimiter='\t'):
        if len(row) != 7 or row[1] in labels:
            raise ValueError('invalid/duplicate TSV label row')
        labels[row[1]] = row
    original = outdir / 'original'
    derived = outdir / 'pcm16'
    original.mkdir()
    derived.mkdir()
    cases = []
    for index, (clip, pin) in enumerate(zip(acquisition['clips'], PINNED_CLIPS), 1):
        filename, expected = pin
        if clip['filename'] != filename or clip['source_member'] != 'dev/' + filename or clip['sha256'] != expected:
            raise ValueError('fixed clip order/member/digest mismatch')
        source = Path(clip['local_path'])
        if not source.is_absolute():
            source = REPO / source
        data = source.read_bytes()
        if len(data) != clip['bytes'] or sha256(data) != expected:
            raise ValueError('source WAV bytes differ from pinned source')
        pcm, conversion = pcm16_derivative(data)
        row = labels[filename]
        if int(row[5]) != conversion['sample_frames']:
            raise ValueError('TSV/source sample count differs')
        original_path = original / filename
        pcm_path = derived / filename
        original_path.write_bytes(data)
        pcm_path.write_bytes(pcm)
        cases.append({'id': 'W14-H%02d' % index, 'wav': str(pcm_path.resolve()), 'sha256': sha256(pcm),
                      'expected': row[2], 'source_kind': 'human', 'source_url': ARCHIVE_URL,
                      'license': 'CC-BY-4.0', 'license_url': LICENSE_URL, 'attribution': ATTRIBUTION,
                      'provenance_note': 'Official Google FLEURS human public read speech; cmn_hans_cn dev first five archive members, selected before ASR; not home-command or local microphone data.',
                      'reference_source': DOCUMENTS['fleurs-dev.tsv'][0], 'speaker_id': None,
                      'source_gender_label': row[6], 'source_sentence_id': row[0],
                      'normalized_reference': row[3], 'source_member': clip['source_member'],
                      'original_wav': str(original_path.resolve()), 'origin_sha256': expected,
                      'conversion': conversion})
    manifest = {'schema_version': 1, 'kind': 'w14_short_speech_manifest',
                'created_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                'dataset': 'google/fleurs', 'dataset_revision': REVISION, 'dataset_config': 'cmn_hans_cn',
                'dataset_split': 'validation (upstream dev)', 'cases': cases,
                'acquisition': {'path': str(acquisition_path), 'sha256': sha256(acquisition_bytes),
                                'mode': 'reuse_existing' if acquisition_existing else 'fixed_network_prefix',
                                'archive': acquisition['archive'], 'expected_archive_bytes': ARCHIVE_BYTES,
                                'expected_archive_lfs_sha256': ARCHIVE_LFS_SHA256,
                                'full_archive_digest_locally_verified': False},
                'selection': 'Pinned first five WAV members; no listening or ASR-based selection',
                'modifications': 'Float32 WAV converted to PCM16 with explicit rounding/clipping; no resampling or trimming',
                'limitations': ['Human read speech is not a local home-command dataset or current microphone capture.',
                                'No live overflow, physical devices, real-time pacing, speaker count or personal understanding acceptance.']}
    dump(outdir / 'manifest.json', manifest)
    dump(outdir / 'preparation.json', {'passed': True, 'manifest_sha256': sha256((outdir / 'manifest.json').read_bytes()),
                                      'case_count': len(cases), 'network_acquisition_performed': acquisition_existing is None,
                                      'origin_samples_preserved': True, 'asr_calls': 0, 'listening_or_device_calls': 0})
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--outdir', type=Path, required=True, help='New ignored output directory; existing paths are rejected')
    parser.add_argument('--acquisition-existing', type=Path, help='Reuse a verified acquisition.json and its sibling original WAVs/documents without network')
    args = parser.parse_args(argv)
    try:
        manifest = prepare(args.outdir, args.acquisition_existing)
    except (OSError, ValueError, KeyError, TypeError, tarfile.TarError) as error:
        print(json.dumps({'passed': False, 'error': type(error).__name__, 'message': str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps({'passed': True, 'manifest': str((args.outdir / 'manifest.json').resolve()),
                      'case_count': len(manifest['cases']), 'source_kind': 'human'}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
