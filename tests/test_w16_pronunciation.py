"""Fake-only orchestration checks; no native TTS/ASR or audio playback."""
from __future__ import annotations

from argparse import Namespace
from contextlib import ExitStack
from copy import deepcopy
import csv
import importlib.util
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from edge_smart_home.audio.interfaces import StreamingRecognition
from scripts import w16_tts_pronunciation_worker as WORKER

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('w16_pronunciation_coordinator', ROOT / 'scripts/evaluate_w16_pronunciation.py')
E = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(E)
DATA = E.read(ROOT / 'configs/w16-pronunciation-cases-v1.json')
CONFIG = {name: E.read(ROOT / 'configs' / (name + '.json')) for name in E.HOME.CONFIG_NAMES}


class FakeAsr:
    def __init__(self, texts, fail_at=None):
        self.texts, self.calls, self.fail_at = texts, [], fail_at

    def transcribe_waveform(self, samples, rate, chunk_ms):
        index = len(self.calls); self.calls.append((len(samples), rate, chunk_ms))
        if index == self.fail_at: raise RuntimeError('Injected Fake decoder error, not native ASR')
        return StreamingRecognition(self.texts[index], len(samples) / rate, .001)


class PronunciationCoordinatorTests(unittest.TestCase):
    def make_protocol(self, root):
        model = root / 'fake-model'; model.mkdir()
        cases = deepcopy(DATA['home_cases'] + DATA['pronunciation_probes'])
        words = {ch: ['z1', '#0'] for c in cases for ch in c['reference_text']}
        for case in cases:
            for focal in case['focal_pronunciation']:
                syllables = []; pending = []
                for token in focal['expected_phones']:
                    pending.append(token)
                    if token == '#0': syllables.append(pending); pending = []
                for char, phones in zip(focal['text'], syllables): words[char] = phones
                words[focal['text']] = focal['expected_phones']
        # Deliberate ungrouped alternatives, not the canonical lexical targets.
        words['调'] = ['d', 'iao4', '#0']; words['重'] = ['zh', 'ong4', '#0']; words['乐'] = ['l', 'e4', '#0']
        (model / 'lexicon.txt').write_text('\n'.join(w + ' ' + ' '.join(v) for w, v in sorted(words.items())) + '\n')
        alphabet = sorted({p for v in words.values() for p in v} | {'sil', 'eos'})
        (model / 'tokens.txt').write_text('\n'.join(f'{p} {i}' for i, p in enumerate(alphabet)) + '\n')
        for name in WORKER.MODEL_FILES:
            if not (model / name).exists(): (model / name).write_bytes(b'Fake unit resource; never a real model')
        placeholder = root / 'source.json'; placeholder.write_text('{}\n')
        item = E.ASR.file_identity(placeholder)
        original = {'configs': deepcopy(CONFIG), 'models': {}, 'hotwords': None, 'dependencies': {'fake': 'unit'}}
        p = {'schema_version': 1, 'kind': 'w16_frozen_pronunciation_protocol', 'state': 'frozen',
             'source_git': {'commit': 'f' * 40, 'status': ''}, 'sources_sha256': {'unit_fixture': 'f' * 64},
             'dependencies': original['dependencies'], 'settings': deepcopy(WORKER.EXPECTED), 'policy': E.policy(),
             'fixture': deepcopy(DATA), 'case_fixture': E.ASR.file_identity(ROOT / 'configs/w16-pronunciation-cases-v1.json'),
             'cases': cases, 'model_dir': str(model), 'model_files': [E.ASR.file_identity(f) for f in sorted(model.iterdir())],
             'frozen_inputs': [item], 'original_w16_protocol': original, 'configs': deepcopy(original['configs']),
             'models': original['models'], 'hotwords': None,
             'preregistration': {'file': item, 'root_commit': E.PREREG_COMMIT}}
        protocol = root / 'protocol.json'; protocol.write_text(json.dumps(p, ensure_ascii=False, indent=2) + '\n')
        return protocol, p

    def run_fake(self, root, *, wrong=None, asr_fail_at=None, generation_fail=None, missing_trace=None,
                 worker_exit_error=False, source_drift=False):
        protocol, p = self.make_protocol(root)
        calls = []; case_by_text = {c['reference_text']: c for c in p['cases']}
        word_targets = sorted({f['text'] for c in p['cases'] for f in c['focal_pronunciation']}, key=len, reverse=True)
        texts = [c['reference_text'] for arm in ('A', 'B') for c in p['cases']]
        if wrong:
            arm, name, value = wrong
            texts[(0 if arm == 'A' else 18) + next(i for i, c in enumerate(p['cases']) if c['id'] == name)] = value
        # Missing generated audio is not sent to Fake ASR, so its sequence skips that item.
        if generation_fail:
            arm, name = generation_fail
            del texts[(0 if arm == 'A' else 18) + next(i for i, c in enumerate(p['cases']) if c['id'] == name)]
        fake = FakeAsr(texts, asr_fail_at)

        def runner(value, path, arm, directory, logs):
            calls.append(arm)
            class FakeTts:
                sample_rate, num_speakers = 8000, 174
                def synthesize(self, text, *, speaker_id, speed):
                    case = case_by_text[text]
                    os.write(2, ('Raw text: ' + text + '\n').encode())
                    output = []; at = 0
                    while at < len(text):
                        term = next((w for w in word_targets if arm == 'B' and text.startswith(w, at)), None)
                        term = term or text[at]; output.append(term); at += len(term)
                    normalized = ''.join('#$|' + w + '|$#' if len(w) > 1 else w for w in output)
                    for _ in range(2 if arm == 'A' else 48): os.write(2, ('After normalizing: ' + normalized + '\n').encode())
                    os.write(2, ('Input text in string: ' + normalized + '\n').encode())
                    if missing_trace != (arm, case['id']):
                        os.write(2, ('After splitting to words: ' + ' '.join(output) + '\n').encode())
                    if generation_fail == (arm, case['id']): raise RuntimeError('Injected Fake synthesis failure')
                    return SimpleNamespace(samples=np.zeros(80, np.float32), sample_rate=8000)
            result = WORKER.run(Namespace(protocol=path, arm=arm, outdir=directory), tts_factory=lambda *a, **k: FakeTts())
            stdout = logs / (arm + '.stdout'); stdout.write_text('Fake worker stdout; no model loaded\n')
            stderr = logs / (arm + '.stderr'); stderr.write_text('Fake worker wrapper stderr\n')
            return {'arm': arm, 'returncode': 2 if worker_exit_error and arm == 'B' else 0 if result['passed'] else 1,
                    'stdout': E.ASR.file_identity(stdout), 'stderr': E.ASR.file_identity(stderr), 'wall_ms': 0.0}

        current = {'calls': 0}
        def git():
            current['calls'] += 1
            if source_drift and len(calls) == 2 and len(fake.calls) >= 36:
                return {'commit': 'a' * 40, 'status': ' M injected_source_drift'}
            return p['source_git']
        with ExitStack() as stack:
            stack.enter_context(patch.object(E.DEV, 'git', side_effect=git))
            stack.enter_context(patch.object(E.DEV, 'sources', return_value=p['sources_sha256']))
            stack.enter_context(patch.object(E.ASR, 'dependency_identity', return_value=p['dependencies']))
            result = E.run(Namespace(protocol=protocol, outdir=root / 'run'), worker_runner=runner, asr_factory=lambda p: fake)
        return result, p, fake, calls

    def reindex(self, run):
        files = [p for p in run.rglob('*') if p.is_file() and p not in [run / n for n in ('raw-index.json', 'summary.json', 'report.md')]]
        (run / 'raw-index.json').write_text(json.dumps({'artifacts': [{'path': str(f.relative_to(run)), 'bytes': f.stat().st_size, 'sha256': E.ASR.sha256(f)} for f in sorted(files)]}))

    def test_frozen_independent_material_settings_and_fifteen_oracles_are_strict(self):
        with TemporaryDirectory() as temp:
            _, p = self.make_protocol(Path(temp)); E.validate_protocol(p)
            self.assertEqual(len(p['cases']), 18)
            self.assertEqual(sum(len(c['focal_pronunciation']) for c in p['cases']), 15)
            for mutate in ('text', 'settings', 'config', 'hotwords', 'prereg'):
                q = deepcopy(p)
                if mutate == 'text': q['cases'][0]['reference_text'] += 'x'
                elif mutate == 'settings': q['settings']['noise_scale'] = .667
                elif mutate == 'config': q['configs']['audio']['chunk_duration_ms'] = 99
                elif mutate == 'hotwords': q['hotwords'] = {'path': 'invented'}
                else: q['preregistration']['root_commit'] = '0' * 40
                with self.subTest(change=mutate), self.assertRaises(ValueError): E.validate_protocol(q)

    def test_actual_new_guard_schema_and_relative_path_hashes_are_accepted(self):
        with TemporaryDirectory() as temp:
            root = Path(temp); data = root / 'old.txt'; data.write_text('original failed evidence')
            item = E.ASR.file_identity(data); item['path'] = 'old.txt'
            guard = root / 'guard.json'; guard.write_text(json.dumps({'kind': 'w16_pronunciation_original_data_guard', 'path_base': str(root), 'immutable_artifacts': [item]}))
            self.assertEqual(E.original_guard(guard), [E.ASR.file_identity(data)])
            data.write_text('changed')
            with self.assertRaises(ValueError): E.original_guard(guard)
            value = E.read(guard); value['kind'] = 'old_wrong_schema'; guard.write_text(json.dumps(value))
            with self.assertRaises(ValueError): E.original_guard(guard)

    def test_complete_fake_flow_generates_once_per_arm_and_separates_all_evidence_layers(self):
        with TemporaryDirectory() as temp:
            root = Path(temp); s, p, fake, calls = self.run_fake(root)
            self.assertTrue(s['passed'], s)
            self.assertEqual(calls, ['A', 'B']); self.assertEqual(s['tts_generation_attempts'], 36)
            self.assertEqual(len(fake.calls), 36); self.assertEqual(s['asr_attempts'], 36)
            self.assertEqual(s['execution_kind'], 'fake_worker_or_asr_unit_fixture')
            self.assertEqual(s['independent_text_oracle_matches'], 10)
            self.assertEqual(s['by_arm']['B']['focal_passed'], 15)
            self.assertTrue(s['native_word_lexicon_focal_gate']['B'])
            self.assertTrue(s['home_semantic_gate']['B'])
            self.assertTrue(s['development_candidate_gate']['B'])
            self.assertFalse(s['development_candidate_gate']['A'])
            self.assertTrue(s['zero_noise_numeric_controls_passed'])
            self.assertFalse(s['actual_acoustic_pronunciation_accepted'])
            self.assertFalse(s['human_listening_accepted']); self.assertFalse(s['automatic_product_promotion'])
            for arm in ('A', 'B'):
                self.assertEqual(s['by_arm'][arm]['home_speech_scored'], 10)
                self.assertEqual(s['by_arm'][arm]['speech_scored'], 18)
            for i in [*range(10, 18), *range(28, 36)]:
                r = E.read(root / f'run/records/{i:04}.json')
                self.assertIsNone(r['primary']); self.assertIsNone(r['primary_semantics_passed'])

    def test_wrong_asr_cannot_borrow_reference_or_phonetic_score(self):
        with TemporaryDirectory() as temp:
            root = Path(temp); s, _, _, _ = self.run_fake(root, wrong=('B', 'W16-TA01', '开启卧室空调'))
            self.assertTrue(s['passed'])  # Integrity, not semantic success.
            self.assertEqual(s['independent_text_oracle_matches'], 10)
            self.assertTrue(s['native_word_lexicon_focal_gate']['B'])
            self.assertFalse(s['home_semantic_gate']['B']); self.assertFalse(s['development_candidate_gate']['B'])
            row = E.read(root / 'run/records/0023.json')
            self.assertEqual(row['primary']['status'], 'executed')
            self.assertFalse(row['primary_semantics_passed'])
            self.assertEqual(s['by_arm']['B']['abnormal_state_preserved'], 4)

    def test_decoder_failure_is_recorded_once_and_other_cases_continue(self):
        with TemporaryDirectory() as temp:
            root = Path(temp); s, _, fake, calls = self.run_fake(root, asr_fail_at=0)
            self.assertFalse(s['passed']); self.assertEqual(len(fake.calls), 36); self.assertEqual(calls, ['A', 'B'])
            row = E.read(root / 'run/records/0000.json')
            self.assertTrue(row['asr_attempted']); self.assertIsNone(row['recognizer_output'])
            self.assertTrue(row['errors']); self.assertEqual(s['by_arm']['B']['completed'], 18)

    def test_generation_failure_keeps_missing_listening_slot_and_never_retries(self):
        with TemporaryDirectory() as temp:
            root = Path(temp); s, _, fake, calls = self.run_fake(root, generation_fail=('B', 'W16-TN05'))
            self.assertFalse(s['passed']); self.assertEqual(s['tts_generation_attempts'], 36)
            self.assertEqual(len(fake.calls), 35); self.assertEqual(calls, ['A', 'B'])
            packet = E.read(root / 'run/execution.json')['listening_packet']
            self.assertEqual(packet['ratings_rows'], 36); self.assertEqual(packet['audio_copied'], 35)
            ratings = list(csv.DictReader((root / 'run/listening/ratings.csv').open()))
            self.assertEqual(sum(r['status'] == 'missing_audio' for r in ratings), 1)
            self.assertFalse(any(r['heard_words'] for r in ratings))

    def test_missing_native_words_cannot_use_expected_text_as_observed_phonemes(self):
        with TemporaryDirectory() as temp:
            root = Path(temp); s, _, fake, _ = self.run_fake(root, missing_trace=('A', 'W16-TN01'))
            row = E.read(root / 'run/records/0000.json')
            self.assertFalse(s['passed']); self.assertEqual(len(fake.calls), 36)
            self.assertIsNone(row['actual_native_words']); self.assertIsNone(row['frontend']['native_words'])
            self.assertEqual(row['frontend']['focal_observed'], 0)
            self.assertIsNone(row['frontend']['focal_checks'][0]['actual_phones'])

    def test_worker_exit_or_runtime_drift_blocks_development_promotion_even_with_good_phones(self):
        for kwargs in ({'worker_exit_error': True}, {'source_drift': True}):
            with self.subTest(case=kwargs), TemporaryDirectory() as temp:
                s, _, _, _ = self.run_fake(Path(temp), **kwargs)
                self.assertFalse(s['passed']); self.assertTrue(s['native_word_lexicon_focal_gate']['B'])
                self.assertTrue(s['home_semantic_gate']['B']); self.assertFalse(s['development_candidate_gate']['B'])

    def test_readonly_report_is_byte_identical_without_synthesis_asr_control_or_listening_copy(self):
        with TemporaryDirectory() as temp:
            root = Path(temp); self.run_fake(root)
            helper = E.analysis_module()
            with (
                patch.object(E, 'subprocess_worker', side_effect=AssertionError('No TTS worker')),
                patch.object(E.ASR, 'native_asr', side_effect=AssertionError('No ASR')),
                patch.object(E.HOME, 'reference_probe', side_effect=AssertionError('No reference execution')),
                patch.object(E.HOME, 'fixture', side_effect=AssertionError('No Mock fixture/control')),
                patch.object(helper, 'make_listening_packet', side_effect=AssertionError('No listening copy')),
            ):
                s = E.report(Namespace(run=root / 'run', outdir=root / 'report'))
            self.assertTrue(s['passed'])
            for name in ('summary.json', 'report.md'):
                self.assertEqual((root / 'run' / name).read_bytes(), (root / 'report' / name).read_bytes())

    def test_reindexed_metric_frontend_oracle_and_probe_control_tampering_is_rejected(self):
        for change in ('metrics', 'frontend', 'probe_control', 'timing', 'missing_output'):
            with self.subTest(change=change), TemporaryDirectory() as temp:
                root = Path(temp); self.run_fake(root)
                file = root / ('run/records/0010.json' if change == 'probe_control' else 'run/records/0000.json')
                row = E.read(file)
                if change == 'metrics': row['text_metrics']['character_edits'] += 1
                elif change == 'frontend': row['frontend']['focal_passed'] += 1
                elif change == 'probe_control':
                    row['primary'] = E.read(root / 'run/records/0000.json')['primary']
                    row['primary_semantics_passed'] = True
                elif change == 'timing': row['timing'].pop('asr_replay_wall_ms')
                else:
                    row['recognizer_output'] = None; row['text_metrics'] = None
                    row['voice_normalized'] = None; row['asr_metrics'] = None
                file.write_text(json.dumps(row)); self.reindex(root / 'run')
                with self.assertRaises(ValueError): E.report(Namespace(run=root / 'run', outdir=root / 'report'))

    def test_reindexed_cached_worker_passed_and_attempt_counts_cannot_override_worker_ledger(self):
        with TemporaryDirectory() as temp:
            root = Path(temp); self.run_fake(root)
            file = root / 'run/execution.json'; value = E.read(file)
            value['workers'][1]['generation_attempts'] += 1
            file.write_text(json.dumps(value)); self.reindex(root / 'run')
            with self.assertRaises(ValueError): E.report(Namespace(run=root / 'run', outdir=root / 'report'))

    def test_reindexed_worker_ledger_or_verdict_itself_cannot_replace_observed_evidence(self):
        for change in ('ledger', 'passed'):
            with self.subTest(change=change), TemporaryDirectory() as temp:
                root = Path(temp); self.run_fake(root)
                worker = root / 'run/workers/B'
                if change == 'ledger':
                    file = worker / 'ledger.jsonl'
                    file.write_text('\n'.join(file.read_text().splitlines()[2:]) + '\n')
                else:
                    file = worker / 'execution.json'; value = E.read(file); value['passed'] = False
                    file.write_text(json.dumps(value))
                    top_file = root / 'run/execution.json'; top = E.read(top_file); top['workers'][1] = value
                    top_file.write_text(json.dumps(top))
                files = [f for f in worker.rglob('*') if f.is_file() and f.name != 'raw-index.json']
                (worker / 'raw-index.json').write_text(json.dumps({'artifacts': [{'path': str(f.relative_to(worker)),
                    'bytes': f.stat().st_size, 'sha256': E.ASR.sha256(f)} for f in sorted(files)]}))
                self.reindex(root / 'run')
                with self.assertRaises(ValueError): E.report(Namespace(run=root / 'run', outdir=root / 'report'))

    def test_fake_worker_cannot_be_reclassified_as_native_and_model_resources_are_bound(self):
        for change in ('classification', 'lexicon'):
            with self.subTest(change=change), TemporaryDirectory() as temp:
                root = Path(temp); self.run_fake(root)
                if change == 'classification':
                    file = root / 'run/execution.json'; value = E.read(file)
                    value['execution_kind'] = 'native_tts_and_asr'; file.write_text(json.dumps(value))
                else:
                    with (root / 'fake-model/lexicon.txt').open('a') as out: out.write('invented z1 #0\n')
                self.reindex(root / 'run')
                with self.assertRaises(ValueError): E.report(Namespace(run=root / 'run', outdir=root / 'report'))

    def test_reindexed_human_ratings_cannot_fabricate_listening_acceptance(self):
        with TemporaryDirectory() as temp:
            root = Path(temp); self.run_fake(root)
            file = root / 'run/listening/ratings.csv'
            rows = list(csv.DictReader(file.open())); rows[0]['heard_words'] = 'fabricated automatic review'
            with file.open('w', newline='') as out:
                writer = csv.DictWriter(out, fieldnames=list(rows[0]), lineterminator='\n')
                writer.writeheader(); writer.writerows(rows)
            execution = E.read(root / 'run/execution.json')
            packet = execution['listening_packet']
            packet['artifacts'] = [E.ASR.file_identity(Path(a['path'])) for a in packet['artifacts']]
            (root / 'run/execution.json').write_text(json.dumps(execution)); self.reindex(root / 'run')
            with self.assertRaises(ValueError): E.report(Namespace(run=root / 'run', outdir=root / 'report'))

    def test_missing_ledger_or_reordered_entries_are_rejected_without_model_calls(self):
        for change in ('missing', 'reordered'):
            with self.subTest(change=change), TemporaryDirectory() as temp:
                root = Path(temp); self.run_fake(root); file = root / 'run/ledger.jsonl'
                if change == 'missing': file.unlink()
                else:
                    lines = file.read_text().splitlines(); lines[0], lines[1] = lines[1], lines[0]
                    file.write_text('\n'.join(lines) + '\n')
                self.reindex(root / 'run')
                with self.assertRaises((ValueError, FileNotFoundError)):
                    E.report(Namespace(run=root / 'run', outdir=root / 'report'))

    def test_existing_output_and_dirty_prepare_fail_before_inputs_or_models(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            with (
                patch.object(E, 'read', side_effect=AssertionError('Do not read input')),
                patch.object(E.ASR, 'native_asr', side_effect=AssertionError('No model')),
                patch.object(E, 'subprocess_worker', side_effect=AssertionError('No worker')),
            ):
                for fn, args in [(E.prepare, Namespace(outdir=root)),
                                 (E.run, Namespace(protocol=root / 'absent', outdir=root)),
                                 (E.report, Namespace(run=root / 'absent', outdir=root))]:
                    with self.subTest(operation=fn.__name__), self.assertRaises(FileExistsError): fn(args)
            with (
                patch.object(E.DEV, 'git', return_value={'commit': 'f' * 40, 'status': ' M dirty'}),
                patch.object(E, 'read', side_effect=AssertionError('Do not read input')),
            ):
                with self.assertRaises(ValueError): E.prepare(Namespace(outdir=root / 'new'))



if __name__ == '__main__':
    unittest.main()
