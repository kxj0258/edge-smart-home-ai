"""Frozen Chinese TTS frontend A/B, saved speech and separate ASR/Mock evidence.

Each arm is generated in its own worker process once. Native words, reconstructed
lexicon phones, generated audio, ASR output and human listening stay separate.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import csv
import json
import math
import os
import re
from pathlib import Path
import subprocess
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / 'src'):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
from scripts import evaluate_w16_voice_home as HOME
from scripts import prepare_w16_audio as AUDIO
from scripts import w16_tts_pronunciation_worker as WORKER
from scripts.run_unit_tests import network_disabled
from edge_smart_home.audio.pipeline import _normalize_transcript
from edge_smart_home.benchmark.asr_evaluation import text_metrics

ASR, DEV = HOME.ASR, HOME.DEV
read, write, now = HOME.read, HOME.write, HOME.now
FIXTURE_SHA = 'edd9c02dc8e0e1d7a07633597c7f42639f34e9dc3213f8ad02a04f13b894a522'
PREREG_COMMIT = '54c48dc8b006d0d91595b62a772c4a08dc590dca'


def analysis_module():
    from scripts import w16_pronunciation_analysis
    return w16_pronunciation_analysis


def policy():
    return {'arms': ['A', 'B'], 'texts_per_arm': 18, 'home_per_arm': 10,
            'focal_oracles_per_arm': 15, 'tts_attempts': 36, 'asr_attempts': 36,
            'reference_text_controls': 10, 'attempts_per_arm_text': 1,
            'warmups': 0, 'retries': 0, 'factor': 'only add official rule.far in B',
            'new_zero_noise_domain_not_old_waveform_reproduction': True,
            'native_phone_tensor_captured': False, 'human_listening_accepted': False,
            'automatic_product_promotion': False}


def file_check(item):
    ASR.check_file(item)


def original_guard(guard_path):
    guard = read(guard_path)
    if guard.get('kind') != 'w16_pronunciation_original_data_guard':
        raise ValueError('Expected current pronunciation original-data guard')
    values = guard.get('immutable_artifacts')
    if not isinstance(values, list) or not values:
        raise ValueError('Old data guard must list immutable original data identities')
    base = Path(guard.get('path_base', ROOT.parent))
    result = []
    for item in values:
        path = Path(item['path'])
        path = path if path.is_absolute() else base / path
        actual = ASR.file_identity(path)
        if actual['bytes'] != item['bytes'] or actual['sha256'] != item['sha256']:
            raise ValueError('Original failed data differs: ' + str(path))
        result.append(actual)
    return result


def validate_fixture(value):
    if (value.get('schema_version') != 1 or value.get('kind') != 'w16_pronunciation_cases'
            or value.get('common_tts') != {k: WORKER.EXPECTED[k] for k in (
                'speaker_id', 'speed', 'num_threads', 'provider', 'sample_rate',
                'num_speakers', 'noise_scale', 'noise_scale_w', 'debug', 'silence_scale')}):
        raise ValueError('Expected unchanged independent pronunciation fixture/settings')
    home, probes = value.get('home_cases', []), value.get('pronunciation_probes', [])
    HOME.validate_cases({'schema_version': 1, 'kind': 'w16_voice_home_cases', 'cases': home})
    if len(home) != 10 or len(probes) != 8 or sum(c['category'] == 'normal' for c in home) != 5:
        raise ValueError('Exactly five normal/five abnormal/eight pronunciation probes required')
    cases = home + probes
    if len({c['id'] for c in cases}) != 18 or len({c['reference_text'] for c in cases}) != 18:
        raise ValueError('Unique eighteen case IDs and literal input texts required')
    if ([p['category'] for p in probes] != ['polyphone_probe'] * 6 + ['neutral_control'] * 2
            or any(p.get('state_or_semantics_evaluated') is not False or 'expect' in p for p in probes)):
        raise ValueError('Pronunciation-only probes must never be household controls')
    for case in cases:
        if not case['reference_text'] or '\n' in case['reference_text'] or '\r' in case['reference_text']:
            raise ValueError('Nonempty single-line literal input required')
        for focal in case['focal_pronunciation']:
            start, end = focal['char_start'], focal['char_end']
            if (type(start) is not int or type(end) is not int or not 0 <= start < end <= len(case['reference_text'])
                    or case['reference_text'][start:end] != focal['text'] or not focal['expected_phones']):
                raise ValueError('Fixed raw-character focal span/phone oracle differs')
    if sum(len(c['focal_pronunciation']) for c in cases) != 15:
        raise ValueError('Exactly fifteen predeclared focal checks required per arm')
    return cases


def validate_protocol(p, *, current=False):
    WORKER.validate_protocol(p, current=False)
    if (p.get('policy') != policy() or p.get('case_fixture', {}).get('sha256') != FIXTURE_SHA
            or p.get('fixture') != read(ROOT / 'configs/w16-pronunciation-cases-v1.json')
            or p.get('cases') != validate_fixture(p['fixture'])):
        raise ValueError('Frozen fixture, order or predeclared policy differs')
    original = p['original_w16_protocol']
    if (p['configs'] != original['configs'] or p['models'] != original['models']
            or p['hotwords'] != original['hotwords'] or p['dependencies'] != original['dependencies']
            or p['preregistration']['root_commit'] != PREREG_COMMIT):
        raise ValueError('ASR model/config/hotwords/native or preregistration identity changed')
    if current:
        if DEV.git() != p['source_git'] or DEV.sources() != p['sources_sha256'] or ASR.dependency_identity() != p['dependencies']:
            raise ValueError('Exact clean frozen source/native runtime required')
        for item in [*p['model_files'], *p['frozen_inputs']]:
            file_check(item)


def prepare(args):
    args.outdir.mkdir(parents=True, exist_ok=False)
    source = DEV.git()
    if source['status']:
        raise ValueError('Prepare requires clean committed child sources')
    fixture_id = ASR.file_identity(args.cases)
    if fixture_id['sha256'] != FIXTURE_SHA:
        raise ValueError('Predeclared independent input fixture changed')
    fixture = read(args.cases); cases = validate_fixture(fixture)
    original = read(args.original_protocol); HOME.validate_protocol(original)
    inventory = read(args.source_inventory); model = inventory['tts']; model_dir = Path(model['model_dir']).resolve()
    if (model['license'] != 'Apache-2.0' or model['source_data_license'] != 'Apache-2.0'
            or len(model['files']) != 9):
        raise ValueError('Frozen TTS/model/data provenance required')
    models = [{**item} for item in model['files']]
    # Only literal file identity fields enter the worker, not upstream metadata.
    models = [{k: item[k] for k in ('path', 'bytes', 'sha256')} for item in models]
    frozen = [fixture_id, ASR.file_identity(args.original_protocol), ASR.file_identity(args.source_inventory),
              ASR.file_identity(args.old_data_guard), ASR.file_identity(args.preregistration),
              *original_guard(args.old_data_guard), original['cases_file'], original['manifest'],
              original['asr_source_inventory'], original['material_source_inventory'],
              *original['configuration_files'], *original['models'].values(), original['hotwords'], *models,
              *original['dependencies']['sherpa_native_binaries'], *original['dependencies']['bundled_platform_libraries']]
    frozen = list({v['path']: v for v in frozen if v is not None}.values())
    for item in frozen: file_check(item)
    p = {'schema_version': 1, 'kind': 'w16_frozen_pronunciation_protocol', 'state': 'frozen',
         'created_at': now(), 'source_git': source, 'sources_sha256': DEV.sources(),
         'dependencies': ASR.dependency_identity(), 'policy': policy(), 'settings': deepcopy(WORKER.EXPECTED),
         'case_fixture': fixture_id, 'fixture': fixture, 'cases': cases,
         'model_dir': str(model_dir), 'model_files': models, 'tts_source_inventory': ASR.file_identity(args.source_inventory),
         'original_w16_protocol': original, 'configs': original['configs'], 'models': original['models'],
         'hotwords': original['hotwords'], 'frozen_inputs': frozen,
         'preregistration': {'file': ASR.file_identity(args.preregistration), 'root_commit': PREREG_COMMIT},
         'asr_weight_license_verified': False, 'human_home_accepted': False,
         'personal_understanding_accepted': False, 'human_listening_accepted': False}
    validate_protocol(p, current=True)
    write(args.outdir / 'protocol.json', p)
    return {'passed': True, 'cases': 18, 'home_cases': 10, 'focal_oracles_per_arm': 15,
            'tts_calls': 0, 'asr_calls': 0, 'source_git': source}


def subprocess_worker(p, protocol_path, arm, outdir, logdir):
    stdout = logdir / (arm + '.stdout'); stderr = logdir / (arm + '.stderr')
    command = [sys.executable, str(ROOT / 'scripts/w16_tts_pronunciation_worker.py'),
               '--protocol', str(protocol_path), '--arm', arm, '--outdir', str(outdir)]
    start = perf_counter()
    with stdout.open('xb') as output, stderr.open('xb') as error:
        process = subprocess.run(command, cwd=ROOT, env=dict(os.environ, PYTHONPATH=str(ROOT / 'src')),
                                 stdout=output, stderr=error, check=False)
    return {'arm': arm, 'argv': command, 'returncode': process.returncode,
            'wall_ms': (perf_counter() - start) * 1000,
            'stdout': ASR.file_identity(stdout), 'stderr': ASR.file_identity(stderr)}


def verify_raw_index(directory):
    index = read(directory / 'raw-index.json')['artifacts']
    actual = {str(p.relative_to(directory)) for p in directory.rglob('*') if p.is_file() and p.name != 'raw-index.json'}
    # Worker indices contain every file. Coordinator indices deliberately omit derived summaries.
    if len(index) != len(actual) or {v['path'] for v in index} != actual:
        raise ValueError('Worker raw index must cover exact artifact set')
    for value in index:
        file = directory / value['path']
        if file.stat().st_size != value['bytes'] or ASR.sha256(file) != value['sha256']:
            raise ValueError('Worker raw artifact differs')
    return index


def validate_worker_row(row, case, arm):
    if any(type(row.get(k)) is not bool for k in ('generation_attempted', 'generated_audio_saved', 'completed')):
        raise ValueError('Worker completion/attempt flags must be literal booleans')
    if row['case'] != case or row['id'] != case['id'] or row['arm'] != arm or row['reference_text'] != case['reference_text']:
        raise ValueError('Worker changed a frozen case/label/order')
    if row['source_kind'] != 'synthetic_audio':
        raise ValueError('Generated speech cannot become human material')
    if row['native_stderr']:
        file_check(row['native_stderr'])
        trace = WORKER.parse_native_trace(Path(row['native_stderr']['path']), case['reference_text'], arm)
        if (trace != row['native_trace'] or row['actual_native_words'] != (trace['word_splits'][0] if len(trace['word_splits']) == 1 else None)
                or row['actual_native_raw_text'] != (trace['raw_texts'][0] if len(trace['raw_texts']) == 1 else None)
                or row['actual_native_normalization_steps'] != trace['normalization_steps']):
            raise ValueError('Worker native trace/words differ from actual native stderr')
    elif row['native_trace'] is not None or row['actual_native_words'] is not None:
        raise ValueError('No trace may invent native words')
    if row['generated_audio_saved']:
        import numpy as np
        file_check(row['source_float']); file_check(row['pcm16_wav'])
        samples = np.load(row['source_float']['path'], allow_pickle=False)
        if samples.dtype != np.dtype('float32'): raise ValueError('Original generated float32 must be preserved')
        _, wav, conversion = AUDIO.quantize_pcm16(samples, 8000)
        if Path(row['pcm16_wav']['path']).read_bytes() != wav or row['conversion'] != conversion:
            raise ValueError('Generated float/PCM conversion evidence differs')
    value = row['tts_generation_wall_ms']
    if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0):
        raise ValueError('TTS observed generation time must be finite nonnegative milliseconds')
    if row['completed'] and (value is None or not row['generation_attempted'] or not row['generated_audio_saved']
                              or not row['native_trace'] or not row['native_trace']['consistent']
                              or row['native_trace']['oov'] or row['error'] is not None):
        raise ValueError('Completed synthesis requires actual waveform and complete native trace')



def worker_evidence(directory, p, arm, *, formal=False):
    verify_raw_index(directory)
    if read(directory / 'protocol.json') != p:
        raise ValueError('Worker must preserve the exact protocol, not a new configuration')
    paths = sorted((directory / 'records').glob('*.json'))
    if [f.name for f in paths] != [f'{i:04d}.json' for i in range(18)]:
        raise ValueError('Worker must preserve all eighteen records in frozen order')
    rows = [read(f) for f in paths]
    for row, case in zip(rows, p['cases']): validate_worker_row(row, case, arm)
    ledger = []
    for index, row in enumerate(rows):
        ledger += [{'event': 'case_started', 'index': index, 'id': row['id'], 'arm': arm},
                   {'event': 'case_recorded', 'index': index, 'id': row['id'], 'arm': arm, 'completed': row['completed']}]
    if [json.loads(v) for v in (directory / 'ledger.jsonl').read_text().splitlines()] != ledger:
        raise ValueError('Worker ledger must retain exactly one attempt in frozen order')
    execution = read(directory / 'execution.json')
    if (execution['arm'] != arm or execution['generation_attempts'] != sum(r['generation_attempted'] for r in rows)
            or execution['completed'] != sum(r['completed'] for r in rows)
            or execution['retries'] != 0 or any(execution[k] != 0 for k in ('asr_calls', 'control_calls', 'playback_calls', 'microphone_calls'))
            or execution['human_listening_accepted'] is not False or execution['acoustic_pronunciation_accepted'] is not False):
        raise ValueError('Worker attempt counts or side-effect/acceptance scope changed')
    expected_passed = (execution['generation_attempts'] == 18 and execution['completed'] == 18
                       and execution['model_init_error'] is None and not execution['network_attempts']
                       and execution['frozen_inputs_and_runtime_preserved'])
    if (type(execution['passed']) is not bool or execution['passed'] != expected_passed
            or execution['source_git'] != p['source_git']
            or execution['input_identities_before'] != [*p['model_files'], *p['frozen_inputs']]):
        raise ValueError('Worker verdict or frozen source/input binding differs')
    if execution['frozen_inputs_and_runtime_preserved'] and execution['input_identities_after'] != execution['input_identities_before']:
        raise ValueError('Worker preserved-input verdict differs from observed identities')
    if formal and execution['execution_kind'] != 'native_tts':
        raise ValueError('A Fake worker must not become formal speech evidence')
    return execution


def validate_listening_packet(packet, p, rows):
    if packet is None: return
    directory = Path(packet['path'])
    for item in packet['artifacts']: file_check(item)
    actual = {str(f.resolve()) for f in directory.rglob('*') if f.is_file()}
    if (len(packet['artifacts']) != len(actual)
            or {str(Path(v['path']).resolve()) for v in packet['artifacts']} != actual
            or packet['human_listening_accepted'] is not False or packet['autoplay'] is not False):
        raise ValueError('Listening packet may not omit files or invent human acceptance/autoplay')
    mapping = read(directory / 'mapping-key.json')['mapping']
    ratings = list(csv.DictReader((directory / 'ratings.csv').open()))
    if len(mapping) != 36 or len(ratings) != 36 or packet['ratings_rows'] != 36:
        raise ValueError('Retain all thirty-six unselected listening slots')
    lookup = {(r['arm'], r['id']): r for r in rows}; copied = 0
    for i, (item, rating) in enumerate(zip(mapping, ratings)):
        case = p['cases'][i // 2]; slot = 'X' if i % 2 == 0 else 'Y'
        arm = ('A', 'B')[i % 2] if (i // 2) % 2 == 0 else ('B', 'A')[i % 2]
        key = f'review-{i // 2 + 1:03d}-{slot}'; source = lookup[(arm, case['id'])]['pcm16_wav']
        if (item['review_id'] != key or item['case_id'] != case['id'] or item['slot'] != slot or item['arm'] != arm
                or item['source_wav'] != source or rating['review_id'] != key or rating['case_id'] != case['id']
                or rating['slot'] != slot or rating['reference_text'] != case['reference_text']
                or any(rating[k] != '' for k in ('heard_words', 'heard_target_pinyin', 'pronunciation_correct',
                                                'intelligibility_score', 'reviewer', 'reviewed_at', 'comments'))):
            raise ValueError('Listening mapping, labels or pending human fields changed')
        if source:
            file_check(item['copied_wav']); copied += 1
            if item['copied_wav']['sha256'] != source['sha256'] or rating['status'] != 'pending':
                raise ValueError('Listening audio must be an unchanged copy with pending human verdict')
        elif item['copied_wav'] is not None or rating['status'] != 'missing_audio':
            raise ValueError('Missing speech must retain a missing listening slot')
    if packet['audio_copied'] != copied or re.search(r'<audio[^>]*\bautoplay(?:\s|=|>)', (directory / 'listen.html').read_text(), re.I):
        raise ValueError('Listening material may not autoplay or invent copied audio')


def observe(case, arm, p, worker, worker_identity, asr, asr_error, resources):
    home = case['category'] in ('normal', 'abnormal')
    row = {'arm': arm, 'id': case['id'], 'case': case, 'source_kind': 'synthetic_audio',
           'category': case['category'], 'completed': False, 'synthesis_completed': False,
           'asr_attempted': False, 'worker_record_identity': worker_identity,
           'source_float': None, 'pcm16_wav': None, 'actual_native_words': None, 'native_trace': None,
           'frontend': None, 'recognizer_output': None, 'voice_normalized': None,
           'asr_metrics': None, 'text_metrics': None, 'audio': None, 'primary': None,
           'primary_semantics_passed': False if home else None, 'timing': {}, 'errors': [],
           'live_e2e_ms': None, 'human_listening_accepted': False}
    context = None; start_all = perf_counter(); stage, started = 'synthesis_evidence', start_all
    try:
        if worker is None: raise ValueError('Worker did not retain this case')
        validate_worker_row(worker, case, arm)
        row.update(synthesis_completed=worker['completed'], source_float=worker['source_float'],
                   pcm16_wav=worker['pcm16_wav'], actual_native_words=worker['actual_native_words'],
                   native_trace=worker['native_trace'])
        row['timing']['tts_generation_wall_ms'] = worker['tts_generation_wall_ms']
        row['frontend'] = analysis_module().derive_frontend(case, worker['actual_native_words'], resources)
        if worker['error']:
            row['errors'].append({'stage': 'tts_frontend_or_generation', 'code': 'TTS_EVIDENCE_INCOMPLETE', 'detail': worker['error']})
        if not worker['generated_audio_saved']: raise ValueError('No generated waveform; no ASR retry or reference substitution')
        if home: context = HOME.fixture(case, p['configs'])
        start_all = perf_counter(); stage, started = 'audio_input', perf_counter()
        samples, rate, row['audio'] = ASR.read_pcm16_mono(Path(worker['pcm16_wav']['path']))
        row['timing']['pcm_read_convert_wall_ms'] = (perf_counter() - started) * 1000
        if asr_error:
            stage = 'asr_initialization'; raise RuntimeError(asr_error)
        stage, started = 'asr_decode', perf_counter(); row['asr_attempted'] = True
        recognition = asr.transcribe_waveform(samples, rate, p['configs']['audio']['chunk_duration_ms'])
        row['timing']['asr_replay_wall_ms'] = (perf_counter() - started) * 1000
        row['timing']['decode_only_ms'] = recognition.decode_seconds * 1000
        row['recognizer_output'] = recognition.text; row['voice_normalized'] = _normalize_transcript(recognition.text)
        row['asr_metrics'] = {'audio_seconds': recognition.audio_seconds, 'decode_seconds': recognition.decode_seconds}
        row['text_metrics'] = text_metrics(case['reference_text'], recognition.text)
        if home:
            stage, started = 'voice_control', perf_counter()
            result = context['voice'].process_recognition(recognition, report_empty=True)
            row['primary'] = HOME.finish_probe(context, result, wall_ms=(perf_counter() - started) * 1000)
        row['completed'] = bool(worker['completed'])
    except Exception as error:
        row['errors'].append({'stage': getattr(error, 'stage', stage),
                              'code': getattr(error, 'code', None) or 'PRONUNCIATION_OBSERVATION_FAILED',
                              'detail': f'{type(error).__name__}: {error}'})
        if context and stage in ('audio_input', 'asr_decode', 'asr_initialization'):
            result = context['voice'].process_audio_error(error, stage='audio_input' if stage == 'audio_input' else 'asr_decode')
            row['primary'] = HOME.finish_probe(context, result, wall_ms=0.0)
    row['timing']['file_audio_to_readback_wall_ms'] = (perf_counter() - start_all) * 1000
    if row['frontend'] is None:
        row['frontend'] = analysis_module().derive_frontend(case, row['actual_native_words'], resources)
    if row['primary']:
        a = row['primary']; a['checks'] = HOME.check_probe(a, case)
        row['primary_semantics_passed'] = all(a['checks'].values())
        row['timing'].update(voice_control_feedback_wall_ms=a['control_feedback_wall_ms'],
                             explicit_final_readback_wall_ms=a['final_snapshot_wall_ms'],
                             console_feedback_capture_wall_ms=a['feedback_capture_wall_ms'])
        if a['error_code']: row['errors'].append({'stage': a['error_stage'] or 'intent_or_device', 'code': a['error_code'], 'detail': a['status']})
        if not row['primary_semantics_passed']: row['errors'].append({'stage': 'semantic', 'code': 'AUDIO_TO_STATE_ORACLE_MISMATCH', 'detail': 'Frozen home oracle mismatch'})
    if row['text_metrics'] and not row['text_metrics']['exact_match']:
        row['errors'].append({'stage': 'transcript', 'code': 'ASR_TRANSCRIPT_MISMATCH', 'detail': 'Strict frozen reference differs'})
    return row


def summary_group(rows):
    metrics = [r['text_metrics'] for r in rows if r['text_metrics'] is not None]
    edits = sum(m['character_edits'] for m in metrics); characters = sum(m['reference_characters'] for m in metrics)
    home = [r for r in rows if r['category'] in ('normal', 'abnormal')]
    abnormal = [r for r in home if r['category'] == 'abnormal']
    focal = [r['frontend'] for r in rows]
    home_metrics = [r['text_metrics'] for r in home if r['text_metrics'] is not None]
    home_edits = sum(m['character_edits'] for m in home_metrics); home_characters = sum(m['reference_characters'] for m in home_metrics)
    return {'observations': len(rows), 'completed': sum(r['completed'] for r in rows),
            'tts_completed': sum(r['synthesis_completed'] for r in rows),
            'speech_scored': len(metrics), 'speech_exact': sum(m['exact_match'] for m in metrics),
            'character_edits': edits, 'reference_characters': characters, 'cer': edits / characters if characters else None,
            'home_speech_scored': len(home_metrics), 'home_speech_exact': sum(m['exact_match'] for m in home_metrics),
            'home_character_edits': home_edits, 'home_reference_characters': home_characters,
            'home_cer': home_edits / home_characters if home_characters else None,
            'home_semantics_passed': sum(bool(r['primary_semantics_passed']) for r in home),
            'normal_semantics_passed': sum(bool(r['primary_semantics_passed']) for r in home if r['category'] == 'normal'),
            'abnormal_semantics_passed': sum(bool(r['primary_semantics_passed']) for r in abnormal),
            'abnormal_state_preserved': sum(bool(r['primary'] and r['primary']['before'] == r['primary']['after']) for r in abnormal),
            'primary_mock_execute_attempts': sum(len(r['primary']['execution_records']) for r in home if r['primary']),
            'focal_total': sum(f['focal_total'] for f in focal), 'focal_observed': sum(f['focal_observed'] for f in focal),
            'focal_passed': sum(f['focal_passed'] for f in focal),
            'native_surface_preserved': sum(f['surface_preserved'] for f in focal),
            'error_counts': dict(Counter(e['stage'] + ':' + e['code'] for r in rows for e in r['errors'])),
            'latency_ms': {key: {'n': len(v := [r['timing'][key] for r in rows if r['timing'].get(key) is not None]),
                                  'p50': ASR.percentile(v, .5), 'p95': ASR.percentile(v, .95)} for key in ('tts_generation_wall_ms', *HOME.TIMING_KEYS)}}


def aggregate(p, rows, references, execution, packet):
    by = {arm: summary_group([r for r in rows if r['arm'] == arm]) for arm in ('A', 'B')}
    expected = [(arm, c['id']) for arm in ('A', 'B') for c in p['cases']]
    neutral = []
    for case in [c for c in p['cases'] if c['category'] == 'neutral_control']:
        values = [next((r for r in rows if r['id'] == case['id'] and r['arm'] == a), None) for a in ('A', 'B')]
        a, b = values
        entry = {'id': case['id'], 'derived_phones_equal': False, 'float_sha256_equal': False, 'wav_sha256_equal': False}
        if a and b:
            entry['derived_phones_equal'] = bool(a['frontend']['surface_preserved'] and b['frontend']['surface_preserved']
                                                  and not a['frontend']['oov_words'] and not b['frontend']['oov_words']
                                                  and not a['frontend']['alignment_errors'] and not b['frontend']['alignment_errors']
                                                  and a['frontend']['lexicon_derived_phones']
                                                  and a['frontend']['lexicon_derived_phones'] == b['frontend']['lexicon_derived_phones'])
            entry['float_sha256_equal'] = bool(a['source_float'] and b['source_float'] and a['source_float']['sha256'] == b['source_float']['sha256'])
            entry['wav_sha256_equal'] = bool(a['pcm16_wav'] and b['pcm16_wav'] and a['pcm16_wav']['sha256'] == b['pcm16_wav']['sha256'])
        entry['all_equal'] = all(entry[k] for k in ('derived_phones_equal', 'float_sha256_equal', 'wav_sha256_equal')); neutral.append(entry)
    gates = {'exact_order_and_count': [(r['arm'], r['id']) for r in rows] == expected,
             'both_workers_complete': ([v.get('arm') for v in execution['workers']] == ['A', 'B']
                                       and all(v.get('passed') for v in execution['workers'])
                                       and [v.get('arm') for v in execution['worker_subprocesses']] == ['A', 'B']
                                       and all(v.get('returncode') == 0 for v in execution['worker_subprocesses'])),
             'all_audio_and_asr_complete': len(rows) == 36 and all(r['completed'] and r['asr_attempted'] for r in rows),
             'independent_text_controls_complete': len(references) == 10 and all(r['probe'] and all(r['probe']['checks'].values()) for r in references),
             'returned_state_matches_readback': all(r['primary']['checks']['returned_matches_explicit_readback'] for r in rows if r['primary']),
             'zero_network_attempts': not execution['network_attempts'] and all(not w.get('network_attempts') for w in execution['workers']),
             'runtime_inputs_preserved': execution.get('runtime_inputs_preserved') is True,
             'no_global_failure': execution.get('error') is None,
             'complete_listening_packet': packet is not None and packet['ratings_rows'] == 36 and packet['audio_copied'] == 36 and packet['human_listening_accepted'] is False}
    phone_gates = {a: by[a]['focal_total'] == 15 and by[a]['focal_observed'] == 15 and by[a]['focal_passed'] == 15 for a in by}
    home_gates = {a: by[a]['home_semantics_passed'] == 10 and by[a]['abnormal_state_preserved'] == 5 for a in by}
    return {'schema_version': 1, 'kind': 'w16_pronunciation_ab_results', 'passed': all(gates.values()),
            'engineering_evidence_passed': all(gates.values()), 'engineering_gates': gates,
            'runtime_source_commit': p['source_git']['commit'], 'execution_kind': execution['execution_kind'],
            'by_arm': by, 'native_word_lexicon_focal_gate': phone_gates, 'home_semantic_gate': home_gates,
            'neutral_controls': neutral, 'zero_noise_numeric_controls_passed': len(neutral) == 2 and all(v['all_equal'] for v in neutral),
            'development_candidate_gate': {a: all(gates.values()) and phone_gates[a] and home_gates[a]
                                               and all(v['all_equal'] for v in neutral) for a in by},
            'independent_text_oracle_matches': sum(bool(r['probe']) and all(r['probe']['checks'].values()) for r in references),
            'tts_generation_attempts': sum(v.get('generation_attempts', 0) for v in execution['workers']),
            'asr_attempts': sum(r['asr_attempted'] for r in rows), 'network_attempts': len(execution['network_attempts']),
            'human_listening_accepted': False, 'actual_acoustic_pronunciation_accepted': False,
            'human_household_material_accepted': False, 'personal_understanding_accepted': False,
            'automatic_product_promotion': False, 'asr_weight_license_verified': False,
            'live_e2e_ms': None, 'overflow_resolution_accepted': False, 'real_ha_accepted': False,
            'physical_completion_accepted': False, 'audible_feedback_accepted': False,
            'warmups': 0, 'retries': 0, 'latency_policy': 'Different independent synthetic texts once each. TTS generation/model load separate from offline file replay; IO/decode/Voice/Mock/console nested stages are not a repeated benchmark or live/acoustic latency.',
            'limitations': ['New controlled zero-noise development material; original W16 failed scores/first WAVs remain unchanged.',
                            'Native words are captured, phones reconstructed through the frozen original lexicon are derived, not a captured TTS input tensor.',
                            'A phone oracle match, generated waveform or ASR transcript is not a human acoustic pronunciation/intelligibility verdict.',
                            'Probe words never execute Mock; all household devices/readbacks are Mock, not HA or physical completion.',
                            'Full 36-slot listening packet has no autoplay or completed human ratings; no output selection or retries.']}


def markdown(s):
    lines = ['# W16 pronunciation frontend A/B', '', 'Engineering evidence passed: ' + str(s['passed']),
             'Human acoustic pronunciation accepted: False', '', '| Arm | Completed | Focal matched/observed/total | Speech Exact | CER | Home semantics |', '|---|---|---|---|---|---|']
    for a, v in s['by_arm'].items():
        lines.append(f"| {a} | {v['completed']}/{v['observations']} | {v['focal_passed']}/{v['focal_observed']}/{v['focal_total']} | {v['speech_exact']}/{v['speech_scored']} | {v['cer']} | {v['home_semantics_passed']}/10 |")
    return '\n'.join(lines + ['', s['latency_policy'], ''] + ['- ' + v for v in s['limitations']]) + '\n'


def run(args, worker_runner=None, asr_factory=None):
    args.outdir.mkdir(parents=True, exist_ok=False)
    p = read(args.protocol); validate_protocol(p, current=True)
    write(args.outdir / 'protocol.json', p)
    for name in ('workers', 'worker-logs', 'records', 'references'): (args.outdir / name).mkdir()
    resources = analysis_module().load_resources(Path(p['model_dir']))
    execution = {'started_at': now(), 'execution_kind': 'fake_worker_or_asr_unit_fixture' if worker_runner or asr_factory else 'native_tts_and_asr',
                 'source_git': DEV.git(), 'workers': [], 'worker_subprocesses': [], 'network_attempts': [],
                 'asr_model_load_wall_ms': None, 'asr_model_init_error': None, 'error': None,
                 'runtime_inputs_preserved': False, 'worker_subprocesses_network_guarded_separately': True}
    rows, references = [], []; packet = None; valid_workers = {}
    with network_disabled() as attempts:
        try:
            for arm in ('A', 'B'):
                directory = args.outdir / 'workers' / arm
                try:
                    result = (worker_runner or subprocess_worker)(p, args.protocol, arm, directory, args.outdir / 'worker-logs')
                    execution['worker_subprocesses'].append(result)
                    w = worker_evidence(directory, p, arm, formal=worker_runner is None)
                    execution['workers'].append(w); valid_workers[arm] = True
                except Exception as error:
                    execution['workers'].append({'arm': arm, 'passed': False, 'generation_attempts': 0,
                                                 'network_attempts': [], 'coordinator_error': f'{type(error).__name__}: {error}'})
            asr = None; start = perf_counter()
            try: asr = (asr_factory or ASR.native_asr)(p)
            except Exception as error: execution['asr_model_init_error'] = f'{type(error).__name__}: {error}'
            execution['asr_model_load_wall_ms'] = (perf_counter() - start) * 1000
            with (args.outdir / 'ledger.jsonl').open('x') as ledger:
                for arm in ('A', 'B'):
                    for index, case in enumerate(p['cases']):
                        n = len(rows); ledger.write(json.dumps({'event': 'observation_started', 'index': n, 'arm': arm, 'id': case['id']}) + '\n'); ledger.flush()
                        source = args.outdir / 'workers' / arm / 'records' / f'{index:04d}.json'
                        worker = read(source) if valid_workers.get(arm) and source.exists() else None
                        row = observe(case, arm, p, worker, ASR.file_identity(source) if source.exists() else None,
                                      asr, execution['asr_model_init_error'], resources)
                        rows.append(row); write(args.outdir / 'records' / f'{n:04d}.json', row)
                        ledger.write(json.dumps({'event': 'observation_recorded', 'index': n, 'arm': arm, 'id': case['id'], 'completed': row['completed']}) + '\n'); ledger.flush()
                for case in p['fixture']['home_cases']:
                    n = len(references); ledger.write(json.dumps({'event': 'reference_started', 'index': n, 'id': case['id']}) + '\n'); ledger.flush()
                    ref = {'case': case, 'probe': None, 'error': None}
                    try: ref['probe'] = HOME.reference_probe(case, p['configs'])
                    except Exception as error: ref['error'] = f'{type(error).__name__}: {error}'
                    references.append(ref); write(args.outdir / 'references' / f'{n:04d}.json', ref)
                    ledger.write(json.dumps({'event': 'reference_recorded', 'index': n, 'id': case['id'], 'completed': ref['probe'] is not None}) + '\n'); ledger.flush()
            packet = analysis_module().make_listening_packet(p, rows, args.outdir / 'listening')
            validate_listening_packet(packet, p, rows)
        except Exception as error:
            execution['error'] = {'type': type(error).__name__, 'detail': str(error)}
        execution['network_attempts'] = list(attempts)
    try:
        validate_protocol(p, current=True)
        execution['runtime_inputs_preserved'] = True
    except Exception as error:
        execution['integrity_error'] = f'{type(error).__name__}: {error}'
    execution['listening_packet'] = packet; execution['finished_at'] = now()
    write(args.outdir / 'execution.json', execution)
    files = [f for f in args.outdir.rglob('*') if f.is_file()]
    write(args.outdir / 'raw-index.json', {'artifacts': [{'path': str(f.relative_to(args.outdir)), 'bytes': f.stat().st_size, 'sha256': ASR.sha256(f)} for f in sorted(files)]})
    s = aggregate(p, rows, references, execution, packet)
    write(args.outdir / 'summary.json', s); (args.outdir / 'report.md').write_text(markdown(s)); return s


def validate_row(row, case, arm, resources):
    home = case['category'] in ('normal', 'abnormal')
    if any(type(row.get(k)) is not bool for k in ('completed', 'synthesis_completed', 'asr_attempted')):
        raise ValueError('Observation flags must be literal booleans')
    if row['case'] != case or row['arm'] != arm or row['id'] != case['id'] or row['category'] != case['category'] or row['source_kind'] != 'synthetic_audio':
        raise ValueError('Recorded source/case differs from frozen inputs')
    if row['worker_record_identity']:
        file_check(row['worker_record_identity']); worker = read(row['worker_record_identity']['path'])
        validate_worker_row(worker, case, arm)
        for name in ('source_float', 'pcm16_wav', 'actual_native_words', 'native_trace'):
            if row[name] != worker[name]: raise ValueError('Primary evidence differs from the actual worker')
        if row['synthesis_completed'] != worker['completed']: raise ValueError('Synthesis completion was changed')
    elif row['synthesis_completed'] or row['source_float'] or row['pcm16_wav']:
        raise ValueError('Missing worker cannot invent synthesized audio')
    if row['frontend'] != analysis_module().derive_frontend(case, row['actual_native_words'], resources):
        raise ValueError('Reconstructed frontend phone/oracle differs')
    for value in row['timing'].values():
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0):
            raise ValueError('Finite nonnegative observed timing required')
    needed = {'tts_generation_wall_ms', 'pcm_read_convert_wall_ms', 'asr_replay_wall_ms', 'decode_only_ms', 'file_audio_to_readback_wall_ms'}
    if home: needed.update(HOME.TIMING_KEYS)
    if row['completed'] and (not needed <= row['timing'].keys() or any(row['timing'][k] is None for k in needed)
                              or not row['synthesis_completed'] or not row['asr_attempted'] or not isinstance(row['recognizer_output'], str)
                              or not row['asr_metrics'] or row['text_metrics'] is None or not row['audio']
                              or (home and row['primary'] is None)):
        raise ValueError('Completed row must retain actual TTS/ASR/state evidence')
    if row['recognizer_output'] is not None:
        if (not row['asr_attempted'] or row['text_metrics'] != text_metrics(case['reference_text'], row['recognizer_output'])
                or row['voice_normalized'] != _normalize_transcript(row['recognizer_output'])):
            raise ValueError('ASR metrics or normalization differs')
        samples, rate, metadata = ASR.read_pcm16_mono(Path(row['pcm16_wav']['path']))
        decode = row['asr_metrics'].get('decode_seconds')
        if (isinstance(decode, bool) or not isinstance(decode, (int, float)) or not math.isfinite(decode) or decode < 0
                or row['timing'].get('decode_only_ms') != decode * 1000):
            raise ValueError('ASR decode measurement differs from observed milliseconds')
        if row['audio'] != metadata or not math.isclose(row['asr_metrics']['audio_seconds'], len(samples) / rate, rel_tol=1e-12, abs_tol=1e-12):
            raise ValueError('Recorded ASR input/duration differs from real generated WAV')
    elif row['text_metrics'] is not None or row['voice_normalized'] is not None or row['asr_metrics'] is not None:
        raise ValueError('No received ASR output can invent transcript metrics')
    if home:
        a = row['primary']; expected = HOME.check_probe(a, case) if a else None
        if a and a['checks'] != expected: raise ValueError('Mock state/feedback oracle differs')
        if row['primary_semantics_passed'] != bool(a and all(expected.values())): raise ValueError('Home semantic verdict differs')
    elif (row['primary'] is not None or row['primary_semantics_passed'] is not None
          or any(k in row['timing'] for k in ('voice_control_feedback_wall_ms', 'explicit_final_readback_wall_ms', 'console_feedback_capture_wall_ms'))):
        raise ValueError('Pronunciation probes must never control Mock or acquire semantic scores')


def report(args):
    args.outdir.mkdir(parents=True, exist_ok=False)
    index = read(args.run / 'raw-index.json')['artifacts']
    actual = {str(f.relative_to(args.run)) for f in args.run.rglob('*') if f.is_file()
              and f != args.run / 'raw-index.json' and f != args.run / 'summary.json' and f != args.run / 'report.md'}
    if len(index) != len(actual) or {v['path'] for v in index} != actual:
        raise ValueError('Raw index must cover exact worker/log/float/WAV/listening/protocol/ledger/record set')
    for value in index:
        f = args.run / value['path']
        if f.stat().st_size != value['bytes'] or ASR.sha256(f) != value['sha256']:
            raise ValueError('Raw evidence changed')
    p = read(args.run / 'protocol.json'); validate_protocol(p)
    for item in [*p['model_files'], *p['frozen_inputs']]: file_check(item)
    resources = analysis_module().load_resources(Path(p['model_dir']))
    execution = read(args.run / 'execution.json')
    if (execution['execution_kind'] not in ('native_tts_and_asr', 'fake_worker_or_asr_unit_fixture')
            or execution['source_git'] != p['source_git']):
        raise ValueError('Execution source/evidence classification differs')
    if [w.get('arm') for w in execution['workers']] != ['A', 'B']:
        raise ValueError('Top-level worker order differs')
    for arm, cached in zip(('A', 'B'), execution['workers']):
        directory = args.run / 'workers' / arm
        if (directory / 'raw-index.json').exists():
            if worker_evidence(directory, p, arm, formal=execution['execution_kind'] == 'native_tts_and_asr') != cached:
                raise ValueError('Cached worker verdict/counts differ from actual worker evidence')
        elif cached.get('passed') is not False or not cached.get('coordinator_error'):
            raise ValueError('Missing worker evidence must remain an explicit failed/unverified worker')
    rows = [read(f) for f in sorted((args.run / 'records').glob('*.json'))]
    expected = [(a, c) for a in ('A', 'B') for c in p['cases']]; ledger = []
    for i, row in enumerate(rows):
        if i >= len(expected): raise ValueError('Unexpected extra observation')
        arm, case = expected[i]; validate_row(row, case, arm, resources)
        ledger += [{'event': 'observation_started', 'index': i, 'arm': arm, 'id': case['id']},
                   {'event': 'observation_recorded', 'index': i, 'arm': arm, 'id': case['id'], 'completed': row['completed']}]
    references = [read(f) for f in sorted((args.run / 'references').glob('*.json'))]
    for i, ref in enumerate(references):
        if i >= len(p['fixture']['home_cases']) or ref['case'] != p['fixture']['home_cases'][i]: raise ValueError('Reference source/order differs')
        if ref['probe'] and ref['probe']['checks'] != HOME.check_probe(ref['probe'], ref['case']): raise ValueError('Reference state oracle differs')
        ledger += [{'event': 'reference_started', 'index': i, 'id': ref['case']['id']},
                   {'event': 'reference_recorded', 'index': i, 'id': ref['case']['id'], 'completed': ref['probe'] is not None}]
    if [json.loads(x) for x in (args.run / 'ledger.jsonl').read_text().splitlines()] != ledger:
        raise ValueError('Ledger changed or omitted observations')
    packet = execution['listening_packet']
    validate_listening_packet(packet, p, rows)
    s = aggregate(p, rows, references, execution, packet)
    if s != read(args.run / 'summary.json'): raise ValueError('Recorded summary differs from reconstructed evidence')
    write(args.outdir / 'summary.json', s); (args.outdir / 'report.md').write_text(markdown(s)); return s


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__); sub = parser.add_subparsers(dest='operation', required=True)
    prep = sub.add_parser('prepare')
    prep.add_argument('--cases', type=Path, default=ROOT / 'configs/w16-pronunciation-cases-v1.json')
    prep.add_argument('--original-protocol', type=Path, default=ROOT / 'evaluation/20261113-w16-chain/protocol.json')
    prep.add_argument('--source-inventory', type=Path, default=ROOT / 'configs/w16-material-source-v1.json')
    prep.add_argument('--original-data-guard', '--old-data-guard', dest='old_data_guard', type=Path,
                      default=ROOT / 'evaluation/20261113-w16-pronunciation-executions/original-data-before.json')
    prep.add_argument('--preregistration', type=Path, default=ROOT.parent / 'ai-learning/docs/stages/E0-w16-pronunciation-implementation.md')
    execute = sub.add_parser('run'); execute.add_argument('--protocol', type=Path, required=True)
    rebuild = sub.add_parser('report'); rebuild.add_argument('--run', type=Path, required=True)
    for command in (prep, execute, rebuild): command.add_argument('--outdir', type=Path, required=True)
    args = parser.parse_args(argv)
    try: result = {'prepare': prepare, 'run': run, 'report': report}[args.operation](args)
    except Exception as error:
        print(json.dumps({'passed': False, 'error': f'{type(error).__name__}: {error}'}), file=sys.stderr); return 2
    print(json.dumps(result, ensure_ascii=False, indent=2)); return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
