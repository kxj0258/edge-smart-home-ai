"""Artificial worker fixtures only; no model, FAR compiler or human audio."""
from argparse import Namespace
from copy import deepcopy
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from scripts import w16_context_d_worker as W


class FakeTts:
    sample_rate = 8000
    num_speakers = 174

    def __init__(self, *, fail_at=None, missing_step=False, changed_prefix=False,
                 hook=None, dtype=np.float32, dropped_surface=False, oov=False):
        self.calls = []
        self.fail_at = fail_at
        self.missing_step = missing_step
        self.changed_prefix = changed_prefix
        self.hook = hook
        self.dtype = dtype
        self.dropped_surface = dropped_surface
        self.oov = oov

    def synthesize(self, text, *, speaker_id, speed):
        i = len(self.calls)
        self.calls.append((text, speaker_id, speed))
        os.write(2, ('[fake] Raw text: ' + text + '\n').encode())
        if self.hook:
            self.hook(i)
        if i == self.fail_at:
            raise RuntimeError('artificial generation failure; no retry')
        for step in range(48 if self.missing_step else 49):
            normalized = 'wrong-prefix' if self.changed_prefix and step == 17 else text
            os.write(2, ('[fake] After normalizing: ' + normalized + '\n').encode())
        words = text[:-1] if self.dropped_surface else text
        os.write(2, ('[fake] Input text in string: ' + text
                     + '\n[fake] After splitting to words: ' + ' '.join(words) + '\n').encode())
        if self.oov:
            os.write(2, b'[fake] OOV artificial-word\n')
        return SimpleNamespace(samples=np.array([0, .125, -.25, .1], dtype=self.dtype),
                               sample_rate=self.sample_rate)


class ContextWorkerTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        model = self.root / 'model'
        model.mkdir()
        for name in W.MODEL_FILES:
            (model / name).write_text('artificial model resource; never loaded')
        old = self.root / 'old-run'
        old.mkdir()
        bundle = self.root / 'bundle'
        bundle.mkdir()
        (bundle / 'manifest.json').write_text('{}')
        sidecar = bundle / 'sidecar.txt'
        sidecar.write_text('调到 t iao2 #0 d ao4 #0\n')
        far = bundle / 'context.far'
        far.write_bytes(b'not-a-real-FAR; fake factory only')
        self.sentinel = self.root / 'immutable-input'
        self.sentinel.write_text('unchanged')
        cases = [{'id': f'FAKE-{i:02d}', 'reference_text': f'人工构造口令{i}',
                  'source_kind': 'unit_fixture'} for i in range(18)]
        controls, static = [], []
        for case in cases:
            p = old / (case['id'] + '.json')
            W.write(p, {'id': case['id'], 'arm': 'B',
                        'native_trace': {'normalization_steps': [case['reference_text']] * 48}})
            controls.append(W.identity(p))
            static.append({'id': case['id'], 'output_normalized': case['reference_text']})
        self.p = {'model_dir': str(model), 'original_run': str(old),
                  'bundle_manifest': W.identity(bundle / 'manifest.json'),
                  'resources': {'sidecar_lexicon': W.identity(sidecar), 'context_far': W.identity(far)},
                  'settings': deepcopy(W.EXPECTED), 'cases': cases,
                  'control_records': controls, 'static_cases': static,
                  'model_files': [W.identity(model / n) for n in sorted(W.MODEL_FILES)],
                  'frozen_inputs': [W.identity(self.sentinel), W.identity(sidecar), W.identity(far), *controls],
                  'source_git': {'commit': 'f' * 40, 'status': ''},
                  'sources_sha256': {}, 'dependencies': {}}
        self.protocol = self.root / 'protocol.json'
        W.write(self.protocol, self.p)
        # Coordinator tests exercise the complete immutable schema. Worker tests
        # isolate execution/failure recording with these explicitly fake inputs.
        validator = patch.object(W, 'validate_protocol', return_value=None)
        self.validator = validator.start()
        self.addCleanup(validator.stop)

    def args(self, name='run'):
        return Namespace(protocol=self.protocol, arm='D', outdir=self.root / name)

    def run_fake(self, **kw):
        engine = FakeTts(**kw)
        factory = Mock(return_value=engine)
        result = W.run(self.args(), tts_factory=factory)
        return result, engine, factory

    def test_one_model_eighteen_once_with_sidecar_and_ordered_fars(self):
        result, engine, factory = self.run_fake()
        self.assertTrue(result['passed'], result)
        self.assertEqual(result['execution_kind'], 'fake_tts_unit_fixture')
        self.assertEqual(factory.call_count, 1)
        self.assertEqual(engine.calls, [(c['reference_text'], 13, 1.0) for c in self.p['cases']])
        options = factory.call_args.kwargs
        self.assertEqual(options['lexicon_path'], Path(self.p['resources']['sidecar_lexicon']['path']))
        self.assertEqual(options['rule_far_paths'], (Path(self.p['model_dir']) / 'rule.far',
                                                    Path(self.p['resources']['context_far']['path'])))
        self.assertEqual((options['noise_scale'], options['noise_scale_w']), (0.0, 0.0))
        self.assertEqual((result['generation_attempts'], result['completed']), (18, 18))
        self.assertEqual(len((self.root / 'run/ledger.jsonl').read_text().splitlines()), 36)
        for i, case in enumerate(self.p['cases']):
            row = W.read(self.root / f'run/records/{i:04d}.json')
            self.assertTrue(row['completed'])
            self.assertEqual(row['case'], case)
            self.assertEqual(len(row['native_trace']['normalization_steps']), 49)
            self.assertIsNotNone(row['source_float'])
            self.assertIsNotNone(row['pcm16_wav'])
        for key in ['asr_calls', 'control_calls', 'playback_calls', 'microphone_calls', 'retries']:
            self.assertEqual(result[key], 0)
        self.assertFalse(result['human_listening_accepted'])
        self.assertFalse(result['acoustic_pronunciation_accepted'])

    def test_generation_failure_is_retained_without_retry_or_sample_omission(self):
        result, engine, _ = self.run_fake(fail_at=3)
        self.assertFalse(result['passed'])
        self.assertEqual((len(engine.calls), result['generation_attempts'], result['completed']), (18, 18, 17))
        failed = W.read(self.root / 'run/records/0003.json')
        self.assertTrue(failed['generation_attempted'])
        self.assertIsNone(failed['pcm16_wav'])
        self.assertIn('artificial generation failure', failed['error']['detail'])
        self.assertTrue(W.read(self.root / 'run/records/0017.json')['completed'])

    def test_48_step_trace_cannot_pass_as_the_expected_49(self):
        result, engine, _ = self.run_fake(missing_step=True)
        self.assertFalse(result['passed'])
        self.assertEqual(len(engine.calls), 1)
        self.assertEqual(result['generation_attempts'], 1)
        self.assertEqual(result['aborted_reason']['index'], 0)
        row = W.read(self.root / 'run/records/0000.json')
        self.assertTrue(row['generated_audio_saved'])
        self.assertFalse(row['completed'])
        self.assertEqual(row['native_trace']['expected_normalization_steps'], 49)
        self.assertEqual(len(list((self.root / 'run/records').glob('*.json'))), 18)
        for i in range(1, 18):
            remaining = W.read(self.root / f'run/records/{i:04d}.json')
            self.assertFalse(remaining['generation_attempted'])
            self.assertIsNone(remaining['pcm16_wav'])
            self.assertIn('Not attempted after fatal', remaining['error']['detail'])

    def test_first_48_steps_must_preserve_every_archived_B_step(self):
        result, engine, _ = self.run_fake(changed_prefix=True)
        self.assertFalse(result['passed'])
        self.assertEqual(len(engine.calls), 1)
        row = W.read(self.root / 'run/records/0000.json')
        self.assertTrue(row['native_trace']['consistent'])
        self.assertFalse(row['completed'])
        self.assertIn('normalization order/output differs', row['error']['detail'])

    def test_dropped_native_character_stops_without_replacing_the_label(self):
        result, engine, _ = self.run_fake(dropped_surface=True)
        self.assertFalse(result['passed'])
        self.assertEqual(len(engine.calls), 1)
        row = W.read(self.root / 'run/records/0000.json')
        self.assertEqual(row['case'], self.p['cases'][0])
        self.assertTrue(row['generated_audio_saved'])
        self.assertEqual(row['error']['type'], 'NativeConfigurationError')
        self.assertIn('surface dropped', row['error']['detail'])

    def test_oov_is_fatal_and_keeps_the_first_failed_waveform(self):
        result, engine, _ = self.run_fake(oov=True)
        self.assertFalse(result['passed'])
        self.assertEqual(len(engine.calls), 1)
        row = W.read(self.root / 'run/records/0000.json')
        self.assertTrue(row['native_trace']['oov'])
        self.assertIsNotNone(row['pcm16_wav'])
        self.assertEqual(result['aborted_reason']['type'], 'NativeConfigurationError')

    def test_model_initialization_failure_keeps_all_unattempted_slots(self):
        factory = Mock(side_effect=RuntimeError('fake initialization error'))
        result = W.run(self.args(), tts_factory=factory)
        self.assertFalse(result['passed'])
        self.assertEqual(factory.call_count, 1)
        self.assertEqual(result['generation_attempts'], 0)
        self.assertEqual(len(list((self.root / 'run/records').glob('*.json'))), 18)
        self.assertFalse(W.read(self.root / 'run/records/0000.json')['generation_attempted'])

    def test_frozen_input_mutation_cannot_be_reported_preserved(self):
        result, engine, _ = self.run_fake(hook=lambda i: self.sentinel.write_text('mutated') if i == 0 else None)
        self.assertFalse(result['passed'])
        self.assertFalse(result['frozen_inputs_and_runtime_preserved'])
        self.assertEqual(len(engine.calls), 18)

    def test_non_float32_audio_is_rejected_without_claiming_saved_success(self):
        result, _, _ = self.run_fake(dtype=np.float64)
        self.assertFalse(result['passed'])
        row = W.read(self.root / 'run/records/0000.json')
        self.assertFalse(row['generated_audio_saved'])
        self.assertIn('float32', row['error']['detail'])

    def test_existing_output_rejects_before_read_validation_or_model(self):
        with patch.object(W, 'read', side_effect=AssertionError('must not read')), \
             patch.object(W, 'native_factory', side_effect=AssertionError('must not construct')):
            with self.assertRaises(FileExistsError):
                W.run(Namespace(protocol=self.root / 'missing', arm='D', outdir=self.root))
        self.validator.assert_not_called()


if __name__ == '__main__':
    unittest.main()
