from __future__ import annotations

from argparse import Namespace
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import wave

import numpy as np
from edge_smart_home.audio.interfaces import StreamingRecognition

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('evaluate_w16_repair', ROOT / 'scripts/evaluate_w16_repair.py')
E = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(E)
CASES = E.read(ROOT / 'configs/evaluation/w16-voice-home-v1.json')['cases']
CONFIG = {name: E.read(ROOT / 'configs' / (name + '.json')) for name in E.HOME.CONFIG_NAMES}


class FakeAsr:
    def __init__(self, texts, *, fail_at=None):
        self.texts, self.fail_at, self.calls = texts, fail_at, []

    def transcribe_waveform(self, samples, rate, chunk_ms):
        index = len(self.calls); self.calls.append((len(samples), rate, chunk_ms))
        if index == self.fail_at: raise RuntimeError('Fake decoder failure, not native')
        return StreamingRecognition(self.texts[index], len(samples) / rate, .001)


class W16RepairTests(unittest.TestCase):
    def make_protocol(self, root):
        root.mkdir(exist_ok=True)
        clips, homes, news_cases, news = [], [], [], []
        for case in CASES:
            wav = root / (case['id'] + '.wav')
            with wave.open(str(wav), 'wb') as out:
                out.setnchannels(1); out.setsampwidth(2); out.setframerate(8000); out.writeframes(b'\0\0' * 80)
            orig = root / (case['id'] + '-original.npy'); np.save(orig, np.zeros(80, np.float32))
            derived = root / (case['id'] + '-16k.npy'); np.save(derived, np.zeros(160, np.float32))
            clips.append({'id': case['id'], 'case_id': case['id'], 'audio_identity': E.ASR.file_identity(wav),
                          'float_npy': E.ASR.file_identity(orig)})
            homes.append({'id': case['id'], 'case_id': case['id'], 'original_wav_identity': E.ASR.file_identity(wav),
                          'original_float_identity': E.ASR.file_identity(orig), 'resampled_float_identity': E.ASR.file_identity(derived),
                          'source_sample_rate': 8000, 'resampled_sample_rate': 16000, 'frames_before': 80, 'frames_after': 160})
        for i in range(5):
            name = f'NEWS-{i}'; wav = root / (name + '.wav')
            with wave.open(str(wav), 'wb') as out:
                out.setnchannels(1); out.setsampwidth(2); out.setframerate(16000); out.writeframes(b'\0\0' * 160)
            down = root / (name + '-8k.npy'); np.save(down, np.zeros(80, np.float32))
            meta = {'id': name, 'source_url': 'urn:unit:fake-news', 'license': 'CC-BY-4.0', 'attribution': 'Fake unit fixture, not real speech'}
            news_cases.append({**meta, 'source_kind': 'human', 'expected': '新闻测试' + str(i), 'audio_identity': E.ASR.file_identity(wav)})
            news.append({**meta, 'source_kind': 'human_public_news', 'reference_text': '新闻测试' + str(i),
                         'original_wav_identity': E.ASR.file_identity(wav), 'downsampled_float_identity': E.ASR.file_identity(down),
                         'source_sample_rate': 16000, 'downsampled_sample_rate': 8000, 'frames_before': 160, 'frames_after': 80})
        placeholder = root / 'source-placeholder.json'; placeholder.write_text('{}\n'); identity = E.ASR.file_identity(placeholder)
        original = {'manifest': identity, 'clips': clips, 'cases': deepcopy(CASES), 'models': {},
                    'configs': deepcopy(CONFIG), 'hotwords': None, 'dependencies': {'unit': 'fake'}}
        news_protocol = {'protocol_identity': identity, 'cases': news_cases}
        material = {'schema_version': 1, 'kind': 'w16_repair_derived_audio_manifest', 'preparation_kind': 'deterministic_derivation',
                    'original_w16_manifest_identity': identity, 'original_w14_protocol_identity': identity,
                    'prepare_source_script': identity, 'derivation_environment': {'numpy': '2.2.6', 'scipy': '1.15.3', 'asr_environment_unchanged': True, 'unit': 'fake, no SciPy imported', 'environment_lock_identity': identity},
                    'source_git': {'commit': 'f' * 40, 'status': ''}, 'source_inputs_before': [identity], 'source_inputs_after': [identity],
                    'first_waveforms_preserved': True, 'reference_texts_changed': False, 'new_tts_generations': 0, 'no_requantization': True,
                    'resampler': {'library': 'scipy.signal.resample_poly', 'scipy_version': '1.15.3',
                                  'home_ratio': {'up': 2, 'down': 1}, 'news_ratio': {'up': 1, 'down': 2},
                                  'window': ['kaiser', 5.0], 'padtype': 'constant', 'cval': 0.0, 'output_dtype': 'float32', 'no_requantization': True,
                                  'gain_applied': False, 'trimmed': False, 'clipped': False},
                    'home': homes, 'news': news}
        files = [identity]
        for v in homes:
            files.extend(v[k] for k in ('original_wav_identity', 'original_float_identity', 'resampled_float_identity'))
        for v in news: files.extend(v[k] for k in ('original_wav_identity', 'downsampled_float_identity'))
        p = {'schema_version': 1, 'kind': 'w16_frozen_repair_protocol', 'state': 'frozen',
             'source_git': {'commit': 'f' * 40, 'status': ''}, 'sources_sha256': {'unit-only': 'f' * 64},
             'dependencies': {'unit': 'fake'}, 'matrix': E.matrix(), 'policy': E.policy(),
             'original_protocol': original, 'news_protocol': news_protocol, 'derived': material,
             'cases': deepcopy(CASES), 'configs': deepcopy(CONFIG), 'models': {}, 'hotwords': None,
             'frozen_files': files}
        protocol = root / 'protocol.json'; E.write(protocol, p); return protocol, p

    def run_fake(self, root, *, wrong_arm=None, fail_arm=None, fail_at=None, init_fail_arm=None, post_drift=None):
        protocol, p = self.make_protocol(root); instances = []
        def factory(runtime):
            arm = E.matrix()[len(instances)]['id']
            if arm in ('H16', 'H8'): texts = [c['expected'] for c in p['news_protocol']['cases']]
            else: texts = [c['reference_text'] for c in CASES]
            if arm == wrong_arm: texts[5] = '打开客厅灯'  # Unknown target misrecognized; no reference rescue.
            fake = FakeAsr(texts, fail_at=fail_at if arm == fail_arm else None); instances.append(fake)
            if arm == init_fail_arm: raise RuntimeError('deliberate model init failure')
            return fake
        original = E.validate_protocol
        with patch.object(E, 'validate_protocol', side_effect=lambda value, current=False: original(value, current=False)), \
             patch.object(E.DEV, 'git', side_effect=[p['source_git'], {'commit': 'e' * 40, 'status': ''}] if post_drift == 'git' else None, return_value=p['source_git']), \
             patch.object(E.DEV, 'sources', return_value={'changed': 'identity'} if post_drift == 'sources' else p['sources_sha256']), \
             patch.object(E.ASR, 'dependency_identity', return_value={'changed': 'native'} if post_drift == 'native' else p['dependencies']):
            summary = E.run(Namespace(protocol=protocol, outdir=root / 'run'), asr_factory=factory)
        return summary, p, instances

    def reindex(self, run):
        index = E.read(run / 'raw-index.json')
        for a in index['artifacts']:
            f = run / a['path']; a.update(bytes=f.stat().st_size, sha256=E.ASR.sha256(f))
        (run / 'raw-index.json').write_text(json.dumps(index))

    def test_predeclared_matrix_has_shared_A_and_exact_single_factors(self):
        arms = E.matrix(); baseline = arms[0]
        expected = {'Q': {'source'}, 'R': {'source', 'rate'}, 'T': {'tail_seconds'}, 'C': {'chunk_ms'}}
        for arm in arms[1:5]:
            self.assertEqual({k for k in baseline if baseline[k] != arm[k]} - {'id'}, expected[arm['id']])
        self.assertEqual([a['id'] for a in arms], ['A', 'Q', 'R', 'T', 'C', 'H16', 'H8'])
        self.assertFalse(E.policy()['automatic_product_promotion'])

    def test_protocol_rejects_matrix_policy_reference_and_model_changes(self):
        with TemporaryDirectory() as temp:
            _, p = self.make_protocol(Path(temp))
            for change in ('tail', 'order', 'retries', 'reference', 'model', 'config', 'hotwords', 'native'):
                q = deepcopy(p)
                if change == 'tail': q['matrix'][2]['tail_seconds'] = .66
                elif change == 'order': q['matrix'].reverse()
                elif change == 'retries': q['policy']['retries'] = 1
                elif change == 'reference': q['cases'][0]['reference_text'] = '换标签'
                elif change == 'model': q['models']['new'] = {}
                elif change == 'config': q['configs']['audio']['asr']['num_threads'] = 3
                elif change == 'hotwords': q['hotwords'] = {'different': 'words'}
                else: q['dependencies']['unit'] = 'swapped'
                with self.subTest(change=change), self.assertRaises(ValueError): E.validate_protocol(q)

    def test_formal_manifest_rejects_fake_and_changed_derivation_or_news_attribution(self):
        with TemporaryDirectory() as temp:
            _, p = self.make_protocol(Path(temp)); m = p['derived']
            E.validate_manifest(m, p['original_protocol'], p['news_protocol'], files=True)
            for change in ('fake', 'ratio', 'frame', 'license', 'news_reference', 'scipy', 'numpy'):
                q = deepcopy(m)
                if change == 'fake': q['preparation_kind'] = 'fake_unit_fixture'
                elif change == 'ratio': q['resampler']['home_ratio']['up'] = 3
                elif change == 'frame': q['home'][0]['frames_after'] += 1
                elif change == 'license': q['news'][0]['license'] = 'unverified'
                elif change == 'news_reference': q['news'][0]['reference_text'] = '换标签'
                elif change == 'scipy': q['resampler']['scipy_version'] = 'changed'
                else: q['derivation_environment']['numpy'] = 'changed'
                with self.subTest(change=change), self.assertRaises(ValueError): E.validate_manifest(q, p['original_protocol'], p['news_protocol'])

    def test_jobs_bind_first_pcm_vs_original_float_and_derived_float_separately(self):
        with TemporaryDirectory() as temp:
            _, p = self.make_protocol(Path(temp)); values = E.jobs(p)
            self.assertEqual(len(values), 60)
            self.assertEqual([values[i]['arm'] for i in (0, 10, 20, 30, 40, 50, 55)], ['A', 'Q', 'R', 'T', 'C', 'H16', 'H8'])
            self.assertEqual(values[10]['audio_identity'], p['derived']['home'][0]['original_float_identity'])
            self.assertEqual(values[20]['audio_identity'], p['derived']['home'][0]['resampled_float_identity'])
            self.assertEqual(values[55]['audio_identity'], p['derived']['news'][0]['downsampled_float_identity'])
            self.assertEqual(values[50]['source_kind'], 'human_public_news')

    def test_audio_reader_rejects_wrong_dtype_shape_nonfinite_range_rate_and_frames(self):
        with TemporaryDirectory() as temp:
            _, p = self.make_protocol(Path(temp)); job = E.jobs(p)[10]; target = Path(job['audio_identity']['path'])
            for value in (np.zeros(80, np.float64), np.zeros((80, 1), np.float32), np.full(80, np.nan, np.float32), np.full(80, 1.1, np.float32), np.zeros(79, np.float32)):
                np.save(target, value)
                with self.subTest(shape=value.shape, dtype=value.dtype), self.assertRaises(ValueError): E.load_audio(job)
            job = E.jobs(p)[0]; job['rate'] = 16000
            with self.assertRaises(ValueError): E.load_audio(job)

    def test_complete_fake_flow_is_sixty_attempts_plus_nine_independent_text_controls(self):
        with TemporaryDirectory() as temp:
            summary, p, instances = self.run_fake(Path(temp))
            self.assertTrue(summary['passed'], summary)
            self.assertEqual(summary['asr_attempts'], 60)
            self.assertEqual(summary['independent_text_oracle_matches'], 9)
            self.assertEqual([len(f.calls) for f in instances], [10, 10, 10, 10, 10, 5, 5])
            self.assertEqual(instances[2].calls[0], (160, 16000, 100))
            self.assertEqual(instances[4].calls[0], (80, 8000, 10000))
            self.assertEqual(summary['by_arm']['A']['home_semantics_passed'], 10)
            self.assertEqual(summary['by_arm']['A']['speech_scored'], 9)
            self.assertEqual(summary['by_arm']['H16']['home_semantics_passed'], 0)
            self.assertEqual(summary['execution_kind'], 'fake_asr_unit_fixture')
            self.assertFalse(summary['automatic_product_promotion'])
            self.assertFalse(summary['human_home_accepted'])
            self.assertFalse(summary['personal_understanding_accepted'])
            self.assertEqual(summary['network_attempts'], 0)
            ledger = (Path(temp) / 'run/ledger.jsonl').read_text().splitlines(); self.assertEqual(len(ledger), 138)

    def test_wrong_ASR_cannot_borrow_reference_or_safe_state_score(self):
        with TemporaryDirectory() as temp:
            summary, _, _ = self.run_fake(Path(temp), wrong_arm='R')
            self.assertTrue(summary['passed'])
            self.assertEqual(summary['independent_text_oracle_matches'], 9)
            self.assertEqual(summary['by_arm']['R']['home_semantics_passed'], 9)
            self.assertEqual(summary['by_arm']['R']['abnormal_state_preserved'], 4)
            self.assertFalse(summary['development_semantic_gate']['R'])
            self.assertTrue(summary['development_semantic_gate']['A'])
            self.assertFalse(summary['automatic_product_promotion'])

    def test_decoder_failure_retained_once_without_stopping_other_arms(self):
        with TemporaryDirectory() as temp:
            summary, _, instances = self.run_fake(Path(temp), fail_arm='Q', fail_at=2)
            self.assertFalse(summary['passed'])
            self.assertEqual(summary['asr_attempts'], 60)
            self.assertEqual(summary['by_arm']['Q']['completed'], 9)
            self.assertEqual(summary['by_arm']['R']['completed'], 10)
            self.assertEqual(len(instances[1].calls), 10)
            row = E.read(Path(temp) / 'run/records/0012.json')
            self.assertFalse(row['completed']); self.assertEqual(row['primary']['status'], 'asr_error')
            self.assertEqual(row['primary']['before'], row['primary']['after'])

    def test_model_init_failure_retains_arm_observations_and_continues(self):
        with TemporaryDirectory() as temp:
            summary, _, _ = self.run_fake(Path(temp), init_fail_arm='T')
            self.assertFalse(summary['passed'])
            self.assertEqual(summary['by_arm']['T']['completed'], 0)
            self.assertEqual(summary['by_arm']['C']['completed'], 10)
            self.assertEqual(summary['asr_attempts'], 50)
            self.assertIsNotNone(summary['model_loads'][3]['error'])

    def test_readonly_report_matches_bytes_and_never_invokes_native_or_control(self):
        with TemporaryDirectory() as temp:
            root = Path(temp); self.run_fake(root)
            with patch.object(E.ASR, 'native_asr', side_effect=AssertionError('no model')), patch.object(E.HOME, 'fixture', side_effect=AssertionError('no control')), patch.object(E.ASR, 'dependency_identity', side_effect=AssertionError('no native import')):
                summary = E.report(Namespace(run=root / 'run', outdir=root / 'report'))
            self.assertTrue(summary['passed'])
            for name in ('summary.json', 'report.md'):
                self.assertEqual((root / 'run' / name).read_bytes(), (root / 'report' / name).read_bytes())

    def test_reindexed_tampered_metrics_oracle_or_source_rejected(self):
        for change in ('metrics', 'oracle', 'source', 'news_control'):
            with self.subTest(change=change), TemporaryDirectory() as temp:
                root = Path(temp); self.run_fake(root)
                name = '0050.json' if change == 'news_control' else '0000.json'; f = root / 'run/records' / name; row = E.read(f)
                if change == 'metrics': row['text_metrics']['character_edits'] += 1
                elif change == 'oracle': row['primary']['checks']['status'] = False
                elif change == 'source': row['job']['source_kind'] = 'human'
                else: row['primary_semantics_passed'] = True
                f.write_text(json.dumps(row)); self.reindex(root / 'run')
                with self.assertRaises(ValueError): E.report(Namespace(run=root / 'run', outdir=root / 'report'))

    def test_missing_ledger_or_index_entry_and_reordered_ledger_rejected(self):
        for change in ('missing_ledger', 'index_entry', 'ledger_order'):
            with self.subTest(change=change), TemporaryDirectory() as temp:
                root = Path(temp); self.run_fake(root); run = root / 'run'
                if change == 'missing_ledger': (run / 'ledger.jsonl').unlink()
                elif change == 'index_entry':
                    data = E.read(run / 'raw-index.json'); data['artifacts'].pop(); (run / 'raw-index.json').write_text(json.dumps(data))
                else:
                    f = run / 'ledger.jsonl'; lines = f.read_text().splitlines(); lines[0], lines[1] = lines[1], lines[0]; f.write_text('\n'.join(lines) + '\n'); self.reindex(run)
                with self.assertRaises((ValueError, FileNotFoundError)): E.report(Namespace(run=run, outdir=root / 'report'))

    def test_tampered_summary_is_rejected_instead_of_trusted(self):
        with TemporaryDirectory() as temp:
            root = Path(temp); self.run_fake(root); f = root / 'run/summary.json'; data = E.read(f); data['automatic_product_promotion'] = True; f.write_text(json.dumps(data))
            with self.assertRaises(ValueError): E.report(Namespace(run=root / 'run', outdir=root / 'report'))

    def test_runtime_source_or_native_drift_fails_integrity_after_all_observations(self):
        for drift in ('git', 'sources', 'native'):
            with self.subTest(drift=drift), TemporaryDirectory() as temp:
                summary, _, _ = self.run_fake(Path(temp), post_drift=drift)
                self.assertFalse(summary['passed'])
                self.assertFalse(summary['engineering_gates']['inputs_unchanged'])
                self.assertEqual(summary['asr_attempts'], 60)
                self.assertEqual(summary['by_arm']['A']['completed'], 10)

    def test_current_identity_validation_rejects_changed_input_without_native_inference(self):
        with TemporaryDirectory() as temp:
            _, p = self.make_protocol(Path(temp))
            with patch.object(E.DEV, 'git', return_value=p['source_git']), patch.object(E.DEV, 'sources', return_value=p['sources_sha256']), patch.object(E.ASR, 'dependency_identity', return_value=p['dependencies']):
                E.validate_protocol(p, current=True)
                Path(p['frozen_files'][0]['path']).write_text('changed')
                with self.assertRaises(ValueError): E.validate_protocol(p, current=True)

    def test_dirty_prepare_is_rejected_before_material_reads(self):
        with TemporaryDirectory() as temp:
            with patch.object(E.DEV, 'git', return_value={'commit': 'f' * 40, 'status': ' M changed'}), patch.object(E, 'read', side_effect=AssertionError('must not read')):
                with self.assertRaises(ValueError): E.prepare(Namespace(outdir=Path(temp) / 'new'))

    def test_report_rejects_missing_completed_output_or_negative_timing(self):
        for change in ('output', 'attempt', 'timing', 'duration'):
            with self.subTest(change=change), TemporaryDirectory() as temp:
                root = Path(temp); self.run_fake(root); f = root / 'run/records/0000.json'; row = E.read(f)
                if change == 'output':
                    row['recognizer_output'] = None; row['text_metrics'] = None; row['voice_normalized'] = None; row['asr_metrics'] = None
                elif change == 'attempt': row['asr_attempted'] = False
                elif change == 'timing': row['timing']['asr_replay_wall_ms'] = -1
                else: row['asr_metrics']['audio_seconds'] = 999
                f.write_text(json.dumps(row)); self.reindex(root / 'run')
                with self.assertRaises(ValueError): E.report(Namespace(run=root / 'run', outdir=root / 'report'))

    def test_existing_output_rejected_before_inputs_or_factories(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            with patch.object(E, 'read', side_effect=AssertionError('must not read')), patch.object(E.ASR, 'native_asr', side_effect=AssertionError('must not load')):
                for fn, args in [(E.run, Namespace(protocol=root / 'absent', outdir=root)),
                                 (E.report, Namespace(run=root / 'absent', outdir=root)),
                                 (E.prepare, Namespace(outdir=root))]:
                    with self.subTest(operation=fn.__name__), self.assertRaises(FileExistsError): fn(args)


if __name__ == '__main__': unittest.main()
