"""FakeTTS artifacts validate provenance, never speaker playback or native TTS."""
from argparse import Namespace
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/synthesize_w16_feedback.py'
SPEC = importlib.util.spec_from_file_location('w16_feedback_artifacts', SCRIPT)
F = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(F)


class FakeTts:
    num_speakers = 174
    sample_rate = 8000

    def __init__(self):
        self.calls = []
        self.fail_at = None
        self.hook = None

    def synthesize(self, text, *, speaker_id, speed):
        self.calls.append((text, speaker_id, speed))
        if len(self.calls) == self.fail_at:
            raise RuntimeError('intentional generation failure')
        if self.hook:
            self.hook()
        return SimpleNamespace(samples=np.array([0., .1, -.1], dtype=np.float32), sample_rate=8000)


class FeedbackArtifactTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.run = self.root / 'run'
        (self.run / 'records').mkdir(parents=True)
        self.git = {'commit': 'b' * 40, 'status': ''}
        clips = [{'id': 'N01'}, {'id': 'A01'}]
        (self.run / 'protocol.json').write_text(json.dumps({'source_git': self.git, 'clips': clips}))
        (self.run / 'execution.json').write_text(json.dumps({'source_git': self.git, 'error': None}))
        self.rows = []
        for i, (clip, passed, message) in enumerate(zip(clips, [True, False], ['已完成，打开客厅灯。', '设备执行未确认完成。'])):
            row = {'id': clip['id'], 'clip': clip, 'completed': True, 'primary_semantics_passed': passed,
                   'primary': {'status': 'executed' if passed else 'device_error', 'error_code': None if passed else 'DEVICE_OFFLINE',
                               'console_feedback': [message]},
                   'reference_text_control': {'console_feedback': ['REFERENCE MUST NOT BE SYNTHESIZED']}}
            self.rows.append(row)
            (self.run / 'records' / f'{i:04d}.json').write_text(json.dumps(row))
        ledger = [v for i, row in enumerate(self.rows) for v in (
            {'event': 'clip_started', 'index': i, 'id': row['id']},
            {'event': 'clip_recorded', 'index': i, 'id': row['id'], 'completed': True})]
        (self.run / 'ledger.jsonl').write_text('\n'.join(json.dumps(v) for v in ledger) + '\n')
        self.index()
        self.model = self.root / 'model'
        self.model.mkdir()
        files = []
        for name in sorted(F.AUDIO.MODEL_FILES):
            path = self.model / name
            path.write_bytes(('fake model ' + name).encode())
            files.append(F.AUDIO.identity(path))
        self.inventory = self.root / 'inventory.json'
        self.inventory.write_text(json.dumps({'tts': {'model_dir': str(self.model), 'license': 'Apache-2.0',
                                                      'source_data_license': 'Apache-2.0', 'files': files,
                                                      **F.AUDIO.SETTINGS}}))
        self.args = Namespace(run=self.run, source_inventory=self.inventory, outdir=self.root / 'out')
        self.engine = FakeTts()
        self.factory = Mock(return_value=self.engine)
        self.patch_git = patch.object(F.AUDIO, 'git_info', return_value=self.git)
        self.patch_git.start()
        self.addCleanup(self.patch_git.stop)

    def index(self):
        files = [self.run / 'protocol.json', self.run / 'execution.json', self.run / 'ledger.jsonl',
                 *sorted((self.run / 'records').glob('*.json'))]
        (self.run / 'raw-index.json').write_text(json.dumps({'artifacts': [
            {'path': str(path.relative_to(self.run)), 'bytes': path.stat().st_size, 'sha256': F.AUDIO.sha(path)}
            for path in files]}))

    def generate(self):
        return F.synthesize(self.args, self.factory)

    def test_only_actual_primary_messages_are_synthesized_and_failure_stays_failure(self):
        result = self.generate()
        self.assertEqual(result['feedback_artifacts'], 2)
        self.assertEqual(self.engine.calls, [('已完成，打开客厅灯。', 13, 1.0), ('设备执行未确认完成。', 13, 1.0)])
        summary = F.AUDIO.read_json(self.args.outdir / 'summary.json')
        self.assertEqual(summary['kind'], 'fake_tts_feedback_fixture')
        self.assertFalse(summary['feedback'][1]['primary_semantics_passed'])
        self.assertEqual(summary['feedback'][1]['primary_error_code'], 'DEVICE_OFFLINE')
        self.assertFalse(summary['primary_latency_modified'])
        self.assertFalse(summary['source_primary_semantics_promoted'])
        self.assertEqual(summary['playback_calls'], 0)
        for artifact in summary['feedback']:
            self.assertEqual(F.AUDIO.identity(artifact['wav']['path']), artifact['wav'])
            np.testing.assert_array_equal(np.load(artifact['float_npy']['path'], allow_pickle=False), [0., np.float32(.1), np.float32(-.1)])

    def test_existing_output_is_rejected_before_source_read_or_model(self):
        self.args.outdir.mkdir()
        marker = self.args.outdir / 'preserve'
        marker.write_bytes(b'original')
        with patch.object(F, 'verified_feedback', side_effect=AssertionError('read attempted')), self.assertRaises(FileExistsError):
            self.generate()
        self.factory.assert_not_called()
        self.assertEqual(marker.read_bytes(), b'original')

    def test_changed_source_hash_or_different_runtime_is_rejected_before_model(self):
        path = self.run / 'records/0000.json'
        original = path.read_bytes()
        path.write_bytes(original + b'\n')
        with self.assertRaisesRegex(ValueError, 'raw hash'):
            self.generate()
        self.factory.assert_not_called()
        path.write_bytes(original)
        self.args.outdir = self.root / 'different-head'
        with patch.object(F.AUDIO, 'git_info', return_value={'commit': 'c' * 40, 'status': ''}), self.assertRaisesRegex(ValueError, 'same clean runtime'):
            self.generate()
        self.factory.assert_not_called()

    def test_empty_primary_feedback_cannot_borrow_reference_feedback(self):
        row = deepcopy(self.rows[0])
        row['primary']['console_feedback'] = []
        (self.run / 'records/0000.json').write_text(json.dumps(row))
        self.index()
        with self.assertRaisesRegex(ValueError, 'nonempty actual console feedback'):
            self.generate()
        self.factory.assert_not_called()

    def test_generation_error_preserves_partial_evidence_without_claiming_pass(self):
        self.engine.fail_at = 2
        with self.assertRaisesRegex(RuntimeError, 'intentional generation failure'):
            self.generate()
        summary = F.AUDIO.read_json(self.args.outdir / 'summary.json')
        self.assertFalse(summary['passed'])
        self.assertEqual(summary['synthesis_attempts'], 2)
        self.assertEqual(len(summary['feedback']), 1)
        self.assertTrue(Path(summary['feedback'][0]['wav']['path']).exists())

    def test_source_mutation_during_generation_is_rejected_and_retained(self):
        path = self.run / 'execution.json'
        self.engine.hook = lambda: path.write_text(path.read_text() + '\n')
        with self.assertRaisesRegex(ValueError, 'changed during feedback generation'):
            self.generate()
        summary = F.AUDIO.read_json(self.args.outdir / 'summary.json')
        self.assertFalse(summary['passed'])
        self.assertEqual(len(summary['feedback']), 2)
        self.assertEqual(summary['error']['type'], 'ValueError')


if __name__ == '__main__':
    unittest.main()
