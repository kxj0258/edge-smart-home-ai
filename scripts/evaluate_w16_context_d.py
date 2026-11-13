"""Frozen D18 context-bundle validation against the archived B18 control.

Prepare/readonly report do not generate, infer, record, play or control devices.
Run creates each D waveform once, then decodes each once into the existing Mock
text path. Archived reference controls are read, never executed again.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / 'src'):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
from scripts import evaluate_w16_pronunciation as BASE
from scripts import w16_pronunciation_analysis as ANALYSIS
from scripts import w16_listening_review as SOURCE
from scripts import w16_context_d_worker as WORKER
from scripts.run_unit_tests import network_disabled
from edge_smart_home.audio.pipeline import _normalize_transcript
from edge_smart_home.benchmark.asr_evaluation import text_metrics

HOME, ASR, DEV = BASE.HOME, BASE.ASR, BASE.DEV
read, write, now = HOME.read, HOME.write, HOME.now
PLAN_SHA = '0c4576a8fe4089b204ceab71f4833273753ac2cac751b91ba6bc983e22a5d401'
TARGET_IDS = {'W16-TN02', 'W16-TA02'}
PATTERN = r'(?<=#\$\|温度\|\$#)调到(?=[零〇一二两三四五六七八九十百]+度)'


def no_symlink(path):
    path = Path(path).absolute()
    if any(p.is_symlink() for p in [path, *path.parents]):
        raise ValueError('Symlink input/output paths are not allowed')
    return path.resolve()


def fresh_outdir(path, forbidden):
    path = no_symlink(path)
    if path.exists():
        raise FileExistsError('Output exists; choose a fresh directory')
    for source in forbidden:
        source = no_symlink(source)
        if path.is_relative_to(source) or source.is_relative_to(path):
            raise ValueError('Output overlaps a frozen source')
    return path


def protocol_forbidden(p):
    result = [Path(p['original_run']), Path(p['model_dir']), Path(p['bundle_manifest']['path']).parent,
              *[Path(a['path']).parent for a in p.get('models', {}).values()]]
    for a in p['frozen_inputs']:
        path = Path(a['path'])
        result.append(path)
        # Protect complete prior evaluation packages, while ordinary source
        # files at the repository root must not forbid every new evaluation.
        if path.is_relative_to(ROOT / 'evaluation'):
            parts = path.relative_to(ROOT / 'evaluation').parts
            if len(parts) > 1:
                result.append(ROOT / 'evaluation' / parts[0])
    return result


def check_file(item):
    path = no_symlink(item['path'])
    if not path.is_file() or ASR.file_identity(path) != item:
        raise ValueError('Frozen file identity changed: ' + str(path))


def bundle_verify(path):
    from scripts.build_w16_context_bundle import verify_bundle
    manifest = verify_bundle(Path(path))
    if not isinstance(manifest, dict):
        manifest = read(path)
    if (manifest.get('kind') != 'w16_temperature_context_bundle' or manifest.get('schema_version') != 1
            or manifest.get('state') != 'frozen' or manifest.get('passed') is not True
            or manifest.get('static_validation', {}).get('passed') is not True):
        raise ValueError('A frozen statically verified compiled context bundle is required')
    return manifest


def policy():
    return {'arm': 'D', 'control': 'archived_original_B18', 'texts': 18, 'home': 10,
            'target_focals': 2, 'all_focals': 15, 'protected_non_target_focals': 13,
            'protected_non_target_utterances': 16, 'tts_attempts': 18, 'asr_attempts': 18,
            'attempts_per_case': 1, 'warmups': 0, 'retries': 0,
            'new_reference_control_calls': 0, 'archived_reference_records': 10,
            'factor': 'one inseparable context FAR plus append-only sidecar lexicon bundle',
            'strict_non_target_float_and_wav_sha256_equality': True,
            'automatic_product_promotion': False}


def static_case_check(cases, controls, static):
    if len(static) != 18 or len(controls) != 18:
        raise ValueError('All original eighteen ordered static/control cases are required')
    changed = []
    for case, control, row in zip(cases, controls, static):
        before = control['native_trace']['normalization_steps'][-1]
        after = re.sub(PATTERN, lambda _: '#$|调到|$#', before)
        if (control['id'] != case['id'] or control['case'] != case or control['arm'] != 'B'
                or row['id'] != case['id'] or row['input_normalized'] != before
                or row['output_normalized'] != after
                or row['changed'] is not (after != before)
                or row['target'] is not (case['id'] in TARGET_IDS)
                or row['surface_preserved'] is not True
                or after.replace('#$|', '').replace('|$#', '') != case['reference_text']):
            raise ValueError('Static proof changes a frozen context, order or literal text')
        if after != before:
            changed.append(case['id'])
    if set(changed) != TARGET_IDS or len(changed) != 2:
        raise ValueError('Only the two predeclared temperature contexts may change')


def validate_protocol(p, *, current=False):
    if (p.get('schema_version') != 1 or p.get('kind') != 'w16_frozen_context_d_protocol'
            or p.get('state') != 'frozen' or p.get('policy') != policy()
            or p.get('settings') != WORKER.EXPECTED):
        raise ValueError('Expected exact frozen D18 context settings/policy')
    for key, value in WORKER.EXPECTED.items():
        if type(p['settings'][key]) is not type(value):
            raise ValueError('Settings must keep their exact types, not bool-as-number')
    check_file(p['registered_plan'])
    if p['registered_plan']['sha256'] != PLAN_SHA:
        raise ValueError('Original context preregistration may not be relabeled or edited')
    if p['cases'] != BASE.validate_fixture(p['fixture']):
        raise ValueError('Exactly the original fixture/order/oracles are required')
    source = SOURCE.verify_source(p['original_run'])
    if source != p['original_source']:
        raise ValueError('Archived original A/B run identity changed')
    old = read(p['original_run_protocol']['path'])
    if (p['configs'] != old['configs'] or p['models'] != old['models']
            or p['hotwords'] != old['hotwords'] or p['model_files'] != old['model_files']
            or p['model_dir'] != old['model_dir'] or p['dependencies'] != old['dependencies']
            or p['fixture'] != old['fixture']):
        raise ValueError('Models/configs/settings/fixture/native runtime must reuse the frozen original run')
    manifest = bundle_verify(p['bundle_manifest']['path'])
    if (manifest['resources'] != p['resources'] or manifest['compiler'] != p['compiler']
            or manifest['static_validation']['cases'] != p['static_cases']):
        raise ValueError('Compiler/resource/static-proof bundle differs from its freeze')
    controls = [read(a['path']) for a in p['control_records']]
    static_case_check(p['cases'], controls, p['static_cases'])
    refs = [read(a['path']) for a in p['archived_references']]
    if len(refs) != 10:
        raise ValueError('Exactly ten archived reference records, not new calls')
    for case, ref in zip(p['fixture']['home_cases'], refs):
        if (ref['case'] != case or not ref['probe'] or ref['probe']['checks'] != HOME.check_probe(ref['probe'], case)
                or not all(ref['probe']['checks'].values())):
            raise ValueError('Archived independent text control evidence changed')
    if not p['frozen_inputs'] or not p['sources_sha256'] or p['source_git']['status'] != '':
        raise ValueError('Complete frozen inputs and clean committed runtime source required')
    expected_inputs = frozen_input_list(p['original_data_guard']['path'], p['preregistration']['path'],
        p['registered_plan'], p['bundle_manifest']['path'], p['original_run_protocol']['path'],
        p['control_records'], p['archived_references'], manifest)
    if p['frozen_inputs'] != expected_inputs:
        raise ValueError('Frozen inputs must include the complete1241 guard, compiler/bundle and all source controls')
    for a in [p['registered_plan'], p['bundle_manifest'], p['original_run_protocol'],
              *p['model_files'], *p['frozen_inputs'], *p['control_records'], *p['archived_references']]:
        check_file(a)
    for name, digest in p['sources_sha256'].items():
        if ASR.sha256(ROOT / name) != digest:
            raise ValueError('Runtime source hash changed: ' + name)
    if current and (DEV.git() != p['source_git'] or DEV.sources() != p['sources_sha256']
                    or ASR.dependency_identity() != p['dependencies']):
        raise ValueError('Exact clean runtime source and native dependencies required')


def nested_absolute_identities(value):
    found = []
    if isinstance(value, dict):
        if {'path', 'bytes', 'sha256'} <= value.keys() and Path(value['path']).is_absolute():
            found.append({k: value[k] for k in ('path', 'bytes', 'sha256')})
        for item in value.values():
            found.extend(nested_absolute_identities(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(nested_absolute_identities(item))
    return found


def frozen_input_list(guard_path, preregistration, plan, bundle_path, old_protocol, controls, refs, manifest):
    guard = read(guard_path)
    if (guard.get('kind') != 'w16_context_d_original_data_guard'
            or guard.get('prior_index_artifacts_verified') != 1247
            or len(guard.get('immutable_artifacts', [])) != 1241):
        raise ValueError('Current pre-generation data guard with all1241 prior identities required')
    base = Path(guard['path_base'])
    guarded_paths = [no_symlink(Path(a['path']) if Path(a['path']).is_absolute() else base / a['path'])
                     for a in guard['immutable_artifacts']]
    if len(set(guarded_paths)) != 1241:
        raise ValueError('All1241 protected source paths must be distinct, not padded duplicates')
    frozen = [ASR.file_identity(guard_path), ASR.file_identity(preregistration), plan,
              ASR.file_identity(bundle_path), ASR.file_identity(old_protocol),
              *controls, *refs, *nested_absolute_identities(manifest)]
    for item in guard['immutable_artifacts']:
        path = Path(item['path'])
        frozen.append({**item, 'path': str(path if path.is_absolute() else base / path)})
    for f in Path(bundle_path).parent.rglob('*'):
        no_symlink(f)
        if f.is_file():
            frozen.append(ASR.file_identity(f))
    result = {}
    for item in frozen:
        if item['path'] in result and result[item['path']] != item:
            raise ValueError('Contradictory frozen identity for one source path')
        result[item['path']] = item
    return list(result.values())


def prepare(args):
    for path in [args.old_data_guard, args.preregistration, args.registered_plan]:
        no_symlink(path)
    original_run, bundle_path = no_symlink(args.original_run), no_symlink(args.bundle)
    outdir = fresh_outdir(args.outdir, [original_run, bundle_path.parent, args.old_data_guard, args.preregistration])
    source = DEV.git()
    if source['status']:
        raise ValueError('Prepare requires clean committed child source')
    original_source = SOURCE.verify_source(original_run)
    old = read(original_run / 'protocol.json')
    plan = ASR.file_identity(args.registered_plan)
    if plan['sha256'] != PLAN_SHA:
        raise ValueError('Original preregistration is immutable')
    manifest = bundle_verify(bundle_path)
    controls = []
    for file in sorted((original_run / 'records').glob('*.json')):
        row = read(file)
        if row['arm'] == 'B':
            controls.append(ASR.file_identity(file))
    refs = [ASR.file_identity(f) for f in sorted((original_run / 'references').glob('*.json'))]
    frozen = frozen_input_list(args.old_data_guard, args.preregistration, plan, bundle_path,
                              original_run / 'protocol.json', controls, refs, manifest)
    for item in frozen:
        check_file(item)
    p = {'schema_version': 1, 'kind': 'w16_frozen_context_d_protocol', 'state': 'frozen',
         'created_at': now(), 'source_git': source, 'sources_sha256': DEV.sources(),
         'dependencies': ASR.dependency_identity(), 'policy': policy(), 'settings': deepcopy(WORKER.EXPECTED),
         'registered_plan': plan, 'preregistration': ASR.file_identity(args.preregistration),
         'original_data_guard': ASR.file_identity(args.old_data_guard),
         'bundle_manifest': ASR.file_identity(bundle_path), 'resources': manifest['resources'],
         'compiler': manifest['compiler'], 'static_cases': manifest['static_validation']['cases'],
         'original_run': str(original_run), 'original_source': original_source,
         'original_run_protocol': ASR.file_identity(original_run / 'protocol.json'),
         'control_records': controls, 'archived_references': refs,
         'fixture': old['fixture'], 'cases': old['cases'], 'configs': old['configs'],
         'models': old['models'], 'hotwords': old['hotwords'], 'model_files': old['model_files'],
         'model_dir': old['model_dir'], 'frozen_inputs': frozen,
         'human_listening_accepted': False, 'human_household_material_accepted': False,
         'personal_understanding_accepted': False, 'automatic_product_promotion': False}
    validate_protocol(p, current=True)
    fresh_outdir(outdir, protocol_forbidden(p))
    outdir.mkdir(parents=True, exist_ok=False)
    write(outdir / 'protocol.json', p)
    return {'passed': True, 'protocol': ASR.file_identity(outdir / 'protocol.json'),
            'source_git': source, 'new_tts_generations': 0, 'new_asr_calls': 0}


def resources_for(p):
    resources = ANALYSIS.load_resources(Path(p['model_dir']))
    tokens, lexicon, invalid, duplicates = resources['token_ids'], {}, [], []
    for line in Path(p['resources']['sidecar_lexicon']['path']).read_text().splitlines():
        parts = line.split()
        if not parts:
            continue
        word, phones = parts[0].lower(), parts[1:]
        if word in lexicon:
            duplicates.append(word)
            continue
        if not phones or any(phone not in tokens for phone in phones):
            invalid.append(word)
            continue
        lexicon[word] = phones
    return {**resources, 'lexicon': lexicon, 'invalid_entries': invalid, 'duplicate_entries': duplicates,
            'files': [resources['files'][0], p['resources']['sidecar_lexicon']]}


def verify_raw_index(directory, *, top=False):
    directory = no_symlink(directory)
    excluded = {'raw-index.json'} | ({'summary.json', 'report.md'} if top else set())
    for f in directory.rglob('*'):
        no_symlink(f)
    actual = {str(f.relative_to(directory)) for f in directory.rglob('*') if f.is_file()
              and str(f.relative_to(directory)) not in excluded}
    index = read(directory / 'raw-index.json')['artifacts']
    if len(index) != len(actual) or {a['path'] for a in index} != actual:
        raise ValueError('Raw index must bind the exact unselected artifact set')
    for a in index:
        name = PurePosixPath(a['path'])
        if name.is_absolute() or '..' in name.parts:
            raise ValueError('Unsafe raw indexed path')
        f = directory / a['path']
        if f.stat().st_size != a['bytes'] or ASR.sha256(f) != a['sha256']:
            raise ValueError('Raw artifact changed')
    return index


def validate_worker_row(row, case, index, p):
    if any(type(row.get(k)) is not bool for k in ('generation_attempted', 'generated_audio_saved', 'completed')):
        raise ValueError('Literal attempt/completion flags required')
    if row['case'] != case or row['id'] != case['id'] or row['arm'] != 'D' or row['reference_text'] != case['reference_text']:
        raise ValueError('Worker changed original case/order/oracle')
    if row['source_kind'] != 'synthetic_audio':
        raise ValueError('D synthetic speech cannot become human evidence')
    if row['native_stderr']:
        check_file(row['native_stderr'])
        trace = WORKER.parse_native_trace(Path(row['native_stderr']['path']), case['reference_text'], 'D')
        if (trace != row['native_trace']
                or row['actual_native_words'] != (trace['word_splits'][0] if len(trace['word_splits']) == 1 else None)
                or row['actual_native_raw_text'] != (trace['raw_texts'][0] if len(trace['raw_texts']) == 1 else None)
                or row['actual_native_normalization_steps'] != trace['normalization_steps']):
            raise ValueError('Actual FD2 trace differs from cached D words')
    elif row['native_trace'] is not None or row['actual_native_words'] is not None:
        raise ValueError('Missing native trace cannot invent words')
    if row['generated_audio_saved']:
        import numpy as np
        check_file(row['source_float']); check_file(row['pcm16_wav'])
        samples = np.load(row['source_float']['path'], allow_pickle=False)
        if samples.dtype != np.dtype('float32'):
            raise ValueError('Original float32 output required')
        _, wav, conversion = BASE.AUDIO.quantize_pcm16(samples, 8000)
        if Path(row['pcm16_wav']['path']).read_bytes() != wav or conversion != row['conversion']:
            raise ValueError('Float/PCM derived conversion differs')
    value = row['tts_generation_wall_ms']
    if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
        raise ValueError('Finite nonnegative TTS time required')
    if row['completed']:
        if (value is None or not row['generation_attempted'] or not row['generated_audio_saved'] or row['error'] is not None
                or not row['native_trace'] or not row['native_trace']['consistent'] or row['native_trace']['oov']):
            raise ValueError('Complete synthesis needs real saved wave and actual trace')
        WORKER.validate_case_trace(row['native_trace'], p, index)


def worker_evidence(directory, p, *, formal=False):
    verify_raw_index(directory)
    if read(directory / 'protocol.json') != p:
        raise ValueError('Worker protocol must equal frozen source protocol')
    paths = sorted((directory / 'records').glob('*.json'))
    if [f.name for f in paths] != [f'{i:04d}.json' for i in range(18)]:
        raise ValueError('All18 worker records must remain ordered')
    rows = [read(f) for f in paths]
    for i, (row, case) in enumerate(zip(rows, p['cases'])):
        validate_worker_row(row, case, i, p)
    ledger = []
    for i, row in enumerate(rows):
        ledger += [{'event': 'case_started', 'index': i, 'id': row['id'], 'arm': 'D'},
                   {'event': 'case_recorded', 'index': i, 'id': row['id'], 'arm': 'D', 'completed': row['completed']}]
    if [json.loads(x) for x in (directory / 'ledger.jsonl').read_text().splitlines()] != ledger:
        raise ValueError('Worker ledger differs from exactly one frozen attempt per case')
    e = read(directory / 'execution.json')
    expected = (len(rows) == 18 and sum(r['generation_attempted'] for r in rows) == 18 and sum(r['completed'] for r in rows) == 18
                and e['model_init_error'] is None and not e['network_attempts'] and e['frozen_inputs_and_runtime_preserved'])
    if (e['arm'] != 'D' or e['source_git'] != p['source_git'] or e['generation_attempts'] != sum(r['generation_attempted'] for r in rows)
            or e['completed'] != sum(r['completed'] for r in rows) or e['passed'] is not expected or e['retries'] != 0
            or any(e[k] != 0 for k in ['asr_calls', 'control_calls', 'playback_calls', 'microphone_calls'])
            or e['human_listening_accepted'] is not False or e['acoustic_pronunciation_accepted'] is not False
            or e['input_identities_before'] != [*p['model_files'], *p['frozen_inputs']]):
        raise ValueError('Worker count/source/verdict/scope binding changed')
    if e['frozen_inputs_and_runtime_preserved'] and e['input_identities_after'] != e['input_identities_before']:
        raise ValueError('Worker input preservation differs')
    fatal_indices = [i for i, row in enumerate(rows) if row['error'] and row['error']['type'] == 'NativeConfigurationError']
    abort = e.get('aborted_reason')
    if abort is None:
        if fatal_indices:
            raise ValueError('A fatal native discrepancy cannot omit its stop reason')
    else:
        index = abort.get('index')
        if (type(index) is not int or not 0 <= index < 18 or fatal_indices != [index]
                or abort != {'index': index, 'id': rows[index]['id'], **rows[index]['error']}
                or any(r['generation_attempted'] or r['completed'] for r in rows[index + 1:])):
            raise ValueError('Native configuration stop must retain every remaining unattempted slot')
    expected_options = {'num_threads': 2, 'provider': 'cpu', 'noise_scale': 0.0, 'noise_scale_w': 0.0,
                        'debug': True, 'lexicon_path': p['resources']['sidecar_lexicon']['path'],
                        'rule_far_paths': [str(Path(p['model_dir']) / 'rule.far'), p['resources']['context_far']['path']]}
    if e['tts_options'] != expected_options:
        raise ValueError('Only sidecar lexicon and appended context FAR may differ from old B')
    if formal and e['execution_kind'] != 'native_tts':
        raise ValueError('A Fake worker cannot become formal model evidence')
    return e


def subprocess_worker(protocol_path, directory, logdir):
    command = [sys.executable, str(ROOT / 'scripts/w16_context_d_worker.py'),
               '--protocol', str(protocol_path), '--arm', 'D', '--outdir', str(directory)]
    with (logdir / 'stdout').open('x') as out, (logdir / 'stderr').open('x') as err:
        result = subprocess.run(command, stdout=out, stderr=err, cwd=ROOT)
    return {'argv': command, 'returncode': result.returncode,
            'stdout': ASR.file_identity(logdir / 'stdout'), 'stderr': ASR.file_identity(logdir / 'stderr')}


def analysis_module():
    return ANALYSIS


def stopped_observation(case, p, worker, worker_identity, resources, reason, stop_kind):
    """Retain source evidence without constructing ASR, Voice or a Mock fixture."""
    if worker is not None:
        validate_worker_row(worker, case, p['cases'].index(case), p)
    home = case['category'] in ('normal', 'abnormal')
    words = worker['actual_native_words'] if worker else None
    return {'arm': 'D', 'id': case['id'], 'case': case, 'source_kind': 'synthetic_audio',
            'category': case['category'], 'completed': False,
            'synthesis_completed': worker['completed'] if worker else False, 'asr_attempted': False,
            'worker_record_identity': worker_identity,
            'source_float': worker['source_float'] if worker else None,
            'pcm16_wav': worker['pcm16_wav'] if worker else None,
            'actual_native_words': words, 'native_trace': worker['native_trace'] if worker else None,
            'frontend': ANALYSIS.derive_frontend(case, words, resources),
            'recognizer_output': None, 'voice_normalized': None, 'asr_metrics': None,
            'text_metrics': None, 'audio': None, 'primary': None,
            'primary_semantics_passed': False if home else None,
            'timing': {'tts_generation_wall_ms': worker['tts_generation_wall_ms'] if worker else None,
                       'file_audio_to_readback_wall_ms': None},
            'errors': [{'stage': stop_kind + '_stop', 'code': 'D_PIPELINE_STOPPED_BEFORE_ASR', 'detail': reason}],
            'live_e2e_ms': None, 'human_listening_accepted': False}


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
        validate_worker_row(worker, case, p['cases'].index(case), p)
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



def validate_row(row, case, arm, resources, p):
    home = case['category'] in ('normal', 'abnormal')
    if any(type(row.get(k)) is not bool for k in ('completed', 'synthesis_completed', 'asr_attempted')):
        raise ValueError('Observation flags must be literal booleans')
    if row['case'] != case or row['arm'] != arm or row['id'] != case['id'] or row['category'] != case['category'] or row['source_kind'] != 'synthetic_audio':
        raise ValueError('Recorded source/case differs from frozen inputs')
    if row['worker_record_identity']:
        check_file(row['worker_record_identity']); worker = read(row['worker_record_identity']['path'])
        validate_worker_row(worker, case, p['cases'].index(case), p)
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


def aggregate(p, rows, controls, references, execution):
    comparisons, targets, other_focals = [], [], []
    for case, d, c in zip(p['cases'], rows, controls):
        if case['id'] not in TARGET_IDS:
            entry = {'id': case['id'], 'derived_phones_equal': False,
                     'float_sha256_equal': False, 'wav_sha256_equal': False,
                     'native_words_equal': c['actual_native_words'] == d['actual_native_words']}
            cf, df = c['frontend'], d['frontend']
            entry['derived_phones_equal'] = bool(cf['surface_preserved'] and df['surface_preserved']
                and not cf['oov_words'] and not df['oov_words']
                and not cf['alignment_errors'] and not df['alignment_errors']
                and cf['lexicon_derived_phones'] and cf['lexicon_derived_phones'] == df['lexicon_derived_phones'])
            for key, evidence in [('float_sha256_equal', 'source_float'), ('wav_sha256_equal', 'pcm16_wav')]:
                entry[key] = bool(c[evidence] and d[evidence] and c[evidence]['sha256'] == d[evidence]['sha256'])
            entry['strict_equal'] = all(entry[k] for k in ['derived_phones_equal', 'float_sha256_equal', 'wav_sha256_equal'])
            comparisons.append(entry)
        for f in d['frontend']['focal_checks']:
            item = {'id': case['id'], **f}
            (targets if case['id'] in TARGET_IDS and f['text'] == '调到' else other_focals).append(item)
    group = BASE.summary_group(rows)
    gates = {
        'exact_order_and_count': len(rows) == 18 and [r['id'] for r in rows] == [c['id'] for c in p['cases']]
                                and all(r['arm'] == 'D' for r in rows),
        'worker_complete': execution['worker'].get('passed') is True and execution['worker_subprocess'].get('returncode') == 0,
        'all_audio_and_asr_complete': len(rows) == 18 and all(r['completed'] and r['asr_attempted'] for r in rows),
        'archived_text_controls_verified': len(references) == 10 and all(r['probe'] and all(r['probe']['checks'].values()) for r in references),
        'returned_state_matches_readback': all(r['primary']['checks']['returned_matches_explicit_readback'] for r in rows if r['primary']),
        'zero_network_attempts': not execution['network_attempts'] and not execution['worker'].get('network_attempts', []),
        'runtime_inputs_preserved': execution['runtime_inputs_preserved'] is True,
        'no_global_failure': execution['error'] is None,
    }
    strict = {'two_target_focals': len(targets) == 2 and all(f['matched'] for f in targets),
              'thirteen_non_target_focals': len(other_focals) == 13 and all(f['matched'] for f in other_focals),
              'all_fifteen_focals': group['focal_total'] == group['focal_observed'] == group['focal_passed'] == 15,
              'sixteen_non_target_exact_artifacts': len(comparisons) == 16 and all(v['strict_equal'] for v in comparisons),
              'all_native_sequences_complete': len(rows) == 18 and all(r['synthesis_completed']
                    and r['native_trace'] and r['native_trace']['consistent']
                    and len(r['native_trace']['normalization_steps']) == 49 for r in rows),
              'worker_input_integrity': execution['worker'].get('frozen_inputs_and_runtime_preserved') is True,
              'all_literal_surfaces_and_no_oov': len(rows) == 18 and all(r['frontend']['surface_preserved']
                    and not r['frontend']['oov_words'] and not r['frontend']['alignment_errors'] for r in rows)}
    engineering = all(gates.values())
    frontend = all(strict.values())
    home = group['home_semantics_passed'] == 10 and group['abnormal_state_preserved'] == 5
    return {'schema_version': 1, 'kind': 'w16_context_d_results', 'passed': engineering,
            'engineering_evidence_passed': engineering, 'engineering_gates': gates,
            'runtime_source_commit': p['source_git']['commit'], 'execution_kind': execution['execution_kind'],
            'control_scope': 'C is reused original B18, not regenerated or a contemporaneous performance control.',
            'control_original_B': BASE.summary_group(controls), 'D': group,
            'strict_frontend_gates': strict, 'strict_frontend_regression_gate': frontend,
            'target_focal_checks': targets, 'protected_focal_checks': other_focals,
            'non_target_comparisons': comparisons,
            'neutral_controls_passed': all(v['strict_equal'] for v in comparisons
                if next(c for c in p['cases'] if c['id'] == v['id'])['category'] == 'neutral_control'),
            'home_semantic_gate': home, 'development_candidate_gate': engineering and frontend and home,
            'tts_generation_attempts': execution['worker'].get('generation_attempts'),
            'unverified_reported_tts_generation_attempts': execution['worker'].get('reported_unverified_generation_attempts'),
            'asr_attempts': sum(r['asr_attempted'] for r in rows),
            'archived_reference_oracle_matches': sum(bool(r['probe']) and all(r['probe']['checks'].values()) for r in references),
            'new_reference_control_calls': 0, 'network_attempts': len(execution['network_attempts']),
            'warmups': 0, 'retries': 0, 'new_control_B_generations': 0,
            **{k: False for k in ['human_listening_accepted', 'actual_acoustic_pronunciation_accepted',
                'human_household_material_accepted', 'personal_understanding_accepted', 'automatic_product_promotion',
                'asr_weight_license_verified', 'overflow_resolution_accepted', 'real_ha_accepted',
                'physical_completion_accepted', 'audible_feedback_accepted', 'arm_hardware_accepted']},
            'live_e2e_ms': None,
            'latency_policy': 'D18 different fixed texts once each; no warmup/repetition or contemporaneous B speed control. Offline split timings are descriptive, not live/acoustic E2E or an optimization benchmark.',
            'limitations': [
                'Original B18/C and all original A/B scores/waveforms are preserved. D uses the same disclosed development texts, not held-out generalization.',
                'Native FD2 words and 49 normalization steps are observed; reconstructed sidecar lexicon phones are derived, not a captured neural input tensor or acoustic hearing.',
                'One resource-bundle switch comprises the appended context FAR and sidecar lexicon; individual file causal effects are not identified.',
                'Strict 16 non-target float/WAV SHA gates are not relaxed after results; all failed observations remain retained.',
                'Archived ten independent text controls are read and checked, not rerun and not used as ASR fallback.',
                'ASR transcription is not an independent pronunciation judge; human hearing, authorized human recordings and personal understanding remain pending.',
                'Home state is Mock with explicit software readback; no real HA/physical completion, playback, microphone, ARM, live latency or overflow acceptance.']}


def markdown(s):
    c, d = s['control_original_B'], s['D']
    lines = ['# W16 D context bundle', '', 'Engineering evidence: ' + str(s['passed']),
             'Strict frontend regression: ' + str(s['strict_frontend_regression_gate']),
             'Development home candidate: ' + str(s['development_candidate_gate']), '',
             '| Group | Focal matched | Speech Exact | CER | Home semantics |', '|---|---|---|---|---|',
             f"| Archived B / C | {c['focal_passed']}/15 | {c['speech_exact']}/{c['speech_scored']} | {c['cer']} | {c['home_semantics_passed']}/10 |",
             f"| New D | {d['focal_passed']}/15 | {d['speech_exact']}/{d['speech_scored']} | {d['cer']} | {d['home_semantics_passed']}/10 |", '',
             s['control_scope'], '', s['latency_policy'], '']
    return '\n'.join(lines + ['- ' + value for value in s['limitations']]) + '\n'


def run(args, worker_runner=None, asr_factory=None):
    if args.outdir.exists() or args.outdir.is_symlink():
        raise FileExistsError('Output exists')
    p = read(args.protocol)
    outdir = fresh_outdir(args.outdir, [args.protocol, *protocol_forbidden(p)])
    validate_protocol(p, current=True)
    outdir.mkdir(parents=True, exist_ok=False)
    write(outdir / 'protocol.json', p)
    for name in ['worker-logs', 'records']:
        (outdir / name).mkdir()
    controls = [read(v['path']) for v in p['control_records']]
    references = [read(v['path']) for v in p['archived_references']]
    resources = resources_for(p)
    e = {'started_at': now(), 'source_git': DEV.git(),
         'execution_kind': 'fake_worker_or_asr_unit_fixture' if worker_runner or asr_factory else 'native_tts_and_asr',
         'worker': {}, 'worker_subprocess': {}, 'network_attempts': [], 'error': None,
         'asr_model_init_error': None, 'asr_model_load_wall_ms': None,
         'runtime_inputs_preserved': False, 'archived_references': p['archived_references'],
         'fatal_native_configuration_stop': False, 'asr_skip_reason': None,
         'pipeline_stopped': False, 'worker_stop_kind': None,
         'new_reference_control_calls': 0, 'playback_calls': 0, 'microphone_calls': 0,
         'worker_subprocess_network_guarded_separately': True}
    rows, valid_worker = [], False
    with network_disabled() as attempts:
        try:
            try:
                e['worker_subprocess'] = (worker_runner or subprocess_worker)(args.protocol, outdir / 'worker', outdir / 'worker-logs')
                e['worker'] = worker_evidence(outdir / 'worker', p, formal=worker_runner is None)
                valid_worker = True
            except Exception as error:
                raw_worker = None
                if (outdir / 'worker/execution.json').is_file():
                    try:
                        raw_worker = read(outdir / 'worker/execution.json')
                    except Exception:
                        pass
                e['worker'] = {'passed': False, 'generation_attempts': None, 'network_attempts': [],
                               'reported_unverified_generation_attempts': (raw_worker.get('generation_attempts')
                                                                          if isinstance(raw_worker, dict) else None),
                               'count_scope': 'Unknown verified count after worker evidence failure; raw execution retained, never treated as zero calls.',
                               'coordinator_error': f'{type(error).__name__}: {error}'}
            asr = None
            if e['worker'].get('aborted_reason') is not None:
                e['fatal_native_configuration_stop'] = True
                e['pipeline_stopped'] = True; e['worker_stop_kind'] = 'native_configuration'
                e['asr_skip_reason'] = 'Fatal native configuration discrepancy; no ASR model, Voice or Mock constructed.'
            elif not valid_worker:
                e['pipeline_stopped'] = True; e['worker_stop_kind'] = 'unverified_worker_evidence'
                e['asr_skip_reason'] = 'Worker evidence unverified; no ASR model constructed.'
            elif e['worker'].get('frozen_inputs_and_runtime_preserved') is not True:
                e['pipeline_stopped'] = True; e['worker_stop_kind'] = 'input_integrity'
                e['asr_skip_reason'] = 'Worker detected source/input integrity failure; no ASR model, Voice or Mock constructed.'
            else:
                start = perf_counter()
                try:
                    asr = (asr_factory or ASR.native_asr)(p)
                except Exception as error:
                    e['asr_model_init_error'] = f'{type(error).__name__}: {error}'
                e['asr_model_load_wall_ms'] = (perf_counter() - start) * 1000
            with (outdir / 'ledger.jsonl').open('x') as ledger:
                for index, case in enumerate(p['cases']):
                    ledger.write(json.dumps({'event': 'observation_started', 'index': index, 'arm': 'D', 'id': case['id']}) + '\n'); ledger.flush()
                    source = outdir / 'worker/records' / f'{index:04d}.json'
                    worker = read(source) if valid_worker and source.exists() else None
                    if e['pipeline_stopped']:
                        row = stopped_observation(case, p, worker, ASR.file_identity(source) if source.exists() else None,
                                                  resources, e['asr_skip_reason'], e['worker_stop_kind'])
                    else:
                        row = observe(case, 'D', p, worker, ASR.file_identity(source) if source.exists() else None,
                                      asr, e['asr_model_init_error'], resources)
                    rows.append(row); write(outdir / 'records' / f'{index:04d}.json', row)
                    ledger.write(json.dumps({'event': 'observation_recorded', 'index': index, 'arm': 'D', 'id': case['id'], 'completed': row['completed']}) + '\n'); ledger.flush()
        except Exception as error:
            e['error'] = {'type': type(error).__name__, 'detail': str(error)}
        e['network_attempts'] = list(attempts)
    try:
        validate_protocol(p, current=True)
        e['runtime_inputs_preserved'] = True
    except Exception as error:
        e['integrity_error'] = f'{type(error).__name__}: {error}'
    e['finished_at'] = now()
    write(outdir / 'execution.json', e)
    files = [f for f in outdir.rglob('*') if f.is_file()]
    write(outdir / 'raw-index.json', {'artifacts': [{'path': str(f.relative_to(outdir)), 'bytes': f.stat().st_size, 'sha256': ASR.sha256(f)} for f in sorted(files)]})
    summary = aggregate(p, rows, controls, references, e)
    write(outdir / 'summary.json', summary); (outdir / 'report.md').write_text(markdown(summary))
    return summary


def report(args):
    if args.outdir.exists() or args.outdir.is_symlink():
        raise FileExistsError('Output exists')
    p = read(args.run / 'protocol.json')
    outdir = fresh_outdir(args.outdir, [args.run, *protocol_forbidden(p)])
    verify_raw_index(args.run, top=True)
    validate_protocol(p)
    outdir.mkdir(parents=True, exist_ok=False)
    resources = resources_for(p)
    e = read(args.run / 'execution.json')
    if (e['source_git'] != p['source_git'] or e['execution_kind'] not in ['native_tts_and_asr', 'fake_worker_or_asr_unit_fixture']
            or e['archived_references'] != p['archived_references'] or e['new_reference_control_calls'] != 0
            or e['playback_calls'] != 0 or e['microphone_calls'] != 0):
        raise ValueError('Execution source/reused-reference/device scope changed')
    if (args.run / 'worker/raw-index.json').exists():
        if worker_evidence(args.run / 'worker', p, formal=e['execution_kind'] == 'native_tts_and_asr') != e['worker']:
            raise ValueError('Worker cache differs from actual saved generation evidence')
    elif e['worker'].get('passed') is not False or not e['worker'].get('coordinator_error'):
        raise ValueError('Missing worker must remain an explicit failure')
    paths = sorted((args.run / 'records').glob('*.json'))
    if [f.name for f in paths] != [f'{i:04d}.json' for i in range(18)]:
        raise ValueError('All eighteen ordered D records must remain')
    rows = [read(f) for f in paths]
    ledger = []
    for i, (row, case) in enumerate(zip(rows, p['cases'])):
        validate_row(row, case, 'D', resources, p)
        ledger += [{'event': 'observation_started', 'index': i, 'arm': 'D', 'id': case['id']},
                   {'event': 'observation_recorded', 'index': i, 'arm': 'D', 'id': case['id'], 'completed': row['completed']}]
    fatal = e['worker'].get('aborted_reason') is not None
    if e['fatal_native_configuration_stop'] is not fatal:
        raise ValueError('Coordinator native-stop flag differs from worker evidence')
    stop_kind = ('native_configuration' if fatal else 'unverified_worker_evidence' if e['worker'].get('coordinator_error')
                 else 'input_integrity' if e['worker'].get('frozen_inputs_and_runtime_preserved') is not True else None)
    if e['pipeline_stopped'] is not (stop_kind is not None) or e['worker_stop_kind'] != stop_kind:
        raise ValueError('Coordinator global stop scope differs from actual worker evidence')
    if stop_kind and (e['asr_model_load_wall_ms'] is not None or not e['asr_skip_reason']
                     or any(r['asr_attempted'] or r['primary'] is not None for r in rows)):
        raise ValueError('Stopped worker/input evidence cannot continue into ASR or Mock')
    if [json.loads(x) for x in (args.run / 'ledger.jsonl').read_text().splitlines()] != ledger:
        raise ValueError('Coordinator ledger differs from one ordered observation per case')
    controls = [read(v['path']) for v in p['control_records']]
    references = [read(v['path']) for v in p['archived_references']]
    summary = aggregate(p, rows, controls, references, e)
    if summary != read(args.run / 'summary.json') or (args.run / 'report.md').read_text() != markdown(summary):
        raise ValueError('Recorded summary/report differs from saved raw evidence')
    write(outdir / 'summary.json', summary); (outdir / 'report.md').write_text(markdown(summary))
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='operation', required=True)
    prep = sub.add_parser('prepare')
    prep.add_argument('--bundle', type=Path, required=True)
    prep.add_argument('--original-run', type=Path, default=ROOT / 'evaluation/20261113-w16-pronunciation-run')
    prep.add_argument('--registered-plan', type=Path, default=ROOT / 'configs/w16-context-repair-prereg-v1.json')
    prep.add_argument('--old-data-guard', type=Path, default=ROOT / 'evaluation/20261113-w16-context-d-executions/original-data-before.json')
    prep.add_argument('--preregistration', type=Path, required=True)
    execute = sub.add_parser('run'); execute.add_argument('--protocol', type=Path, required=True)
    rebuild = sub.add_parser('report'); rebuild.add_argument('--run', type=Path, required=True)
    for command in [prep, execute, rebuild]: command.add_argument('--outdir', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = {'prepare': prepare, 'run': run, 'report': report}[args.operation](args)
    except Exception as error:
        print(json.dumps({'passed': False, 'error': f'{type(error).__name__}: {error}'}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
