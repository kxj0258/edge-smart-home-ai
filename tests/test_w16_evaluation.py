from __future__ import annotations

from argparse import Namespace
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import wave

from edge_smart_home.audio.interfaces import StreamingRecognition

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('evaluate_w16_voice_home', ROOT / 'scripts/evaluate_w16_voice_home.py')
W16 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(W16)
CASES = W16.read(ROOT / 'configs/evaluation/w16-voice-home-v1.json')
CONFIG = {name: W16.read(ROOT / 'configs' / (name + '.json')) for name in W16.CONFIG_NAMES}


class FakeAsr:
    def __init__(self, texts, fail_at=None):
        self.texts, self.calls, self.fail_at = texts, 0, fail_at

    def transcribe_waveform(self, samples, rate, chunk_duration_ms):
        index = self.calls
        self.calls += 1
        if index == self.fail_at:
            raise RuntimeError('deliberate Mock decoder failure; not native ASR')
        return StreamingRecognition(self.texts[index], len(samples) / rate, .001)


class W16EvaluationTests(unittest.TestCase):
    def test_five_normal_five_abnormal_are_fixed_before_recognition(self):
        W16.validate_cases(CASES)
        self.assertEqual(len(CASES['cases']), 10)
        self.assertEqual([c['category'] for c in CASES['cases']], ['normal'] * 5 + ['abnormal'] * 5)
        self.assertEqual(CASES['cases'][4]['setup'][0]['command']['action'], 'turn_on')
        self.assertEqual(CASES['cases'][-1]['audio_kind'], 'silence')

    def test_bad_case_counts_ids_and_empty_non_silence_are_rejected(self):
        for kind in ('duplicate', 'category_count', 'missing_oracle', 'empty_speech'):
            changed = deepcopy(CASES)
            if kind == 'duplicate':
                changed['cases'][1]['id'] = changed['cases'][0]['id']
            elif kind == 'category_count':
                changed['cases'][0]['category'] = 'abnormal'
            elif kind == 'missing_oracle':
                del changed['cases'][0]['expect']['error_code']
            else:
                changed['cases'][0]['reference_text'] = ''
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                W16.validate_cases(changed)

    def fixture_manifest(self, directory):
        directory.mkdir()
        clips = []
        for case in CASES['cases']:
            path = directory / (case['id'] + '.wav')
            with wave.open(str(path), 'wb') as wav:
                wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(8000)
                wav.writeframes(b'\0\0' * 400)
            clips.append({'case_id': case['id'], 'wav': str(path), 'sha256': W16.ASR.sha256(path),
                          'source_kind': 'synthetic_signal' if case.get('audio_kind') == 'silence' else 'synthetic_audio',
                          'source_url': 'urn:unit:fake-audio-not-native-tts', 'license': 'unit-fixture',
                          'attribution': 'unit test only', 'provenance_note': 'Fake wave bytes; never formal audio evidence'})
        return {'schema_version': 1, 'kind': 'w16_audio_manifest', 'preparation_kind': 'fake_tts_unit_fixture', 'clips': clips}

    def test_manifest_rejects_transcript_and_missing_provenance_or_coverage(self):
        with TemporaryDirectory() as temp:
            root = Path(temp); manifest = self.fixture_manifest(root / 'waves')
            for kind in ('transcript', 'license', 'coverage', 'signal_as_human', 'hash'):
                changed = deepcopy(manifest)
                if kind == 'transcript': changed['clips'][0]['source_kind'] = 'synthetic_transcript'
                elif kind == 'license': changed['clips'][0]['license'] = ''
                elif kind == 'coverage': changed['clips'].pop()
                elif kind == 'signal_as_human': changed['clips'][-1]['source_kind'] = 'human'
                else: changed['clips'][0]['sha256'] = '0' * 64
                with self.subTest(kind=kind), self.assertRaises(ValueError):
                    W16.manifest_clips(changed, root / 'manifest.json', CASES['cases'])

    def test_same_audio_can_test_distinct_offline_conditions_without_inventing_unique_speech(self):
        with TemporaryDirectory() as temp:
            root = Path(temp); manifest = self.fixture_manifest(root / 'waves')
            clips = W16.manifest_clips(manifest, root / 'manifest.json', CASES['cases'])
            self.assertEqual(len(clips), 10)
            self.assertEqual(len({c['sha256'] for c in clips}), 1)
            self.assertEqual(clips[-1]['source_kind'], 'synthetic_signal')

    def test_independent_reference_text_uses_existing_text_entry_and_oracles(self):
        for case in CASES['cases']:
            with self.subTest(case=case['id']):
                result = W16.reference_probe(case, CONFIG)
                if case['reference_text']:
                    self.assertTrue(all(result['checks'].values()), result)
                    self.assertEqual(result['input_kind'], 'authored_reference_text_on_independent_mock')
                else:
                    self.assertIsNone(result)

    def make_protocol(self, root):
        manifest = self.fixture_manifest(root / 'waves')
        clips = W16.manifest_clips(manifest, root / 'manifest.json', CASES['cases'])
        p = {'schema_version': 1, 'kind': 'w16_frozen_voice_home_protocol', 'state': 'frozen',
             'source_git': {'commit': 'f' * 40, 'status': ''},
             'sources_sha256': {'scripts/evaluate_w16_voice_home.py': W16.ASR.sha256(ROOT / 'scripts/evaluate_w16_voice_home.py')},
             'dependencies': {'unit': 'fake-no-native'}, 'cases': deepcopy(CASES['cases']), 'clips': clips,
             'configs': CONFIG, 'policy': W16.policy(), 'backend_kind': 'mock',
             'audio_preparation_kind': 'native_tts_audio', 'asr_weight_license_verified': False,
             'limitations': W16.LIMITATIONS}
        path = root / 'protocol.json'; W16.write(path, p)
        return path, p

    def run_fake(self, root, *, texts=None, fail_at=None):
        protocol, p = self.make_protocol(root)
        fake = FakeAsr(texts or [c['reference_text'] for c in CASES['cases']], fail_at)
        original = W16.validate_protocol
        # Only external clean Git/native dependency hashes are mocked; all
        # product Voice, intent, Mock control and state checks execute for real.
        with patch.object(W16, 'validate_protocol', side_effect=lambda value, current=False: original(value, False)):
            summary = W16.run(Namespace(protocol=protocol, outdir=root / 'run'), asr_factory=lambda value: fake)
        return summary, p, fake

    def test_complete_fake_asr_unit_loop_and_separate_reference_controls(self):
        with TemporaryDirectory() as temp:
            root = Path(temp); summary, p, fake = self.run_fake(root)
            self.assertTrue(summary['passed'], summary)
            self.assertTrue(summary['primary_all_semantics_passed'])
            self.assertTrue(summary['abnormal_primary_state_preserved'])
            self.assertEqual(summary['reference_text_oracle_matches'], 9)
            self.assertEqual(fake.calls, 10)
            self.assertEqual(summary['overall']['speech_scored'], 9)
            self.assertEqual(summary['overall']['speech_recognizer_exact'], 9)
            self.assertEqual(summary['by_source_kind']['human']['observations'], 0)
            self.assertEqual(summary['network_attempts'], 0)
            self.assertIsNone(summary['live_e2e_ms'])
            for name in W16.TIMING_KEYS:
                self.assertEqual(summary['overall']['latency_ms'][name]['n'], 10)

    def test_wrong_asr_primary_cannot_borrow_reference_score(self):
        with TemporaryDirectory() as temp:
            texts = [c['reference_text'] for c in CASES['cases']]
            texts[5] = '打开客厅灯'  # Unknown spoken target was wrongly recognized.
            summary, _, _ = self.run_fake(Path(temp), texts=texts)
            self.assertTrue(summary['passed'])  # Evidence integrity, not semantic acceptance.
            self.assertFalse(summary['primary_all_semantics_passed'])
            self.assertFalse(summary['abnormal_primary_state_preserved'])
            self.assertEqual(summary['reference_text_oracle_matches'], 9)
            row = W16.read(Path(temp) / 'run/records/0005.json')
            self.assertEqual(row['primary']['status'], 'executed')
            self.assertEqual(row['reference_text_control']['status'], 'request_rejected')
            self.assertIn('semantic:AUDIO_TO_STATE_ORACLE_MISMATCH', summary['overall']['error_counts'])

    def test_decode_failure_is_saved_without_retry_and_without_device_effect(self):
        with TemporaryDirectory() as temp:
            root = Path(temp); summary, _, fake = self.run_fake(root, fail_at=2)
            self.assertFalse(summary['passed'])
            self.assertEqual(fake.calls, 10)
            row = W16.read(root / 'run/records/0002.json')
            self.assertFalse(row['completed'])
            self.assertEqual(row['primary']['error_code'], 'ASR_DECODE_FAILED')
            self.assertEqual(row['primary']['before'], row['primary']['after'])
            self.assertTrue(row['primary']['text_fallback'])

    def test_reference_failure_preserves_received_asr_and_fails_coverage(self):
        with TemporaryDirectory() as temp, patch.object(W16, 'reference_probe', side_effect=RuntimeError('intentional reference failure')):
            root = Path(temp); summary, _, fake = self.run_fake(root)
            self.assertFalse(summary['passed'])
            self.assertEqual(fake.calls, 10)
            row = W16.read(root / 'run/records/0000.json')
            self.assertTrue(row['completed'])
            self.assertEqual(row['recognizer_output'], CASES['cases'][0]['reference_text'])
            self.assertEqual(row['primary']['status'], 'executed')
            self.assertEqual(row['errors'][0]['code'], 'REFERENCE_TEXT_CONTROL_FAILED')

    def test_model_load_failure_retains_empty_ledger_and_rebuildable_report(self):
        with TemporaryDirectory() as temp:
            root = Path(temp); path, p = self.make_protocol(root)
            original = W16.validate_protocol
            with patch.object(W16, 'validate_protocol', side_effect=lambda value, current=False: original(value, False)):
                result = W16.run(Namespace(protocol=path, outdir=root / 'run'),
                                 asr_factory=lambda value: (_ for _ in ()).throw(RuntimeError('unit load failure')))
            self.assertFalse(result['passed'])
            self.assertEqual((root / 'run/ledger.jsonl').read_text(), '')
            error = W16.read(root / 'run/execution.json')['error']
            self.assertEqual(error['stage'], 'asr_initialization')
            self.assertEqual(error['code'], 'ASR_INITIALIZATION_FAILED')
            rebuilt = W16.report(Namespace(run=root / 'run', outdir=root / 'rebuilt'))
            self.assertEqual(result, rebuilt)

    def test_report_rebuilds_without_importing_native_or_running_control(self):
        with TemporaryDirectory() as temp:
            root = Path(temp); summary, _, _ = self.run_fake(root)
            with patch.object(W16.ASR, 'dependency_identity', side_effect=AssertionError('native forbidden')), \
                 patch.object(W16, 'fixture', side_effect=AssertionError('control forbidden')):
                rebuilt = W16.report(Namespace(run=root / 'run', outdir=root / 'rebuilt'))
            self.assertEqual(summary, rebuilt)
            for name in ('summary.json', 'report.md'):
                self.assertEqual((root / 'run' / name).read_bytes(), (root / 'rebuilt' / name).read_bytes())

    def reindex(self, directory):
        path = directory / 'raw-index.json'; index = W16.read(path)
        for a in index['artifacts']:
            f = directory / a['path']; a.update(bytes=f.stat().st_size, sha256=W16.ASR.sha256(f))
        path.write_text(json.dumps(index))

    def test_reindexed_metric_source_and_state_oracle_tampering_is_rejected(self):
        for kind in ('metric', 'source', 'primary_score', 'readback'):
            with self.subTest(kind=kind), TemporaryDirectory() as temp:
                root = Path(temp); self.run_fake(root)
                path = root / 'run/records/0000.json'; row = W16.read(path)
                if kind == 'metric': row['text_metrics']['character_edits'] = 5
                elif kind == 'source': row['source_kind'] = 'human'
                elif kind == 'primary_score': row['primary_semantics_passed'] = False
                else: row['primary']['execution_records'][0]['readback']['attributes']['power'] = False
                path.write_text(json.dumps(row)); self.reindex(root / 'run')
                with self.assertRaises(ValueError):
                    W16.report(Namespace(run=root / 'run', outdir=root / 'bad-report'))

    def test_reindexed_ledger_order_tampering_is_rejected(self):
        with TemporaryDirectory() as temp:
            root = Path(temp); self.run_fake(root)
            ledger = root / 'run/ledger.jsonl'; lines = ledger.read_text().splitlines()
            ledger.write_text('\n'.join(reversed(lines)) + '\n'); self.reindex(root / 'run')
            with self.assertRaises(ValueError):
                W16.report(Namespace(run=root / 'run', outdir=root / 'bad-report'))

    def test_existing_output_is_rejected_before_protocol_read_or_asr(self):
        with TemporaryDirectory() as temp:
            root = Path(temp); out = root / 'existing'; out.mkdir(); marker = out / 'marker'; marker.write_bytes(b'keep')
            digest = hashlib.sha256(marker.read_bytes()).hexdigest()
            with self.assertRaises(FileExistsError):
                W16.run(Namespace(protocol=root / 'missing.json', outdir=out), asr_factory=lambda value: self.fail('ASR must not run'))
            self.assertEqual(hashlib.sha256(marker.read_bytes()).hexdigest(), digest)

    def test_fake_audio_preparation_cannot_be_promoted_to_formal_evidence(self):
        with TemporaryDirectory() as temp:
            root = Path(temp); manifest = self.fixture_manifest(root / 'waves'); path = root / 'manifest.json'; W16.write(path, manifest)
            with patch.object(W16.DEV, 'git', return_value={'commit': 'f' * 40, 'status': ''}), self.assertRaisesRegex(ValueError, 'native TTS'):
                W16.prepare(Namespace(outdir=root / 'out', cases=ROOT / 'configs/evaluation/w16-voice-home-v1.json', manifest=path))


if __name__ == '__main__':
    unittest.main()
