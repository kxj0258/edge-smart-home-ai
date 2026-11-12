"""Synthetic fixtures exercise preparation; they are not recorded-speech evidence."""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
from pathlib import Path
import struct
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import wave

MODULE_PATH = Path(__file__).resolve().parents[1] / 'scripts/prepare_w14_fleurs.py'
SPEC = importlib.util.spec_from_file_location('w14_fleurs', MODULE_PATH)
F = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(F)


def chunk(name, payload):
    return name + struct.pack('<I', len(payload)) + payload + (b'\0' if len(payload) % 2 else b'')


def riff(parts):
    body = b'WAVE' + b''.join(parts)
    return b'RIFF' + struct.pack('<I', len(body)) + body


def float_wav(values, *, code=3, channels=1, rate=16000, align=4, bits=32, fact=None):
    fmt = struct.pack('<HHIIHHH', code, channels, rate, rate * align, align, bits, 0)
    data = struct.pack('<' + 'f' * len(values), *values)
    return riff([chunk(b'fmt ', fmt), chunk(b'fact', struct.pack('<I', len(values) if fact is None else fact)), chunk(b'data', data)])


class PreparationTests(unittest.TestCase):
    def fixture(self, root):
        src = root / 'fixture'
        src.mkdir()
        pins = []
        clips = []
        tsv = []
        for i in range(1, 6):
            name = str(i) + '.wav'
            data = float_wav([0.0, i / 10.0, -i / 10.0])
            (src / name).write_bytes(data)
            digest = hashlib.sha256(data).hexdigest()
            pins.append((name, digest))
            clips.append({'filename': name, 'source_member': 'dev/' + name,
                          'local_path': str(src / name), 'sha256': digest, 'bytes': len(data)})
            tsv.append(f'{i}\t{name}\t原始文本{i}。\t规范文本{i}\t词{i}\t3\tMALE\n')
        card = b'fixture cc-by-4.0'
        labels = ''.join(tsv).encode()
        docs = {'fleurs-card.md': ('https://invalid.test/card', F.sha256(card)),
                'fleurs-dev.tsv': ('https://invalid.test/tsv', F.sha256(labels))}
        (src / 'fleurs-card.md').write_bytes(card)
        (src / 'fleurs-dev.tsv').write_bytes(labels)
        acquisition = {'dataset': 'google/fleurs', 'dataset_revision': F.REVISION, 'config': 'cmn_hans_cn',
                       'archive': {'url': F.ARCHIVE_URL, 'complete_archive_downloaded': False}, 'clips': clips}
        path = src / 'acquisition.json'
        path.write_text(json.dumps(acquisition))
        return src, path, pins, docs

    def test_float_wave_decodes_without_resampling(self):
        samples, metadata = F.read_float32_wav(float_wav([0.0, .5, -1.0, 1.0]))
        self.assertEqual(samples.tolist(), [0.0, .5, -1.0, 1.0])
        self.assertEqual((metadata['sample_rate'], metadata['sample_frames']), (16000, 4))

    def test_quantization_ties_extremes_and_error_are_explicit(self):
        values = [0.0, 1 / 65536, 3 / 65536, -1 / 65536, -3 / 65536, 1.0, -1.0]
        result, metadata = F.pcm16_derivative(float_wav(values))
        with wave.open(io.BytesIO(result), 'rb') as wav:
            self.assertEqual((wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getnframes()), (1, 2, 16000, 7))
            self.assertEqual(struct.unpack('<7h', wav.readframes(7)), (0, 0, 2, 0, -2, 32767, -32768))
        self.assertEqual(metadata['clipped_sample_count'], 1)
        self.assertEqual(metadata['max_abs_quantization_error'], 1 / 32768)
        self.assertFalse(metadata['resampled'])

    def test_rejects_nan_infinite_and_out_of_range_sources(self):
        for value in [float('nan'), float('inf'), -float('inf'), 1.0001, -1.0001]:
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'finite normalized'):
                F.read_float32_wav(float_wav([value]))

    def test_rejects_container_and_declared_length_corruption(self):
        valid = float_wav([0.0])
        corrupt = [b'', b'RIFX' + valid[4:], valid + b'\0', valid[:-1], valid[:4] + struct.pack('<I', len(valid)) + valid[8:]]
        for data in corrupt:
            with self.subTest(data=data[:12]), self.assertRaises(ValueError):
                F.read_float32_wav(data)

    def test_rejects_wrong_format_channels_rate_and_alignment(self):
        for kwargs in [{'code': 1}, {'channels': 2}, {'rate': 8000}, {'align': 2}, {'bits': 16}]:
            with self.subTest(kwargs=kwargs), self.assertRaisesRegex(ValueError, 'IEEE_FLOAT'):
                F.read_float32_wav(float_wav([0.0], **kwargs))

    def test_rejects_ambiguous_chunks_and_frame_counts(self):
        fmt = struct.pack('<HHIIHH', 3, 1, 16000, 64000, 4, 32)
        base = [chunk(b'fmt ', fmt), chunk(b'data', struct.pack('<f', .5))]
        invalid = [riff(base + [base[0]]), riff(base + [base[1]]), riff(base[:1]),
                   riff([chunk(b'data', b'')]), riff(base + [chunk(b'fact', b'bad')]),
                   riff(base + [chunk(b'fact', struct.pack('<I', 2))]),
                   riff(base) + b'bad']
        for data in invalid:
            with self.subTest(data=len(data)), self.assertRaises(ValueError):
                F.read_float32_wav(data)

    def test_rejects_truncated_unknown_chunk_and_odd_padding(self):
        header = chunk(b'fmt ', struct.pack('<HHIIHH', 3, 1, 16000, 64000, 4, 32))
        body = b'WAVE' + header + b'JUNK' + struct.pack('<I', 5) + b'12345'
        data = b'RIFF' + struct.pack('<I', len(body)) + body
        with self.assertRaisesRegex(ValueError, 'padding'):
            F.read_float32_wav(data)

    def test_rejects_empty_and_partial_float_frames(self):
        fmt = chunk(b'fmt ', struct.pack('<HHIIHH', 3, 1, 16000, 64000, 4, 32))
        for payload in [b'', b'abc']:
            with self.subTest(payload=payload), self.assertRaisesRegex(ValueError, 'whole nonempty'):
                F.read_float32_wav(riff([fmt, chunk(b'data', payload)]))

    def test_existing_acquisition_preserves_original_and_uses_original_reference(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, path, pins, docs = self.fixture(root)
            with patch.object(F, 'PINNED_CLIPS', tuple(pins)), patch.object(F, 'DOCUMENTS', docs), patch.object(F, 'open_url') as network:
                manifest = F.prepare(root / 'out', path)
            network.assert_not_called()
            self.assertEqual(len(manifest['cases']), 5)
            self.assertEqual(manifest['cases'][0]['expected'], '原始文本1。')
            for case, (_, expected) in zip(manifest['cases'], pins):
                self.assertEqual(F.sha256(Path(case['original_wav']).read_bytes()), expected)
                self.assertEqual(case['conversion']['sample_frames'], 3)
                self.assertEqual(case['source_kind'], 'human')
                self.assertIsNone(case['speaker_id'])
                self.assertEqual(F.sha256(Path(case['wav']).read_bytes()), case['sha256'])

    def test_existing_output_is_rejected_before_reads_or_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            marker = path / 'keep.bin'
            marker.write_bytes(b'unchanged')
            with patch.object(F, 'open_url') as network, patch.object(Path, 'read_bytes', side_effect=AssertionError('unexpected read')):
                with self.assertRaisesRegex(ValueError, 'already exists'):
                    F.prepare(path, path / 'missing.json')
            network.assert_not_called()
            self.assertEqual(marker.read_bytes(), b'unchanged')

    def test_wrong_revision_and_member_order_are_rejected(self):
        for mutation in ['revision', 'order']:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                _, path, pins, docs = self.fixture(root)
                acquisition = json.loads(path.read_text())
                if mutation == 'revision':
                    acquisition['dataset_revision'] = 'mutable-main'
                else:
                    acquisition['clips'].reverse()
                path.write_text(json.dumps(acquisition))
                with patch.object(F, 'PINNED_CLIPS', tuple(pins)), patch.object(F, 'DOCUMENTS', docs), self.assertRaises(ValueError):
                    F.prepare(root / 'out', path)

    def test_tampered_audio_or_labels_are_rejected(self):
        for mutation in ['audio', 'labels']:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                src, path, pins, docs = self.fixture(root)
                target = src / ('1.wav' if mutation == 'audio' else 'fleurs-dev.tsv')
                target.write_bytes(target.read_bytes() + b'!')
                with patch.object(F, 'PINNED_CLIPS', tuple(pins)), patch.object(F, 'DOCUMENTS', docs), self.assertRaises(ValueError):
                    F.prepare(root / 'out', path)

    def test_network_acquisition_keeps_exact_first_five_members(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src, _, pins, docs = self.fixture(root)
            archive = io.BytesIO()
            with tarfile.open(fileobj=archive, mode='w:gz') as tar:
                for filename, _ in pins:
                    data = (src / filename).read_bytes()
                    info = tarfile.TarInfo('dev/' + filename)
                    info.size = len(data)
                    tar.addfile(info, io.BytesIO(data))
                info = tarfile.TarInfo('dev/sixth.wav')
                info.size = 4
                tar.addfile(info, io.BytesIO(b'nope'))
            urls = {url: (src / name).read_bytes() for name, (url, _) in docs.items()}
            urls[F.ARCHIVE_URL] = archive.getvalue()
            with patch.object(F, 'PINNED_CLIPS', tuple(pins)), patch.object(F, 'DOCUMENTS', docs), patch.object(F, 'open_url', side_effect=lambda url: io.BytesIO(urls[url])):
                result = F.prepare(root / 'out')
            self.assertEqual(len(result['cases']), 5)
            self.assertFalse((root / 'out/raw/fleurs-wavs/sixth.wav').exists())
            self.assertFalse(result['acquisition']['archive']['complete_archive_downloaded'])


if __name__ == '__main__':
    unittest.main()
