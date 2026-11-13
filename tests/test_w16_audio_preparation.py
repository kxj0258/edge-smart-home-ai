"""Preparation tests use FakeTTS, never native synthesis or human audio."""
from __future__ import annotations

from argparse import Namespace
import hashlib
import importlib.util
import io
import json
from pathlib import Path
from shutil import copyfile
import struct
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import wave

import numpy as np

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/prepare_w16_audio.py'
SPEC = importlib.util.spec_from_file_location('w16_audio_preparation', SCRIPT)
P = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(P)


class FakeTts:
    num_speakers = 174
    sample_rate = 8000

    def __init__(self):
        self.calls = []
        self.samples = np.array([-.25, 0, .25], dtype=np.float32)
        self.hook = None

    def synthesize(self, text, *, speaker_id, speed):
        self.calls.append((text, speaker_id, speed))
        if self.hook:
            self.hook()
        return SimpleNamespace(samples=self.samples, sample_rate=self.sample_rate)


class PreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.model = self.root / 'model'
        self.model.mkdir()
        files = []
        for name in sorted(P.MODEL_FILES):
            path = self.model / name
            path.write_bytes(('fake model fixture: ' + name).encode())
            files.append(P.identity(path))
        self.inventory = self.root / 'inventory.json'
        self.inventory.write_text(json.dumps({'schema_version': 1, 'tts': {
            'model_dir': str(self.model), 'license': 'Apache-2.0', 'source_data_license': 'Apache-2.0',
            'source_repository': 'https://invalid.test/fixture', 'source_revision': 'a' * 40,
            'files': files, **P.SETTINGS}}))
        self.cases = self.root / 'cases.json'
        self.cases.write_text(json.dumps({'schema_version': 1, 'kind': 'w16_voice_home_cases', 'cases': [
            {'id': 'N01', 'reference_text': '打开客厅灯'},
            {'id': 'A01', 'reference_text': '打开客厅灯'},
            {'id': 'A02', 'reference_text': '', 'audio_kind': 'silence'}]}))
        self.args = Namespace(cases=self.cases, source_inventory=self.inventory, model_dir=None,
                              outdir=self.root / 'output')
        self.engine = FakeTts()
        self.factory = Mock(return_value=self.engine)
        self.git = patch.object(P, 'git_info', return_value={'commit': 'b' * 40, 'status': ''})
        self.git.start()
        self.addCleanup(self.git.stop)

    def prepare(self):
        return P.prepare(self.args, self.factory)

    def test_same_phrase_cases_are_generated_once_each_and_silence_is_distinct(self):
        result = self.prepare()
        self.assertEqual(result['synthesis_attempts'], 2)
        self.assertEqual(result['synthetic_signals'], 1)
        self.assertEqual(self.engine.calls, [('打开客厅灯', 13, 1.0), ('打开客厅灯', 13, 1.0)])
        manifest = P.read_json(self.args.outdir / 'manifest.json')
        self.assertEqual(manifest['preparation_kind'], 'fake_tts_unit_fixture')
        self.assertEqual([c['source_kind'] for c in manifest['clips']], ['synthetic_audio', 'synthetic_audio', 'synthetic_signal'])
        self.assertFalse(manifest['cross_generation_bitwise_determinism_claimed'])
        for clip in manifest['clips']:
            npy = np.load(clip['float_npy']['path'], allow_pickle=False)
            expected = self.engine.samples if clip['case_id'] != 'A02' else np.zeros(8000, dtype=np.float32)
            np.testing.assert_array_equal(npy, expected)
            self.assertEqual(P.sha(clip['wav']), clip['sha256'])
            self.assertFalse(clip['conversion']['resampled'])
        with wave.open(manifest['clips'][-1]['wav'], 'rb') as wav:
            self.assertEqual((wav.getframerate(), wav.getnframes()), (8000, 8000))
            self.assertEqual(wav.readframes(8000), b'\0' * 16000)

    def test_pcm16_quantization_has_explicit_ties_saturation_and_error(self):
        values = np.array([0., 1/65536, 3/65536, -1/65536, -3/65536, 1., -1., 2., -2.])
        original, data, info = P.quantize_pcm16(values, 8000)
        with wave.open(io.BytesIO(data), 'rb') as wav:
            self.assertEqual((wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getnframes()), (1, 2, 8000, 9))
            self.assertEqual(struct.unpack('<9h', wav.readframes(9)), (0, 0, 2, 0, -2, 32767, -32768, 32767, -32768))
        np.testing.assert_array_equal(original, values)
        self.assertEqual(info['clipped_sample_count'], 3)
        self.assertEqual(info['max_abs_quantization_error'], 1 + 1/32768)

    def test_nonfinite_wrong_shape_empty_integer_and_wrong_rate_are_rejected(self):
        for value in (np.array([np.nan]), np.array([np.inf]), np.zeros((2, 2)), [], [0, 1]):
            with self.subTest(value=repr(value)), self.assertRaises(ValueError):
                P.quantize_pcm16(value, 8000)
        for rate in (True, 0, 16000, 8000.0):
            with self.subTest(rate=rate), self.assertRaises(ValueError):
                P.quantize_pcm16([0.], rate)

    def test_existing_output_rejected_before_input_reads_or_factory(self):
        self.args.outdir.mkdir()
        marker = self.args.outdir / 'marker'
        marker.write_bytes(b'preserve')
        with patch.object(P, 'read_json', side_effect=AssertionError('input read')), self.assertRaises(FileExistsError):
            self.prepare()
        self.factory.assert_not_called()
        self.assertEqual(marker.read_bytes(), b'preserve')

    def test_changed_model_hash_prevents_tts_loading(self):
        (self.model / 'lexicon.txt').write_bytes(b'altered fixture')
        with self.assertRaisesRegex(ValueError, 'frozen TTS file differs'):
            self.prepare()
        self.factory.assert_not_called()
        self.assertFalse((self.args.outdir / 'manifest.json').exists())

    def test_missing_model_file_prevents_tts_loading(self):
        (self.model / 'number.fst').unlink()
        with self.assertRaises(FileNotFoundError):
            self.prepare()
        self.factory.assert_not_called()

    def test_model_override_requires_identical_files_and_is_recorded(self):
        other = self.root / 'copy'
        other.mkdir()
        for path in self.model.iterdir():
            copyfile(path, other / path.name)
        self.args.model_dir = other
        self.prepare()
        self.factory.assert_called_once_with(other.resolve())
        preparation = P.read_json(self.args.outdir / 'preparation.json')
        self.assertTrue(all(Path(v['path']).parent == other for v in preparation['model_files_before']))

    def test_changed_parameters_or_unreviewed_license_do_not_generate(self):
        for field, value in [('license', 'UNKNOWN'), ('source_data_license', 'UNKNOWN'), ('speaker_id', 0), ('provider', 'cuda')]:
            data = P.read_json(self.inventory)
            data['tts'][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                P.verify_model(data)
        self.factory.assert_not_called()

    def test_bad_case_identity_or_implicit_silence_is_not_guessed(self):
        for cases in ([{'id': '../escape', 'reference_text': '打开灯'}],
                      [{'id': 'A', 'reference_text': ''}],
                      [{'id': 'A', 'reference_text': '打开灯'}, {'id': 'A', 'reference_text': '关闭灯'}],
                      [{'id': 'A', 'reference_text': '打开灯', 'audio_kind': 'silence'}]):
            with self.subTest(cases=cases), self.assertRaises(ValueError):
                P.validate_cases({'schema_version': 1, 'kind': 'w16_voice_home_cases', 'cases': cases})

    def test_nonfinite_generation_preserves_failed_evidence_not_a_manifest(self):
        self.engine.samples = np.array([np.nan], dtype=np.float32)
        with self.assertRaisesRegex(ValueError, 'finite'):
            self.prepare()
        result = P.read_json(self.args.outdir / 'preparation.json')
        self.assertFalse(result['passed'])
        self.assertEqual(result['synthesis_attempts'], 1)
        self.assertEqual(result['clips'], [])
        self.assertFalse((self.args.outdir / 'manifest.json').exists())

    def test_source_file_mutation_during_generation_is_detected(self):
        self.engine.hook = lambda: self.cases.write_text(self.cases.read_text() + '\n')
        with self.assertRaisesRegex(ValueError, 'changed during preparation'):
            self.prepare()
        result = P.read_json(self.args.outdir / 'preparation.json')
        self.assertFalse(result['passed'])
        self.assertEqual(len(result['clips']), 3)
        self.assertFalse((self.args.outdir / 'manifest.json').exists())

    def test_dirty_native_execution_never_loads_the_model(self):
        with patch.object(P, 'git_info', return_value={'commit': 'b'*40, 'status': ' M source.py'}), patch.object(P, 'native_tts') as loader:
            with self.assertRaisesRegex(ValueError, 'clean committed'):
                P.prepare(self.args)
        loader.assert_not_called()


if __name__ == '__main__':
    unittest.main()
