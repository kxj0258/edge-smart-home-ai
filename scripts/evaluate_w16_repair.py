"""Predeclared one-factor W16 replay contrasts; original evidence is read-only.

ASR/Voice/Mock development observations are separate from human-home acceptance.
The fixed shared A is not repeated per contrast and no result selects a rerun.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import gc
import json
import math
from pathlib import Path
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / 'src'):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
from scripts import evaluate_w16_voice_home as HOME
from scripts.run_unit_tests import network_disabled
from edge_smart_home.audio.pipeline import _normalize_transcript
from edge_smart_home.audio.validation import validate_audio_samples
from edge_smart_home.benchmark.asr_evaluation import text_metrics

ASR, DEV = HOME.ASR, HOME.DEV
read, write, now = HOME.read, HOME.write, HOME.now


def matrix():
    return [
        {'id': 'A', 'group': 'home', 'source': 'pcm16', 'rate': 8000, 'chunk_ms': 100, 'tail_seconds': .5},
        {'id': 'Q', 'group': 'home', 'source': 'original_float', 'rate': 8000, 'chunk_ms': 100, 'tail_seconds': .5},
        {'id': 'R', 'group': 'home', 'source': 'resampled_float', 'rate': 16000, 'chunk_ms': 100, 'tail_seconds': .5},
        {'id': 'T', 'group': 'home', 'source': 'pcm16', 'rate': 8000, 'chunk_ms': 100, 'tail_seconds': .66},
        {'id': 'C', 'group': 'home', 'source': 'pcm16', 'rate': 8000, 'chunk_ms': 10000, 'tail_seconds': .5},
        {'id': 'H16', 'group': 'news', 'source': 'pcm16', 'rate': 16000, 'chunk_ms': 100, 'tail_seconds': .5},
        {'id': 'H8', 'group': 'news', 'source': 'downsampled_float', 'rate': 8000, 'chunk_ms': 100, 'tail_seconds': .5},
    ]


def policy():
    return {'shared_baseline': 'A', 'home_contrasts': ['Q-A', 'R-A', 'T-A', 'C-A'],
            'news_contrast': 'H8-H16', 'asr_attempts': 60, 'home_attempts': 50, 'news_attempts': 10,
            'attempts_per_arm_clip': 1, 'warmups': 0, 'retries': 0,
            'independent_reference_controls': 9, 'tts_generation_calls': 0,
            'development_candidate_gate': 'all 5 normal and 5 abnormal semantic oracles and abnormal unchanged state; strict labels unchanged',
            'asr_improvement_gate': 'strict character edits lower than shared A, reported separately from semantic gate',
            'automatic_product_promotion': False, 'human_home_acceptance': False,
            'personal_understanding_acceptance': False}


def original_guard(run):
    index = read(run / 'raw-index.json')['artifacts']
    records = sorted((run / 'records').glob('*.json'))
    names = {'protocol.json', 'execution.json', 'ledger.jsonl', *(str(f.relative_to(run)) for f in records)}
    if len(records) != 10 or len(index) != len(names) or {a['path'] for a in index} != names:
        raise ValueError('Original W16 raw index must contain exact ten records/protocol/execution/ledger')
    items = []
    for a in index:
        value = ASR.file_identity(run / a['path'])
        if value['bytes'] != a['bytes'] or value['sha256'] != a['sha256']:
            raise ValueError('Original evidence differs')
        items.append(value)
    return items + [ASR.file_identity(run / n) for n in ('raw-index.json', 'summary.json', 'report.md')]


def identities(value):
    if isinstance(value, dict):
        if {'path', 'bytes', 'sha256'} <= value.keys():
            return [{k: value[k] for k in ('path', 'bytes', 'sha256')}]
        return [v for child in value.values() for v in identities(child)]
    if isinstance(value, list):
        return [v for child in value for v in identities(child)]
    return []


def validate_manifest(m, original, news, *, files=False):
    if (m.get('schema_version') != 1 or m.get('kind') != 'w16_repair_derived_audio_manifest'
            or m.get('preparation_kind') != 'deterministic_derivation'):
        raise ValueError('Expected formal deterministic derivation manifest, not a Fake or regenerated TTS')
    if (m.get('original_w16_manifest_identity') != original['manifest']
            or m.get('original_w14_protocol_identity', {}).get('sha256') != news['protocol_identity']['sha256']):
        raise ValueError('Derived inputs must bind exact first W16 manifest and W14 protocol')
    required = {'library': 'scipy.signal.resample_poly', 'home_ratio': {'up': 2, 'down': 1},
                'news_ratio': {'up': 1, 'down': 2}, 'window': ['kaiser', 5.0], 'padtype': 'constant',
                'cval': 0.0, 'output_dtype': 'float32', 'no_requantization': True,
                'gain_applied': False, 'trimmed': False, 'clipped': False}
    if any(m.get('resampler', {}).get(k) != v for k, v in required.items()):
        raise ValueError('Frozen single-factor resampler parameters differ')
    if (m.get('resampler', {}).get('scipy_version') != '1.15.3'
            or m.get('derivation_environment', {}).get('numpy') != '2.2.6'
            or m.get('derivation_environment', {}).get('scipy') != '1.15.3'
            or m.get('derivation_environment', {}).get('asr_environment_unchanged') is not True
            or not m.get('prepare_source_script')):
        raise ValueError('Derivation implementation and isolated environment identities required')
    if (m.get('first_waveforms_preserved') is not True or m.get('reference_texts_changed') is not False
            or type(m.get('new_tts_generations')) is not int or m['new_tts_generations'] != 0
            or m.get('no_requantization') is not True or not m.get('source_inputs_before')
            or m.get('source_inputs_before') != m.get('source_inputs_after')
            or not m.get('derivation_environment', {}).get('environment_lock_identity')):
        raise ValueError('Frozen source preservation and isolated dependency evidence required')
    if ([r.get('id') for r in m.get('home', [])] != [c['id'] for c in original['clips']]
            or [r.get('id') for r in m.get('news', [])] != [c['id'] for c in news['cases']]):
        raise ValueError('Derived coverage/order differs from frozen material')
    for row, clip in zip(m['home'], original['clips']):
        if (row.get('case_id') != clip['case_id'] or row.get('original_wav_identity') != clip['audio_identity']
                or row.get('original_float_identity') != clip['float_npy']
                or row.get('source_sample_rate') != 8000 or row.get('resampled_sample_rate') != 16000
                or type(row.get('frames_before')) is not int or row['frames_before'] <= 0
                or row.get('frames_after') != row['frames_before'] * 2):
            raise ValueError('Home source/sample-rate/frame derivation differs')
        if files:
            for key in ('original_wav_identity', 'original_float_identity', 'resampled_float_identity'):
                ASR.check_file(row[key])
    for row, case in zip(m['news'], news['cases']):
        if (row.get('original_wav_identity') != case['audio_identity'] or row.get('reference_text') != case['expected']
                or row.get('source_sample_rate') != 16000 or row.get('downsampled_sample_rate') != 8000
                or row.get('source_kind') != 'human_public_news' or row.get('license') != case['license']
                or row.get('source_url') != case['source_url'] or row.get('attribution') != case['attribution']
                or type(row.get('frames_before')) is not int or row['frames_before'] <= 0
                or row.get('frames_after') != (row['frames_before'] + 1) // 2):
            raise ValueError('News derivation/attribution differs; it must not become household material')
        if files:
            ASR.check_file(row['original_wav_identity']); ASR.check_file(row['downsampled_float_identity'])


def jobs(p):
    case_map = {c['id']: c for c in p['cases']}
    result = []
    for arm in p['matrix']:
        for material in p['derived']['home' if arm['group'] == 'home' else 'news']:
            home = arm['group'] == 'home'
            case = case_map[material['case_id']] if home else None
            source = arm['source']
            key = {'pcm16': 'original_wav_identity', 'original_float': 'original_float_identity',
                   'resampled_float': 'resampled_float_identity', 'downsampled_float': 'downsampled_float_identity'}[source]
            result.append({'arm': arm['id'], 'group': arm['group'], 'id': material['id'],
                           'case': case, 'material': material, 'source': source, 'audio_identity': material[key],
                           'reference_text': case['reference_text'] if home else material['reference_text'],
                           'source_kind': ('synthetic_signal' if case.get('audio_kind') == 'silence' else 'synthetic_audio') if home else 'human_public_news',
                           'rate': arm['rate'], 'chunk_ms': arm['chunk_ms'], 'tail_seconds': arm['tail_seconds'],
                           'frames': material['frames_after'] if source in ('resampled_float', 'downsampled_float') else material['frames_before']})
    return result


def validate_protocol(p, *, current=False):
    if (p.get('schema_version') != 1 or p.get('kind') != 'w16_frozen_repair_protocol' or p.get('state') != 'frozen'
            or p.get('matrix') != matrix() or p.get('policy') != policy() or p.get('source_git', {}).get('status') != ''):
        raise ValueError('Expected unchanged predeclared single-factor repair protocol')
    HOME.validate_cases({'schema_version': 1, 'kind': 'w16_voice_home_cases', 'cases': p['cases']})
    validate_manifest(p['derived'], p['original_protocol'], p['news_protocol'])
    if (p['cases'] != p['original_protocol']['cases'] or p['models'] != p['original_protocol']['models']
            or p['configs'] != p['original_protocol']['configs'] or p['hotwords'] != p['original_protocol']['hotwords']
            or p['dependencies'] != p['original_protocol']['dependencies']):
        raise ValueError('Frozen cases/model/config/hotword/native identities changed')
    if len(jobs(p)) != 60 or not p.get('sources_sha256') or not p.get('frozen_files'):
        raise ValueError('Incomplete protocol identity or observation coverage')
    if current:
        if DEV.git() != p['source_git'] or DEV.sources() != p['sources_sha256'] or ASR.dependency_identity() != p['dependencies']:
            raise ValueError('Exact clean source/native runtime required')
        for item in p['frozen_files']:
            ASR.check_file(item)


def load_audio(job):
    if job['source'] == 'pcm16':
        samples, rate, metadata = ASR.read_pcm16_mono(Path(job['audio_identity']['path']))
    else:
        import numpy as np
        value = np.load(job['audio_identity']['path'], allow_pickle=False)
        if value.dtype != np.dtype('float32'):
            raise ValueError('Frozen .npy must contain float32')
        samples = validate_audio_samples(value, job['rate'])
        rate = job['rate']
        metadata = {'format': 'float32 NPY, no additional quantization/clipping/resample', 'frames': len(samples),
                    'sample_rate': rate, 'peak_abs': float(np.max(np.abs(samples)))}
    if rate != job['rate'] or len(samples) != job['frames']:
        raise ValueError('Audio shape/rate differs from frozen derivation')
    return samples, rate, metadata


def prepare(args):
    args.outdir.mkdir(parents=True, exist_ok=False)
    git = DEV.git()
    if git['status']:
        raise ValueError('Prepare requires a clean committed child repository')
    original = read(args.original_run / 'protocol.json')
    HOME.validate_protocol(original)
    guarded = original_guard(args.original_run)
    news = read(args.news_protocol)
    news['protocol_identity'] = ASR.file_identity(args.news_protocol)
    if len(news['cases']) != 5 or any(c['source_kind'] != 'human' or c['license'] != 'CC-BY-4.0' for c in news['cases']):
        raise ValueError('Expected original five licensed W14 human news clips')
    if news['models'] != original['models']:
        raise ValueError('News and home controls must use same original ASR weights')
    derived = read(args.derived_manifest)
    validate_manifest(derived, original, news, files=True)
    if (derived.get('source_git') != git
            or derived['prepare_source_script'] != ASR.file_identity(ROOT / 'scripts/prepare_w16_repair_audio.py')):
        raise ValueError('Derivation must use these exact clean committed preparation sources')
    dependency_inputs = identities(derived['derivation_environment'])
    if len(dependency_inputs) < 2:
        raise ValueError('Isolated lock/wheel/requirement/runtime identities required')
    files = [*guarded, ASR.file_identity(args.derived_manifest), news['protocol_identity'],
             original['manifest'], original['cases_file'], original['asr_source_inventory'], original['material_source_inventory'],
             *original['configuration_files'], *original['models'].values(), original['hotwords'], derived['prepare_source_script'],
             ASR.file_identity(ROOT / 'requirements-w16-resampling.txt'),
             ASR.file_identity(ROOT.parent / 'ai-learning/docs/stages/E0-w16-repair-implementation.md')]
    files.extend(dependency_inputs)
    files.extend(derived['source_inputs_before'])
    for material in derived['home']:
        files.extend(material[k] for k in ('original_wav_identity', 'original_float_identity', 'resampled_float_identity'))
    for material in derived['news']:
        files.extend(material[k] for k in ('original_wav_identity', 'downsampled_float_identity'))
    files = list({v['path']: v for v in files if v is not None}.values())
    for item in files:
        ASR.check_file(item)
    p = {'schema_version': 1, 'kind': 'w16_frozen_repair_protocol', 'state': 'frozen', 'created_at': now(),
         'source_git': git, 'sources_sha256': DEV.sources(), 'dependencies': ASR.dependency_identity(),
         'matrix': matrix(), 'policy': policy(), 'original_protocol': original, 'news_protocol': news,
         'derived': derived, 'cases': original['cases'], 'configs': original['configs'],
         'models': original['models'], 'hotwords': original['hotwords'], 'frozen_files': files,
         'asr_weight_license_verified': False, 'human_home_accepted': False, 'personal_understanding_accepted': False,
         'preregistration': {'file': ASR.file_identity(ROOT.parent / 'ai-learning/docs/stages/E0-w16-repair-implementation.md'),
                             'root_commit': '5296c46478d1c2e288bad54b0bfe0a8391cade7a'}}
    validate_protocol(p)
    # Validate all saved arrays and their shape/rate before freezing any inference.
    for job in jobs(p):
        load_audio(job)
    write(args.outdir / 'protocol.json', p)
    return {'passed': True, 'observations': 60, 'native_asr_calls': 0, 'source_git': git}


def observe(job, p, asr, init_error=None):
    row = {'job': job, 'completed': False, 'asr_attempted': False, 'recognizer_output': None,
           'voice_normalized': None, 'audio': None, 'asr_metrics': None, 'text_metrics': None,
           'primary': None, 'primary_semantics_passed': None if job['group'] == 'news' else False,
           'timing': {}, 'errors': [], 'live_e2e_ms': None, 'live_overflow_count': None}
    context, stage = None, 'fixture'
    started_all = perf_counter(); started = started_all
    try:
        if job['group'] == 'home':
            context = HOME.fixture(job['case'], p['configs'])
        started_all = perf_counter()  # fixture/setup excluded
        stage, started = 'audio_input', perf_counter()
        samples, rate, row['audio'] = load_audio(job)
        row['timing']['pcm_read_convert_wall_ms'] = (perf_counter() - started) * 1000
        if init_error:
            stage = 'asr_initialization'
            raise RuntimeError(init_error)
        stage, started = 'asr_decode', perf_counter()
        row['asr_attempted'] = True
        recognition = asr.transcribe_waveform(samples, rate, job['chunk_ms'])
        row['timing']['asr_replay_wall_ms'] = (perf_counter() - started) * 1000
        row['asr_metrics'] = {'audio_seconds': recognition.audio_seconds, 'decode_seconds': recognition.decode_seconds}
        row['timing']['decode_only_ms'] = recognition.decode_seconds * 1000
        row['recognizer_output'] = recognition.text
        row['voice_normalized'] = _normalize_transcript(recognition.text)
        row['text_metrics'] = text_metrics(job['reference_text'], recognition.text)
        if context:
            stage, started = 'voice_control', perf_counter()
            result = context['voice'].process_recognition(recognition, report_empty=True)
            row['primary'] = HOME.finish_probe(context, result, wall_ms=(perf_counter() - started) * 1000)
        row['completed'] = True
    except Exception as error:
        row['errors'].append({'stage': getattr(error, 'stage', stage), 'code': getattr(error, 'code', None) or
                              ('ASR_INITIALIZATION_FAILED' if stage == 'asr_initialization' else 'VOICE_CHAIN_INTERNAL_ERROR'),
                              'detail': f'{type(error).__name__}: {error}'})
        if context and stage in ('audio_input', 'asr_decode', 'asr_initialization'):
            result = context['voice'].process_audio_error(error, stage='audio_input' if stage == 'audio_input' else 'asr_decode')
            row['primary'] = HOME.finish_probe(context, result, wall_ms=0.0)
    row['timing']['file_audio_to_readback_wall_ms'] = (perf_counter() - started_all) * 1000
    if row['primary']:
        primary = row['primary']
        row['timing'].update(voice_control_feedback_wall_ms=primary['control_feedback_wall_ms'],
                             explicit_final_readback_wall_ms=primary['final_snapshot_wall_ms'],
                             console_feedback_capture_wall_ms=primary['feedback_capture_wall_ms'])
        primary['checks'] = HOME.check_probe(primary, job['case'])
        row['primary_semantics_passed'] = all(primary['checks'].values())
        if primary['error_code']:
            row['errors'].append({'stage': primary.get('error_stage') or ('asr_output' if primary['error_code'] == 'ASR_EMPTY_TRANSCRIPT' else 'intent_or_device'),
                                  'code': primary['error_code'], 'detail': primary['status']})
        if not row['primary_semantics_passed']:
            row['errors'].append({'stage': 'semantic', 'code': 'AUDIO_TO_STATE_ORACLE_MISMATCH', 'detail': 'Frozen oracle mismatch'})
    if row['text_metrics'] and not row['text_metrics']['exact_match']:
        row['errors'].append({'stage': 'transcript', 'code': 'ASR_TRANSCRIPT_MISMATCH', 'detail': 'Frozen normalization/reference mismatch'})
    return row


def group_summary(rows):
    metrics = [r['text_metrics'] for r in rows if r['text_metrics'] and r['job']['reference_text']]
    edits = sum(m['character_edits'] for m in metrics); characters = sum(m['reference_characters'] for m in metrics)
    home = [r for r in rows if r['job']['group'] == 'home']; abnormal = [r for r in home if r['job']['case']['category'] == 'abnormal']
    return {'observations': len(rows), 'completed': sum(r['completed'] for r in rows),
            'speech_scored': len(metrics), 'speech_exact': sum(m['exact_match'] for m in metrics),
            'character_edits': edits, 'reference_characters': characters, 'cer': edits / characters if characters else None,
            'home_semantics_passed': sum(bool(r['primary_semantics_passed']) for r in home),
            'normal_semantics_passed': sum(bool(r['primary_semantics_passed']) for r in home if r['job']['case']['category'] == 'normal'),
            'abnormal_semantics_passed': sum(bool(r['primary_semantics_passed']) for r in abnormal),
            'abnormal_state_preserved': sum(bool(r['primary'] and r['primary']['before'] == r['primary']['after']) for r in abnormal),
            'primary_mock_execute_attempts': sum(len(r['primary']['execution_records']) for r in home if r['primary']),
            'error_counts': dict(Counter(e['stage'] + ':' + e['code'] for r in rows for e in r['errors'])),
            'latency_ms': {key: {'n': len(v := [r['timing'][key] for r in rows if key in r['timing']]),
                                  'p50': ASR.percentile(v, .5), 'p95': ASR.percentile(v, .95)} for key in HOME.TIMING_KEYS}}


def aggregate(p, rows, references, execution):
    expected = jobs(p)
    by_arm = {a['id']: group_summary([r for r in rows if r['job']['arm'] == a['id']]) for a in p['matrix']}
    gates = {'exact_job_order_count': [r['job'] for r in rows] == expected,
             'all_audio_and_asr_attempts_complete': len(rows) == 60 and all(r['completed'] and r['asr_attempted'] for r in rows),
             'independent_reference_controls_complete': len(references) == 9 and all(r['probe'] and all(r['probe']['checks'].values()) for r in references),
             'returned_state_matches_readback': all(r['primary']['checks']['returned_matches_explicit_readback'] for r in rows if r['primary']),
             'zero_network_attempts': not execution['network_attempts'], 'no_global_failure': execution.get('error') is None,
             'inputs_unchanged': execution.get('frozen_inputs_preserved') is True}
    contrasts = {}
    for b, a in [('Q', 'A'), ('R', 'A'), ('T', 'A'), ('C', 'A'), ('H8', 'H16')]:
        av, bv = by_arm[a], by_arm[b]
        contrasts[b + '-' + a] = {'character_edit_delta': bv['character_edits'] - av['character_edits'],
                                 'cer_delta': bv['cer'] - av['cer'] if bv['cer'] is not None and av['cer'] is not None else None,
                                 'speech_exact_delta': bv['speech_exact'] - av['speech_exact'],
                                 'changed_transcripts': sum(x['recognizer_output'] != y['recognizer_output'] for x, y in zip(
                                     [r for r in rows if r['job']['arm'] == a], [r for r in rows if r['job']['arm'] == b]))}
    development = {name: v['observations'] == 10 and v['home_semantics_passed'] == 10 and v['abnormal_state_preserved'] == 5
                   for name, v in by_arm.items() if name not in ('H16', 'H8')}
    return {'schema_version': 1, 'kind': 'w16_repair_one_factor_results', 'engineering_evidence_passed': all(gates.values()),
            'passed': all(gates.values()), 'engineering_gates': gates, 'runtime_source_commit': p['source_git']['commit'],
            'execution_kind': execution['execution_kind'], 'by_arm': by_arm, 'contrasts': contrasts,
            'development_semantic_gate': development,
            'asr_edit_reduction_vs_A': {a: by_arm[a]['character_edits'] < by_arm['A']['character_edits'] for a in ('Q', 'R', 'T', 'C')},
            'automatic_product_promotion': False, 'human_home_accepted': False, 'personal_understanding_accepted': False,
            'asr_weight_license_verified': False, 'live_e2e_ms': None, 'overflow_resolution_accepted': False,
            'real_ha_accepted': False, 'audible_feedback_accepted': False, 'retries': 0, 'warmups': 0,
            'asr_attempts': sum(r['asr_attempted'] for r in rows), 'independent_text_oracle_matches': sum(bool(r['probe']) and all(r['probe']['checks'].values()) for r in references),
            'model_loads': execution['model_loads'], 'network_attempts': len(execution['network_attempts']),
            'latency_policy': 'Offline different clips once; descriptive percentiles, no performance ranking. Nested decode/console timings cannot be added; model/fixture/reference excluded. PCM-named IO includes NPY load/validation for derived arms.',
            'limitations': ['Original W16 42/73 and combined-style 43/73 failures remain unchanged.',
                            'All home inputs reuse first synthetic WAV/float; no regenerated TTS or label repair.',
                            'R changes the resampler path only and cannot restore missing >4kHz information.',
                            'H16/H8 are licensed public news, not human household commands or semantic controls.',
                            'Every contrast changes one declared factor relative to shared A; no repeated output-driven search.',
                            'Native ASR text, existing Voice/Mock state and independent reference controls remain distinct.']}


def markdown(s):
    lines = ['# W16 predeclared one-factor replay', '', 'Engineering evidence passed: ' + str(s['passed']),
             'Automatic product promotion: False', '', '| Arm | Completed | Speech Exact | CER | Normal semantics | Abnormal semantics | Abnormal state held |', '|---|---|---|---|---|---|---|']
    for a, v in s['by_arm'].items():
        lines.append(f"| {a} | {v['completed']}/{v['observations']} | {v['speech_exact']}/{v['speech_scored']} | {v['cer']} | {v['normal_semantics_passed']} | {v['abnormal_semantics_passed']} | {v['abnormal_state_preserved']} |")
    return '\n'.join(lines + ['', s['latency_policy'], ''] + ['- ' + v for v in s['limitations']]) + '\n'


def run(args, asr_factory=None):
    args.outdir.mkdir(parents=True, exist_ok=False)
    p = read(args.protocol); validate_protocol(p, current=True)
    write(args.outdir / 'protocol.json', p)
    (args.outdir / 'records').mkdir(); (args.outdir / 'references').mkdir()
    execution = {'started_at': now(), 'source_git': DEV.git(), 'execution_kind': 'fake_asr_unit_fixture' if asr_factory else 'native_asr',
                 'network_attempts': [], 'model_loads': [], 'error': None, 'frozen_inputs_preserved': False}
    rows, references = [], []
    with network_disabled() as attempts:
        try:
            with (args.outdir / 'ledger.jsonl').open('x') as ledger:
                for arm in p['matrix']:
                    runtime = deepcopy(p); runtime['configs']['audio']['asr']['tail_padding_seconds'] = arm['tail_seconds']
                    asr, init_error = None, None; started = perf_counter()
                    try:
                        asr = (asr_factory or ASR.native_asr)(runtime)
                    except Exception as error:
                        init_error = f'{type(error).__name__}: {error}'
                    execution['model_loads'].append({'arm': arm['id'], 'wall_ms': (perf_counter() - started) * 1000, 'error': init_error})
                    for job in [j for j in jobs(p) if j['arm'] == arm['id']]:
                        i = len(rows); ledger.write(json.dumps({'event': 'observation_started', 'index': i, 'arm': job['arm'], 'id': job['id']}) + '\n'); ledger.flush()
                        row = observe(job, p, asr, init_error)
                        rows.append(row); write(args.outdir / 'records' / f'{i:04d}.json', row)
                        ledger.write(json.dumps({'event': 'observation_recorded', 'index': i, 'arm': job['arm'], 'id': job['id'], 'completed': row['completed']}) + '\n'); ledger.flush()
                    del asr; gc.collect()
                for case in p['cases']:
                    if not case['reference_text']: continue
                    i = len(references); ledger.write(json.dumps({'event': 'reference_started', 'index': i, 'id': case['id']}) + '\n'); ledger.flush()
                    ref = {'case': case, 'probe': None, 'error': None}
                    try:
                        ref['probe'] = HOME.reference_probe(case, p['configs'])
                    except Exception as error:
                        ref['error'] = f'{type(error).__name__}: {error}'
                    references.append(ref); write(args.outdir / 'references' / f'{i:04d}.json', ref)
                    ledger.write(json.dumps({'event': 'reference_recorded', 'index': i, 'id': case['id'], 'completed': ref['probe'] is not None}) + '\n'); ledger.flush()
        except Exception as error:
            execution['error'] = {'type': type(error).__name__, 'detail': str(error)}
        execution['network_attempts'] = list(attempts)
    try:
        for item in p['frozen_files']: ASR.check_file(item)
        if DEV.git() != p['source_git'] or DEV.sources() != p['sources_sha256'] or ASR.dependency_identity() != p['dependencies']:
            raise ValueError('Source/native identities changed during replay')
        execution['frozen_inputs_preserved'] = True
        execution['runtime_identities_preserved'] = True
    except Exception as error:
        execution['integrity_error'] = f'{type(error).__name__}: {error}'
    execution['finished_at'] = now(); write(args.outdir / 'execution.json', execution)
    paths = [args.outdir / n for n in ('protocol.json', 'execution.json', 'ledger.jsonl')]
    paths += sorted((args.outdir / 'records').glob('*.json')) + sorted((args.outdir / 'references').glob('*.json'))
    write(args.outdir / 'raw-index.json', {'artifacts': [{'path': str(f.relative_to(args.outdir)), 'bytes': f.stat().st_size, 'sha256': ASR.sha256(f)} for f in paths]})
    summary = aggregate(p, rows, references, execution); write(args.outdir / 'summary.json', summary)
    (args.outdir / 'report.md').write_text(markdown(summary)); return summary


def validate_observation(row):
    job = row['job']
    if type(row['completed']) is not bool or type(row['asr_attempted']) is not bool:
        raise ValueError('Observation completion/attempt flags must be boolean')
    for value in row['timing'].values():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise ValueError('Recorded timing must be finite nonnegative milliseconds')
    if row['completed']:
        needed = {'pcm_read_convert_wall_ms', 'asr_replay_wall_ms', 'decode_only_ms', 'file_audio_to_readback_wall_ms'}
        if job['group'] == 'home': needed.update(HOME.TIMING_KEYS)
        if (not row['asr_attempted'] or not isinstance(row['recognizer_output'], str)
                or not row['audio'] or not row['asr_metrics'] or row['text_metrics'] is None
                or not needed <= row['timing'].keys() or (job['group'] == 'home' and row['primary'] is None)):
            raise ValueError('Completed observations require actual ASR output, metrics and applicable readback/timings')
    if row['recognizer_output'] is None:
        if row['text_metrics'] is not None or row['voice_normalized'] is not None or row['asr_metrics'] is not None:
            raise ValueError('Missing recognizer output cannot retain fabricated transcript metrics')
    else:
        if not row['asr_attempted'] or not isinstance(row['recognizer_output'], str):
            raise ValueError('Recognizer output requires a real recorded attempt')
        if (row['text_metrics'] != text_metrics(job['reference_text'], row['recognizer_output'])
                or row['voice_normalized'] != _normalize_transcript(row['recognizer_output'])):
            raise ValueError('Transcript metrics/normalization differ')
        metrics = row['asr_metrics']
        if (not isinstance(metrics, dict) or metrics.get('audio_seconds') != job['frames'] / job['rate']
                or not isinstance(metrics.get('decode_seconds'), (int, float)) or isinstance(metrics['decode_seconds'], bool)
                or not math.isfinite(metrics['decode_seconds']) or metrics['decode_seconds'] < 0):
            raise ValueError('ASR duration/decode metrics differ from frozen input')


def report(args):
    args.outdir.mkdir(parents=True, exist_ok=False)
    names = {'protocol.json', 'execution.json', 'ledger.jsonl'}
    names.update(str(f.relative_to(args.run)) for folder in ('records', 'references') for f in (args.run / folder).glob('*.json'))
    index = read(args.run / 'raw-index.json')['artifacts']
    if len(index) != len(names) or {a['path'] for a in index} != names:
        raise ValueError('Raw index must cover exact protocol/execution/ledger/record/reference set')
    for a in index:
        f = args.run / a['path']
        if f.stat().st_size != a['bytes'] or ASR.sha256(f) != a['sha256']:
            raise ValueError('Raw evidence differs')
    p = read(args.run / 'protocol.json'); validate_protocol(p)
    rows = [read(f) for f in sorted((args.run / 'records').glob('*.json'))]
    references = [read(f) for f in sorted((args.run / 'references').glob('*.json'))]
    expected = jobs(p)
    ledger = []
    for i, r in enumerate(rows):
        if i >= len(expected) or r['job'] != expected[i]: raise ValueError('Job source/order differs')
        job = r['job']
        validate_observation(r)
        if job['group'] == 'home':
            primary = r['primary']
            checks = HOME.check_probe(primary, job['case']) if primary else None
            if primary and primary['checks'] != checks: raise ValueError('Primary state oracle differs')
            if r['primary_semantics_passed'] != bool(primary and all(checks.values())): raise ValueError('Primary semantic score differs')
        elif r['primary'] is not None or r['primary_semantics_passed'] is not None:
            raise ValueError('News must not become Mock/home control')
        ledger += [{'event': 'observation_started', 'index': i, 'arm': job['arm'], 'id': job['id']},
                   {'event': 'observation_recorded', 'index': i, 'arm': job['arm'], 'id': job['id'], 'completed': r['completed']}]
    ref_cases = [c for c in p['cases'] if c['reference_text']]
    for i, r in enumerate(references):
        if i >= len(ref_cases) or r['case'] != ref_cases[i]: raise ValueError('Reference order/case differs')
        probe = r['probe']
        if probe and probe['checks'] != HOME.check_probe(probe, r['case']): raise ValueError('Reference state oracle differs')
        ledger += [{'event': 'reference_started', 'index': i, 'id': r['case']['id']},
                   {'event': 'reference_recorded', 'index': i, 'id': r['case']['id'], 'completed': probe is not None}]
    actual_ledger = [json.loads(line) for line in (args.run / 'ledger.jsonl').read_text().splitlines()]
    if actual_ledger != ledger: raise ValueError('Ledger is incomplete, reordered or changed')
    summary = aggregate(p, rows, references, read(args.run / 'execution.json'))
    if summary != read(args.run / 'summary.json'):
        raise ValueError('Recorded summary differs from recomputed observations')
    write(args.outdir / 'summary.json', summary); (args.outdir / 'report.md').write_text(markdown(summary)); return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__); sub = parser.add_subparsers(dest='operation', required=True)
    prep = sub.add_parser('prepare'); prep.add_argument('--original-run', type=Path, required=True)
    prep.add_argument('--news-protocol', type=Path, required=True); prep.add_argument('--derived-manifest', type=Path, required=True)
    execute = sub.add_parser('run'); execute.add_argument('--protocol', type=Path, required=True)
    rebuild = sub.add_parser('report'); rebuild.add_argument('--run', type=Path, required=True)
    for command in (prep, execute, rebuild): command.add_argument('--outdir', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = {'prepare': prepare, 'run': run, 'report': report}[args.operation](args)
    except Exception as error:
        print(json.dumps({'passed': False, 'error': f'{type(error).__name__}: {error}'}), file=sys.stderr); return 2
    print(json.dumps(result, ensure_ascii=False, indent=2)); return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
