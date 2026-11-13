"""D orchestration regressions with temporary Fake models/WAVs, never native."""
from argparse import Namespace
from contextlib import ExitStack
from copy import deepcopy
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from edge_smart_home.audio.interfaces import StreamingRecognition
from scripts import evaluate_w16_context_d as E
from scripts import w16_context_d_worker as W

ROOT = Path(__file__).resolve().parents[1]
DATA = E.read(ROOT / 'configs/w16-pronunciation-cases-v1.json')
CONFIG = {name: E.read(ROOT / 'configs' / (name + '.json')) for name in E.HOME.CONFIG_NAMES}


def make_fixture(root):
    """Fake native resources and archived B evidence; no real waveforms loaded."""
    model = root / 'model'; model.mkdir()
    old = root / 'old'; (old / 'records').mkdir(parents=True); (old / 'references').mkdir()
    bundle = root / 'bundle'; bundle.mkdir()
    cases = deepcopy(DATA['home_cases'] + DATA['pronunciation_probes'])
    words = {ch: ['z1', '#0'] for c in cases for ch in c['reference_text']}
    for case in cases:
        for focal in case['focal_pronunciation']:
            syllables, pending = [], []
            for token in focal['expected_phones']:
                pending.append(token)
                if token == '#0': syllables.append(pending); pending = []
            for char, phones in zip(focal['text'], syllables): words[char] = phones
            if focal['text'] != '调到': words[focal['text']] = focal['expected_phones']
    words['调'], words['重'], words['乐'] = ['d', 'iao4', '#0'], ['zh', 'ong4', '#0'], ['l', 'e4', '#0']
    words['温度'] = words['温'] + words['度']
    (model / 'lexicon.txt').write_text('\n'.join(k + ' ' + ' '.join(v) for k, v in sorted(words.items())) + '\n')
    alphabet = sorted({p for v in words.values() for p in v} | {'sil', 'eos', 't', 'iao2', 'd', 'ao4'})
    (model / 'tokens.txt').write_text('\n'.join(f'{p} {i}' for i, p in enumerate(alphabet)) + '\n')
    for name in W.MODEL_FILES:
        if not (model / name).exists(): (model / name).write_bytes(b'Fake unit resource, never native')
    sidecar = bundle / 'lexicon-context.txt'
    sidecar.write_bytes((model / 'lexicon.txt').read_bytes() + b'\xe8\xb0\x83\xe5\x88\xb0 t iao2 #0 d ao4 #0\n')
    far = bundle / 'context.far'; far.write_bytes(b'Fake unit FAR, never native')
    sentinel = root / 'frozen.json'; sentinel.write_text('{}\n')
    groups = sorted([k for k in words if len(k) > 1], key=len, reverse=True)
    resources = E.ANALYSIS.load_resources(model)
    controls, static = [], []
    for i, case in enumerate(cases):
        text, split, at = case['reference_text'], [], 0
        while at < len(text):
            term = next((k for k in groups if text.startswith(k, at)), text[at])
            split.append(term); at += len(term)
        normalized = ''.join('#$|' + k + '|$#' if len(k) > 1 else k for k in split)
        after = E.re.sub(E.PATTERN, lambda _: '#$|调到|$#', normalized)
        floating = old / f'{i:04d}.npy'
        with floating.open('wb') as f: np.save(f, np.zeros(80, np.float32), allow_pickle=False)
        _, wav, _ = E.BASE.AUDIO.quantize_pcm16(np.zeros(80, np.float32), 8000)
        wavpath = old / f'{i:04d}.wav'; wavpath.write_bytes(wav)
        row = {'id': case['id'], 'arm': 'B', 'case': case, 'category': case['category'],
               'completed': True, 'synthesis_completed': True, 'asr_attempted': True,
               'native_trace': {'normalization_steps': [normalized] * 48},
               'actual_native_words': split, 'frontend': E.ANALYSIS.derive_frontend(case, split, resources),
               'source_float': E.ASR.file_identity(floating), 'pcm16_wav': E.ASR.file_identity(wavpath),
               'text_metrics': E.text_metrics(text, text), 'primary': None,
               'primary_semantics_passed': False if case['category'] in ['normal', 'abnormal'] else None,
               'errors': [], 'timing': {}}
        target = old / 'records' / f'{i:04d}.json'; E.write(target, row)
        controls.append(E.ASR.file_identity(target))
        static.append({'id': case['id'], 'input_normalized': normalized, 'output_normalized': after,
                       'changed': before_different(normalized, after), 'surface_preserved': True,
                       'target': case['id'] in E.TARGET_IDS})
    refs = []
    for i, case in enumerate(DATA['home_cases']):
        ref = {'case': case, 'probe': E.HOME.reference_probe(case, CONFIG), 'error': None}
        target = old / 'references' / f'{i:04d}.json'; E.write(target, ref)
        refs.append(E.ASR.file_identity(target))
    manifest = {'schema_version': 1, 'kind': 'w16_temperature_context_bundle', 'state': 'frozen', 'passed': True,
                'resources': {'sidecar_lexicon': E.ASR.file_identity(sidecar), 'context_far': E.ASR.file_identity(far)},
                'static_validation': {'passed': True, 'cases': static}, 'compiler': {'kind': 'Fake compiler fixture, never run'}}
    E.write(bundle / 'manifest.json', manifest)
    old_protocol = {'configs': deepcopy(CONFIG), 'models': {}, 'hotwords': None,
                    'model_files': [E.ASR.file_identity(f) for f in sorted(model.iterdir())],
                    'model_dir': str(model), 'dependencies': {'unit_fixture': True},
                    'fixture': deepcopy(DATA), 'cases': cases}
    E.write(old / 'protocol.json', old_protocol)
    p = {'schema_version': 1, 'kind': 'w16_frozen_context_d_protocol', 'state': 'frozen',
         'source_git': {'commit': 'f' * 40, 'status': ''},
         'sources_sha256': {'scripts/evaluate_w16_context_d.py': E.ASR.sha256(ROOT / 'scripts/evaluate_w16_context_d.py')},
         'settings': deepcopy(W.EXPECTED), 'policy': E.policy(), 'cases': cases, 'fixture': deepcopy(DATA),
         'model_dir': str(model), 'model_files': old_protocol['model_files'], 'frozen_inputs': [E.ASR.file_identity(sentinel)],
         'original_run': str(old), 'original_source': {}, 'original_run_protocol': E.ASR.file_identity(old / 'protocol.json'),
         'bundle_manifest': E.ASR.file_identity(bundle / 'manifest.json'), 'resources': manifest['resources'],
         'compiler': manifest['compiler'], 'static_cases': static, 'control_records': controls,
         'archived_references': refs, 'configs': deepcopy(CONFIG), 'models': {}, 'hotwords': None,
         'dependencies': old_protocol['dependencies'],
         'registered_plan': E.ASR.file_identity(ROOT / 'configs/w16-context-repair-prereg-v1.json')}
    protocol = root / 'protocol.json'; E.write(protocol, p)
    return protocol, p, manifest


def before_different(before, after):
    return before != after


class FakeTts:
    sample_rate, num_speakers = 8000, 174
    def __init__(self, p, fail_at=None, missing_step_at=None, source_drift=False):
        self.p, self.fail_at, self.missing_step_at, self.calls = p, fail_at, missing_step_at, []
        self.source_drift = source_drift
    def synthesize(self, text, *, speaker_id, speed):
        i = len(self.calls); self.calls.append((text, speaker_id, speed))
        if self.source_drift and i == 0:
            Path(self.p['frozen_inputs'][0]['path']).write_text('Injected source identity drift')
        case = self.p['cases'][i]; before = self.p['static_cases'][i]['input_normalized']
        after = self.p['static_cases'][i]['output_normalized']
        os.write(2, ('Raw text: ' + text + '\n').encode())
        if i == self.fail_at: raise RuntimeError('Injected Fake synthesis failure')
        for _ in range(48 if i != self.missing_step_at else 47):
            os.write(2, ('After normalizing: ' + before + '\n').encode())
        os.write(2, ('After normalizing: ' + after + '\nInput text in string: ' + after + '\n').encode())
        output, at = [], 0
        while at < len(after):
            if after.startswith('#$|', at):
                end = after.index('|$#', at + 3); output.append(after[at + 3:end]); at = end + 3
            else:
                output.append(after[at]); at += 1
        os.write(2, ('After splitting to words: ' + ' '.join(output) + '\n').encode())
        return SimpleNamespace(samples=np.zeros(80, np.float32), sample_rate=8000)


class FakeAsr:
    def __init__(self, texts, fail_at=None):
        self.texts, self.fail_at, self.calls, self.factory_calls = texts, fail_at, [], 0
    def transcribe_waveform(self, samples, rate, chunk_ms):
        i = len(self.calls); self.calls.append((len(samples), rate, chunk_ms))
        if i == self.fail_at: raise RuntimeError('Injected Fake ASR failure')
        return StreamingRecognition(self.texts[i], len(samples) / rate, .001)


class ContextDCoordinatorTests(unittest.TestCase):
    def run_fake(self, root, *, wrong=None, fail_generation=None, fail_asr=None, missing_step=None,
                 corrupt_worker=False, worker_returncode=None, forbid_primary_fixture=False, source_drift=False):
        protocol, p, _ = make_fixture(root)
        texts = [c['reference_text'] for i, c in enumerate(p['cases']) if i != fail_generation]
        if wrong:
            texts[wrong[0]] = wrong[1]
        fake = FakeAsr(texts, fail_asr); tts = FakeTts(p, fail_generation, missing_step, source_drift)
        def asr_factory(_):
            fake.factory_calls += 1
            return fake
        def runner(path, directory, logs):
            result = W.run(Namespace(protocol=path, arm='D', outdir=directory), tts_factory=lambda *a, **kw: tts)
            if corrupt_worker: (directory / 'float/W16-TN01.npy').write_bytes(b'corrupt Fake worker evidence')
            (logs / 'stdout').write_text('Fake worker only\n'); (logs / 'stderr').write_text('')
            return {'argv': ['Fake worker'], 'returncode': worker_returncode if worker_returncode is not None else 0 if result['passed'] else 1,
                    'stdout': E.ASR.file_identity(logs / 'stdout'), 'stderr': E.ASR.file_identity(logs / 'stderr')}
        with ExitStack() as stack:
            stack.enter_context(patch.object(E, 'validate_protocol'))
            stack.enter_context(patch.object(E.DEV, 'git', return_value=p['source_git']))
            if forbid_primary_fixture:
                stack.enter_context(patch.object(E.HOME, 'fixture', side_effect=AssertionError('No Mock fixture after native configuration abort')))
            result = E.run(Namespace(protocol=protocol, outdir=root / 'run'), worker_runner=runner, asr_factory=asr_factory)
        return result, p, tts, fake

    def test_complete_fake_D18_reuses_control_and_readonly_report_is_byte_identical(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp); s, p, tts, asr = self.run_fake(root)
            self.assertTrue(s['passed'], s)
            self.assertEqual(len(tts.calls), 18); self.assertEqual(len(asr.calls), 18)
            self.assertEqual(s['tts_generation_attempts'], 18); self.assertEqual(s['new_reference_control_calls'], 0)
            self.assertEqual(s['archived_reference_oracle_matches'], 10)
            self.assertTrue(s['strict_frontend_regression_gate']); self.assertTrue(s['home_semantic_gate'])
            self.assertEqual(s['D']['focal_passed'], 15); self.assertEqual(len(s['non_target_comparisons']), 16)
            self.assertFalse(s['human_listening_accepted']); self.assertFalse(s['automatic_product_promotion'])
            with patch.object(E, 'validate_protocol'):
                rebuilt = E.report(Namespace(run=root / 'run', outdir=root / 'report'))
            self.assertEqual(rebuilt, s)
            for name in ['summary.json', 'report.md']:
                self.assertEqual((root / 'run' / name).read_bytes(), (root / 'report' / name).read_bytes())
            self.assertEqual(len(asr.calls), 18); self.assertEqual(len(tts.calls), 18)

    def test_wrong_32_transcript_does_not_borrow_archived_text_control_or_phone_score(self):
        with TemporaryDirectory() as tmp:
            s, _, _, _ = self.run_fake(Path(tmp), wrong=(6, '卧室空调温度调到二十四度'))
            self.assertTrue(s['passed']); self.assertTrue(s['strict_frontend_regression_gate'])
            self.assertFalse(s['home_semantic_gate']); self.assertFalse(s['development_candidate_gate'])
            self.assertEqual(s['archived_reference_oracle_matches'], 10)

    def test_failed_generation_is_retained_once_and_other_cases_continue(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp); s, _, tts, asr = self.run_fake(root, fail_generation=3)
            self.assertFalse(s['passed']); self.assertEqual(len(tts.calls), 18); self.assertEqual(len(asr.calls), 17)
            self.assertEqual(len(list((root / 'run/records').glob('*.json'))), 18)
            row = E.read(root / 'run/records/0003.json')
            self.assertFalse(row['asr_attempted']); self.assertFalse(row['completed'])
            self.assertIsNone(row['recognizer_output']); self.assertIsNone(row['pcm16_wav'])

    def test_failed_asr_is_not_retried_or_replaced_with_reference(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp); s, _, tts, asr = self.run_fake(root, fail_asr=1)
            self.assertFalse(s['passed']); self.assertEqual(len(asr.calls), 18); self.assertEqual(len(tts.calls), 18)
            self.assertIsNone(E.read(root / 'run/records/0001.json')['recognizer_output'])
            self.assertFalse(s['home_semantic_gate'])

    def test_incomplete_native_order_fails_strict_gate_even_if_fake_wav_and_words_match(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            s, _, tts, asr = self.run_fake(root, missing_step=0, forbid_primary_fixture=True)
            self.assertFalse(s['passed']); self.assertFalse(s['strict_frontend_regression_gate'])
            self.assertEqual(len(tts.calls), 1); self.assertEqual(len(asr.calls), 0)
            self.assertEqual(asr.factory_calls, 0)
            self.assertEqual(len(list((root / 'run/records').glob('*.json'))), 18)
            with patch.object(E, 'validate_protocol'):
                rebuilt = E.report(Namespace(run=root / 'run', outdir=root / 'report'))
            self.assertEqual(rebuilt, s)

    def test_worker_evidence_failure_keeps_unknown_not_false_zero_generation_count(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp); s, _, tts, asr = self.run_fake(root, corrupt_worker=True)
            self.assertEqual(len(tts.calls), 18); self.assertEqual(len(asr.calls), 0)
            self.assertFalse(s['passed']); self.assertIsNone(s['tts_generation_attempts'])
            self.assertEqual(s['unverified_reported_tts_generation_attempts'], 18)

    def test_worker_input_integrity_failure_stops_downstream_asr_and_mock_without_hiding_TTS_calls(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            s, _, tts, asr = self.run_fake(root, source_drift=True, forbid_primary_fixture=True)
            self.assertFalse(s['passed']); self.assertFalse(s['strict_frontend_regression_gate'])
            self.assertEqual(len(tts.calls), 18); self.assertEqual(s['tts_generation_attempts'], 18)
            self.assertEqual(asr.factory_calls, 0); self.assertEqual(len(asr.calls), 0)
            self.assertEqual(E.read(root / 'run/execution.json')['worker_stop_kind'], 'input_integrity')
            self.assertEqual(len(list((root / 'run/records').glob('*.json'))), 18)

    def test_nonzero_worker_exit_cannot_pass_even_if_its_saved_evidence_is_complete(self):
        with TemporaryDirectory() as tmp:
            s, _, _, _ = self.run_fake(Path(tmp), worker_returncode=1)
            self.assertFalse(s['passed']); self.assertEqual(s['tts_generation_attempts'], 18)

    def test_report_rejects_rewritten_raw_audio_without_models_or_inference(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp); _, _, tts, asr = self.run_fake(root)
            (root / 'run/worker/pcm16/W16-TN01.wav').write_bytes(b'tampered')
            with patch.object(E, 'validate_protocol'), self.assertRaises(ValueError):
                E.report(Namespace(run=root / 'run', outdir=root / 'bad-report'))
            self.assertEqual(len(tts.calls), 18); self.assertEqual(len(asr.calls), 18)

    def test_static_proof_requires_only_original_two_marked_temperature_contexts(self):
        with TemporaryDirectory() as tmp:
            _, p, _ = make_fixture(Path(tmp)); controls = [E.read(x['path']) for x in p['control_records']]
            E.static_case_check(p['cases'], controls, p['static_cases'])
            for kind in ['drop', 'unrelated', 'input', 'target_flag', 'target_output']:
                proof = deepcopy(p['static_cases'])
                if kind == 'drop': proof.pop()
                elif kind == 'unrelated': proof[0]['changed'] = True
                elif kind == 'input': proof[1]['input_normalized'] = p['cases'][1]['reference_text']
                elif kind == 'target_flag': proof[0]['target'] = True
                else: proof[1]['output_normalized'] = proof[1]['input_normalized']
                with self.subTest(kind=kind), self.assertRaises(ValueError): E.static_case_check(p['cases'], controls, proof)

    def test_exact_non_target_gate_does_not_allow_a_relaxed_wav_epsilon(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp); s, p, _, _ = self.run_fake(root)
            rows = [E.read(f) for f in sorted((root / 'run/records').glob('*.json'))]
            rows[0]['pcm16_wav']['sha256'] = '0' * 64
            rebuilt = E.aggregate(p, rows, [E.read(a['path']) for a in p['control_records']],
                                  [E.read(a['path']) for a in p['archived_references']], E.read(root / 'run/execution.json'))
            self.assertTrue(s['strict_frontend_regression_gate'])
            self.assertFalse(rebuilt['strict_frontend_gates']['sixteen_non_target_exact_artifacts'])
            self.assertFalse(rebuilt['strict_frontend_regression_gate'])

    def test_existing_outputs_are_refused_before_protocol_or_model_access(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp)
            with patch.object(E, 'read', side_effect=AssertionError('must reject first')):
                with self.assertRaises(FileExistsError): E.run(Namespace(protocol=path / 'missing', outdir=path))
                with self.assertRaises(FileExistsError): E.report(Namespace(run=path / 'missing', outdir=path))

    def test_output_overlap_and_symlink_protection_includes_models_and_original_run(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp); _, p, _ = make_fixture(root)
            for path in [Path(p['original_run']) / 'new', Path(p['model_dir']) / 'new', root / 'bundle/new']:
                with self.subTest(path=path), self.assertRaises(ValueError): E.fresh_outdir(path, E.protocol_forbidden(p))
            (root / 'link').symlink_to(root / 'old', target_is_directory=True)
            with self.assertRaises(ValueError): E.fresh_outdir(root / 'link/new', [])

    def test_source_closure_files_at_repository_root_do_not_forbid_new_evaluation_directory(self):
        p = {'original_run': str(ROOT / 'evaluation/frozen-old'), 'model_dir': str(ROOT / 'models/frozen'),
             'bundle_manifest': {'path': str(ROOT / 'evaluation/frozen-bundle/manifest.json')}, 'models': {},
             'frozen_inputs': [{'path': str(ROOT / 'pyproject.toml')},
                               {'path': str(ROOT / 'src/edge_smart_home/__init__.py')},
                               {'path': str(ROOT / 'evaluation/frozen-older/records/a.json')}]}
        output = ROOT / 'evaluation/unit-fresh-context-closure-output-never-created'
        self.assertEqual(E.fresh_outdir(output, E.protocol_forbidden(p)), output)
        self.assertNotIn(ROOT, E.protocol_forbidden(p))
        with self.assertRaises(ValueError):
            E.fresh_outdir(ROOT / 'evaluation/frozen-older/new', E.protocol_forbidden(p))

    def test_complete_fake_prepare_freezes_compiler_bundle_all_guard_and_protocol_tampering_is_rejected(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp); _, p, manifest = make_fixture(root)
            archive = root / 'guarded'; archive.mkdir()
            guarded = []
            for i in range(1241):
                f = archive / f'{i:04d}.txt'; f.write_bytes(b'Fake protected fixture')
                guarded.append(E.ASR.file_identity(f))
            guard = root / 'guard.json'
            E.write(guard, {'kind': 'w16_context_d_original_data_guard', 'prior_index_artifacts_verified': 1247,
                            'path_base': str(root), 'immutable_artifacts': guarded})
            prereg = root / 'prereg.md'; prereg.write_text('Fake unit stage, not a native run')
            args = Namespace(bundle=Path(p['bundle_manifest']['path']), original_run=Path(p['original_run']),
                             registered_plan=ROOT / 'configs/w16-context-repair-prereg-v1.json',
                             old_data_guard=guard, preregistration=prereg, outdir=root / 'prepared')
            with ExitStack() as stack:
                stack.enter_context(patch.object(E.SOURCE, 'verify_source', return_value={}))
                stack.enter_context(patch.object(E, 'bundle_verify', return_value=manifest))
                stack.enter_context(patch.object(E.DEV, 'git', return_value=p['source_git']))
                stack.enter_context(patch.object(E.DEV, 'sources', return_value=p['sources_sha256']))
                stack.enter_context(patch.object(E.ASR, 'dependency_identity', return_value=p['dependencies']))
                result = E.prepare(args)
                self.assertTrue(result['passed']); self.assertEqual(result['new_tts_generations'], 0)
                frozen = E.read(root / 'prepared/protocol.json'); E.validate_protocol(frozen, current=True)
                self.assertGreaterEqual(len(frozen['frozen_inputs']), 1241)
                for kind in ['drop_input', 'drop_control', 'noise_bool', 'arm', 'far_order', 'compiler', 'source']:
                    q = deepcopy(frozen)
                    if kind == 'drop_input': q['frozen_inputs'].pop()
                    elif kind == 'drop_control': q['control_records'].pop()
                    elif kind == 'noise_bool': q['settings']['noise_scale'] = False
                    elif kind == 'arm': q['settings']['arm'] = 'B'
                    elif kind == 'far_order': q['static_cases'][1]['output_normalized'] = q['static_cases'][1]['input_normalized']
                    elif kind == 'compiler': q['compiler'] = {'changed': True}
                    else: q['source_git']['status'] = ' M dirty'
                    with self.subTest(kind=kind), self.assertRaises(ValueError): E.validate_protocol(q, current=True)

    def test_guard_cannot_replace1241_sources_with_repeated_padding(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp); _, p, manifest = make_fixture(root)
            dummy = root / 'guarded.txt'; dummy.write_text('one file')
            guard = root / 'guard.json'
            E.write(guard, {'kind': 'w16_context_d_original_data_guard', 'prior_index_artifacts_verified': 1247,
                            'path_base': str(root), 'immutable_artifacts': [E.ASR.file_identity(dummy)] * 1241})
            with self.assertRaisesRegex(ValueError, 'distinct'):
                E.frozen_input_list(guard, dummy, p['registered_plan'], p['bundle_manifest']['path'],
                                    p['original_run_protocol']['path'], p['control_records'], p['archived_references'], manifest)


if __name__ == '__main__':
    unittest.main()
