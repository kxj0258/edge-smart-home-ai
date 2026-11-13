"""One predeclared TTS arm, one native generation per frozen independent text.

Native stderr traces describe actual normalization/word splitting. They are not
phone-tensor dumps or listening/acoustic pronunciation acceptance.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import ctypes
import json
import os
from pathlib import Path
import re
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / 'src'):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
from scripts import prepare_w16_audio as AUDIO
from scripts import evaluate_w14_asr as IDENTITY
from scripts import evaluate_w15_devices as DEV
from scripts.run_unit_tests import network_disabled

read, write, identity = AUDIO.read_json, AUDIO.write_json, AUDIO.identity
MODEL_FILES = AUDIO.MODEL_FILES
EXPECTED = {'speaker_id': 13, 'speed': 1.0, 'num_threads': 2, 'provider': 'cpu',
            'sample_rate': 8000, 'num_speakers': 174, 'noise_scale': 0.0,
            'noise_scale_w': 0.0, 'debug': True, 'silence_scale': .2,
            'rule_fst_names': ['phone.fst', 'number.fst'],
            'arm_rule_far_names': {'A': [], 'B': ['rule.far']}}


def check_file(item):
    if identity(item['path']) != item:
        raise ValueError('Frozen file differs: ' + item['path'])


def validate_protocol(p, *, current=False):
    if (p.get('schema_version') != 1 or p.get('kind') != 'w16_frozen_pronunciation_protocol'
            or p.get('state') != 'frozen'):
        raise ValueError('Expected frozen independent pronunciation protocol')
    settings = p.get('settings', {})
    if any(settings.get(k) != v for k, v in EXPECTED.items()):
        raise ValueError('One-factor FAR settings differ from preregistered values')
    for key in ('speaker_id', 'num_threads', 'sample_rate', 'num_speakers'):
        if type(settings[key]) is not int:
            raise ValueError('Integer setting must not be bool: ' + key)
    for key in ('noise_scale', 'noise_scale_w', 'speed', 'silence_scale'):
        if type(settings[key]) not in (int, float):
            raise ValueError('Numeric setting must not be bool: ' + key)
    if type(settings['debug']) is not bool:
        raise ValueError('Debug must be literal boolean')
    cases = p.get('cases')
    if not isinstance(cases, list) or len(cases) != 18:
        raise ValueError('Exactly eighteen predeclared texts required')
    ids = set()
    for case in cases:
        name, text = case.get('id'), case.get('reference_text')
        if (not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', name)
                or name in ids or not isinstance(text, str) or not text.strip() or '\n' in text or '\r' in text):
            raise ValueError('Unique safe IDs and nonempty single-line reference_text required')
        ids.add(name)
    model_dir = Path(p['model_dir']).resolve()
    files = p.get('model_files', [])
    if (len(files) != 9 or {Path(f['path']).name for f in files} != MODEL_FILES
            or any(Path(f['path']).resolve().parent != model_dir for f in files)):
        raise ValueError('All nine unchanged model/material files must be bound')
    if not p.get('frozen_inputs') or not p.get('sources_sha256') or p.get('source_git', {}).get('status') != '':
        raise ValueError('Frozen input and clean committed source identities required')
    if current:
        if (DEV.git() != p['source_git'] or DEV.sources() != p['sources_sha256']
                or IDENTITY.dependency_identity() != p['dependencies']):
            raise ValueError('Exact clean frozen source/native runtime required')
        for item in [*p['model_files'], *p['frozen_inputs']]:
            check_file(item)


def flush_stderr():
    sys.stderr.flush()
    libc = ctypes.CDLL(None)
    fn = libc.fflush
    fn.argtypes = [ctypes.c_void_p]
    fn.restype = ctypes.c_int
    if fn(None) != 0:
        raise OSError('Unable to flush native stdio')


@contextmanager
def captured_native_stderr(path):
    """Capture file descriptor 2, including C++ SHERPA_ONNX_LOGE output."""
    with Path(path).open('xb') as output:
        flush_stderr()
        saved = os.dup(2)
        try:
            os.dup2(output.fileno(), 2)
            yield
        finally:
            try:
                flush_stderr()
            finally:
                os.dup2(saved, 2)
                os.close(saved)


def parse_native_trace(path, reference_text, arm):
    text = Path(path).read_text(errors='replace')
    fields = {'raw_texts': [], 'normalization_steps': [], 'lexicon_input_texts': [], 'word_splits': [], 'oov': []}
    patterns = {'raw_texts': r'Raw text: (.*)$', 'normalization_steps': r'After normalizing: (.*)$',
                'lexicon_input_texts': r'Input text in string: (.*)$', 'word_splits': r'After splitting to words:(.*)$'}
    for line in text.splitlines():
        for key, pattern in patterns.items():
            match = re.search(pattern, line)
            if match:
                fields[key].append(match.group(1).split() if key == 'word_splits' else match.group(1))
        if 'OOV ' in line or 'Unknown token:' in line or 'Failed to convert ' in line:
            fields['oov'].append(line)
    expected = 2 if arm == 'A' else 48  # two explicit FSTs, plus fixed FAR's 46 entries
    fields['case_start_markers'] = [list(m) for m in re.findall(r'^W16_CASE_START (\S+) (\S+)$', text, re.M)]
    fields['case_end_markers'] = [list(m) for m in re.findall(r'^W16_CASE_END (\S+) (\S+)$', text, re.M)]
    case_id = Path(path).stem
    fields['expected_normalization_steps'] = expected
    fields['consistent'] = (fields['case_start_markers'] == [[arm, case_id]]
                            and fields['case_end_markers'] == [[arm, case_id]]
                            and fields['raw_texts'] == [reference_text]
                            and len(fields['normalization_steps']) == expected
                            and len(fields['lexicon_input_texts']) == 1
                            and fields['lexicon_input_texts'][0] == fields['normalization_steps'][-1]
                            and len(fields['word_splits']) == 1 and bool(fields['word_splits'][0]))
    return fields


def native_factory(model_dir, **options):
    from edge_smart_home.audio.tts import SherpaOnnxVitsTts
    return SherpaOnnxVitsTts(model_dir, **options)


def run(args, tts_factory=None):
    # Reject an existing output before reading a protocol or touching a model.
    args.outdir.mkdir(parents=True, exist_ok=False)
    p = read(args.protocol)
    validate_protocol(p, current=tts_factory is None)
    protocol_identity = identity(args.protocol)
    model_dir = Path(p['model_dir']).resolve()
    settings = p['settings']
    arm = args.arm
    if arm not in ('A', 'B'):
        raise ValueError('Expected A or B')
    write(args.outdir / 'protocol.json', p)
    for name in ('records', 'float', 'pcm16', 'native'):
        (args.outdir / name).mkdir()
    before = [identity(v['path']) for v in [*p['model_files'], *p['frozen_inputs']]]
    execution = {'kind': 'w16_pronunciation_worker_execution', 'arm': arm,
                 'execution_kind': 'fake_tts_unit_fixture' if tts_factory else 'native_tts',
                 'source_git': DEV.git(), 'source_protocol': protocol_identity,
                 'input_identities_before': before, 'model_init_error': None,
                 'generation_attempts': 0, 'completed': 0, 'network_attempts': [],
                 'asr_calls': 0, 'control_calls': 0, 'playback_calls': 0, 'microphone_calls': 0,
                 'human_listening_accepted': False, 'personal_understanding_accepted': False,
                 'acoustic_pronunciation_accepted': False, 'retries': 0, 'passed': False}
    factory = tts_factory or native_factory
    options = {'num_threads': settings['num_threads'], 'provider': settings['provider'],
               'rule_far_paths': tuple(model_dir / n for n in settings['arm_rule_far_names'][arm]),
               'noise_scale': settings['noise_scale'], 'noise_scale_w': settings['noise_scale_w'], 'debug': True}
    execution['tts_options'] = {**options, 'rule_far_paths': [str(p) for p in options['rule_far_paths']]}
    rows, tts = [], None
    with network_disabled() as attempts:
        started = perf_counter()
        try:
            with captured_native_stderr(args.outdir / 'model-init.stderr'):
                os.write(2, ('W16_MODEL_INIT_START ' + arm + '\n').encode())
                tts = factory(model_dir, **options)
                if tts.sample_rate != settings['sample_rate'] or tts.num_speakers != settings['num_speakers']:
                    raise ValueError('Actual model sample rate or speaker count differs')
                os.write(2, ('W16_MODEL_INIT_END ' + arm + '\n').encode())
        except Exception as error:
            execution['model_init_error'] = {'type': type(error).__name__, 'detail': str(error)}
            tts = None
        execution['model_load_wall_ms'] = (perf_counter() - started) * 1000
        with (args.outdir / 'ledger.jsonl').open('x') as ledger:
            for index, case in enumerate(p['cases']):
                ledger.write(json.dumps({'event': 'case_started', 'index': index, 'id': case['id'], 'arm': arm}) + '\n'); ledger.flush()
                stderr_path = args.outdir / 'native' / (case['id'] + '.stderr')
                row = {'id': case['id'], 'arm': arm, 'case': case, 'reference_text': case['reference_text'],
                       'source_kind': 'synthetic_audio', 'generation_attempted': False, 'generated_audio_saved': False,
                       'completed': False, 'error': None, 'source_float': None, 'pcm16_wav': None,
                       'conversion': None, 'tts_generation_wall_ms': None, 'native_stderr': None,
                       'native_trace': None, 'actual_native_raw_text': None,
                       'actual_native_normalization_steps': [], 'actual_native_words': None}
                try:
                    with captured_native_stderr(stderr_path):
                        os.write(2, ('W16_CASE_START ' + arm + ' ' + case['id'] + '\n').encode())
                        if tts is None:
                            raise RuntimeError('TTS model initialization failed; this case was not generated')
                        row['generation_attempted'] = True; execution['generation_attempts'] += 1
                        started = perf_counter()
                        audio = tts.synthesize(case['reference_text'], speaker_id=settings['speaker_id'], speed=settings['speed'])
                        row['tts_generation_wall_ms'] = (perf_counter() - started) * 1000
                        os.write(2, ('W16_CASE_END ' + arm + ' ' + case['id'] + '\n').encode())
                    import numpy as np
                    samples = np.asarray(audio.samples)
                    if samples.dtype != np.dtype('float32'):
                        raise ValueError('Native adapter must preserve float32 output')
                    target = args.outdir / 'float' / (case['id'] + '.npy')
                    with target.open('xb') as output:
                        np.save(output, samples, allow_pickle=False)
                    row['source_float'] = identity(target)
                    _, wav, conversion = AUDIO.quantize_pcm16(samples, audio.sample_rate)
                    target = args.outdir / 'pcm16' / (case['id'] + '.wav')
                    with target.open('xb') as output:
                        output.write(wav)
                    row['pcm16_wav'] = identity(target); row['conversion'] = conversion
                    row['generated_audio_saved'] = True
                    trace = parse_native_trace(stderr_path, case['reference_text'], arm)
                    row['native_trace'] = trace
                    row['actual_native_raw_text'] = trace['raw_texts'][0] if len(trace['raw_texts']) == 1 else None
                    row['actual_native_normalization_steps'] = trace['normalization_steps']
                    row['actual_native_words'] = trace['word_splits'][0] if len(trace['word_splits']) == 1 else None
                    if not trace['consistent'] or trace['oov']:
                        raise ValueError('Native normalization/word trace is incomplete or contains OOV')
                    row['completed'] = True
                except Exception as error:
                    row['error'] = {'type': type(error).__name__, 'detail': str(error)}
                    if stderr_path.exists():
                        trace = parse_native_trace(stderr_path, case['reference_text'], arm)
                        row['native_trace'] = trace
                        row['actual_native_raw_text'] = trace['raw_texts'][0] if len(trace['raw_texts']) == 1 else None
                        row['actual_native_normalization_steps'] = trace['normalization_steps']
                        row['actual_native_words'] = trace['word_splits'][0] if len(trace['word_splits']) == 1 else None
                row['native_stderr'] = identity(stderr_path) if stderr_path.exists() else None
                rows.append(row); write(args.outdir / 'records' / f'{index:04d}.json', row)
                ledger.write(json.dumps({'event': 'case_recorded', 'index': index, 'id': case['id'], 'arm': arm, 'completed': row['completed']}) + '\n'); ledger.flush()
        execution['network_attempts'] = list(attempts)
    try:
        execution['input_identities_after'] = [identity(v['path']) for v in [*p['model_files'], *p['frozen_inputs']]]
        if execution['input_identities_after'] != before or identity(args.protocol) != protocol_identity:
            raise ValueError('Frozen input/protocol changed during TTS generation')
        if tts_factory is None and (DEV.git() != p['source_git'] or DEV.sources() != p['sources_sha256'] or IDENTITY.dependency_identity() != p['dependencies']):
            raise ValueError('Source/native runtime changed during TTS generation')
        execution['frozen_inputs_and_runtime_preserved'] = True
    except Exception as error:
        execution['integrity_error'] = {'type': type(error).__name__, 'detail': str(error)}
        execution['frozen_inputs_and_runtime_preserved'] = False
    execution['completed'] = sum(r['completed'] for r in rows)
    execution['passed'] = (len(rows) == 18 and execution['generation_attempts'] == 18 and execution['completed'] == 18
                           and execution['model_init_error'] is None and not attempts
                           and execution['frozen_inputs_and_runtime_preserved'])
    write(args.outdir / 'execution.json', execution)
    files = [p for p in args.outdir.rglob('*') if p.is_file() and p.name != 'raw-index.json']
    write(args.outdir / 'raw-index.json', {'artifacts': [{'path': str(p.relative_to(args.outdir)),
                                                        'bytes': p.stat().st_size, 'sha256': AUDIO.sha(p)} for p in sorted(files)]})
    return execution


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--protocol', type=Path, required=True)
    parser.add_argument('--arm', choices=('A', 'B'), required=True)
    parser.add_argument('--outdir', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = run(args)
    except Exception as error:
        print(json.dumps({'passed': False, 'error': f'{type(error).__name__}: {error}'}), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
