"""Derive frozen W16/W14 signals with one declared SciPy resampling algorithm.

Uses the first PCM16 bytes, never TTS regeneration, reference edits, gain, trim,
clipping or requantization. Formal derivation runs in the separate pinned
resampling environment; the ASR environment is not changed or imported.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys
from time import perf_counter
import wave

ROOT = Path(__file__).resolve().parents[1]
HOME_IDS = tuple(f'W16-{kind}{i:02}' for kind in ('N', 'A') for i in range(1, 6))
NEWS_IDS = tuple(f'W14-H{i:02}' for i in range(1, 6))
NUMPY_VERSION = '2.2.6'
SCIPY_VERSION = '1.15.3'


def now():
    return datetime.now(timezone.utc).isoformat()


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    with Path(path).open('x') as target:
        json.dump(value, target, ensure_ascii=False, indent=2, allow_nan=False)
        target.write('\n')


def identity(path):
    path = Path(path).resolve()
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(block)
    return {'path': str(path), 'bytes': path.stat().st_size, 'sha256': digest.hexdigest()}


def check(item):
    if identity(item['path']) != item:
        raise ValueError('Frozen input identity differs: ' + item['path'])


def git_info():
    return {'commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
            'status': subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT, text=True)}


def resampler_policy(scipy_version=SCIPY_VERSION):
    return {'library': 'scipy.signal.resample_poly', 'scipy_version': scipy_version,
            'home_ratio': {'up': 2, 'down': 1}, 'news_ratio': {'up': 1, 'down': 2},
            'window': ['kaiser', 5.0], 'padtype': 'constant', 'cval': 0.0,
            'output_dtype': 'float32', 'no_requantization': True,
            'gain_applied': False, 'trimmed': False, 'clipped': False,
            'source': 'original mono PCM16LE decoded as float32 /32768; not original TTS float'}


def collect_identities(value):
    """Extract literal file identities from the dependency evidence object."""
    items = []
    if isinstance(value, dict):
        if set(('path', 'bytes', 'sha256')) <= set(value):
            items.append({key: value[key] for key in ('path', 'bytes', 'sha256')})
        else:
            for child in value.values():
                items.extend(collect_identities(child))
    elif isinstance(value, list):
        for child in value:
            items.extend(collect_identities(child))
    return items


def load_resampler(lock_path):
    """Validate the separate environment before importing SciPy's algorithm."""
    lock = read(lock_path)
    if (lock.get('kind') != 'w16_resampling_environment_lock' or lock.get('schema_version') != 1
            or lock.get('numpy') != NUMPY_VERSION or lock.get('scipy') != SCIPY_VERSION
            or lock.get('asr_environment_unchanged') is not True):
        raise ValueError('Expected frozen separate NumPy2.2.6/SciPy1.15.3 environment evidence')
    executable = str(Path(sys.executable).absolute())
    if (lock.get('executable') != executable or lock.get('python') != platform.python_version()
            or Path(sys.prefix).absolute() == Path(sys.base_prefix).absolute()):
        raise ValueError('Use the frozen isolated resampling environment, not the ASR interpreter')
    for key in ('requirements', 'wheel', 'pypi_metadata', 'runtime_files'):
        if not collect_identities(lock.get(key)):
            raise ValueError('Missing resampling dependency file evidence: ' + key)
    dependencies = collect_identities(lock)
    for item in dependencies:
        check(item)
    import numpy as np
    import scipy
    from scipy.signal import resample_poly
    if np.__version__ != NUMPY_VERSION or scipy.__version__ != SCIPY_VERSION:
        raise ValueError('Actual NumPy/SciPy versions differ from the frozen environment')
    return resample_poly, {'python': platform.python_version(), 'executable': executable,
                           'numpy': np.__version__, 'scipy': scipy.__version__,
                           'environment_lock_identity': identity(lock_path),
                           'requirements': lock['requirements'], 'wheel': lock['wheel'],
                           'pypi_metadata': lock['pypi_metadata'], 'runtime_files': lock['runtime_files'],
                           'asr_environment_unchanged': True}, dependencies + [identity(lock_path)]


def validate_float(samples, expected_frames=None):
    import numpy as np
    values = np.asarray(samples)
    if values.dtype != np.dtype('float32') or values.ndim != 1 or not values.size:
        raise ValueError('Expected a nonempty mono float32 signal')
    if expected_frames is not None and len(values) != expected_frames:
        raise ValueError('Resampled frame count differs from ceil(input*up/down)')
    if not np.isfinite(values).all() or np.any(values < -1) or np.any(values > 1):
        raise ValueError('Signal must be finite and within [-1,1]; clipping is forbidden')
    return values


def read_pcm16(path, expected_rate):
    import numpy as np
    with wave.open(str(path), 'rb') as wav:
        if (wav.getnchannels(), wav.getsampwidth(), wav.getcomptype(), wav.getframerate()) != (1, 2, 'NONE', expected_rate):
            raise ValueError('Expected original uncompressed mono PCM16 at the frozen true rate')
        frames = wav.getnframes()
        data = wav.readframes(frames)
    if len(data) != frames * 2 or frames == 0:
        raise ValueError('Truncated or empty PCM input')
    return validate_float(np.frombuffer(data, dtype='<i2').astype(np.float32) / np.float32(32768.0))


def derive(samples, up, down, resampler):
    source = validate_float(samples)
    # Float32 output is required, rather than silently coercing or clipping an
    # unexpected algorithm result. resample_poly preserves float32 inputs.
    value = resampler(source, up=up, down=down, window=('kaiser', 5.0), padtype='constant', cval=0.0)
    return validate_float(value, (len(source) * up + down - 1) // down)


def frozen_inputs(args):
    home = read(args.home_protocol)
    news = read(args.news_protocol)
    if (home.get('schema_version') != 1 or home.get('kind') != 'w16_frozen_voice_home_protocol' or home.get('state') != 'frozen'
            or news.get('schema_version') != 1 or news.get('kind') != 'w14_frozen_asr_protocol' or news.get('state') != 'frozen'):
        raise ValueError('Expected original frozen W16 home and W14 public-news protocols')
    if tuple(c.get('id') for c in home.get('clips', [])) != HOME_IDS:
        raise ValueError('Keep all first ten home clips in their frozen order')
    if tuple(c.get('id') for c in news.get('cases', [])) != NEWS_IDS:
        raise ValueError('Keep all first five public-news clips in their frozen order')
    manifest = read(home['manifest']['path'])
    if (manifest.get('schema_version') != 1 or manifest.get('kind') != 'w16_audio_manifest' or manifest.get('preparation_kind') != 'native_tts_audio'
            or manifest.get('first_generation_preserved') is not True):
        raise ValueError('Home source must remain the first native-TTS/silence manifest')
    if [c.get('id') for c in manifest.get('clips', [])] != list(HOME_IDS):
        raise ValueError('Home manifest and protocol clip order differ')
    inputs = [identity(args.home_protocol), identity(args.news_protocol), home['manifest'], news['manifest']]
    for protocol_clip, manifest_clip in zip(home['clips'], manifest['clips']):
        for key in ('id', 'case_id', 'reference_text', 'source_kind', 'float_npy', 'sha256'):
            if protocol_clip.get(key) != manifest_clip.get(key):
                raise ValueError('Original home protocol/manifest clip differs: ' + key)
        if protocol_clip['source_kind'] not in ('synthetic_audio', 'synthetic_signal'):
            raise ValueError('First home material is synthetic speech/signal, not human')
        if protocol_clip['audio_identity']['sha256'] != protocol_clip['sha256']:
            raise ValueError('Original home WAV binding differs')
        inputs.extend([protocol_clip['audio_identity'], protocol_clip['float_npy']])
    for case in news['cases']:
        if (case.get('source_kind') != 'human' or case.get('license') != 'CC-BY-4.0'
                or any(not isinstance(case.get(k), str) or not case[k] for k in ('source_url', 'attribution', 'expected'))
                or case['audio_identity']['sha256'] != case['sha256']):
            raise ValueError('Public-news source/reference/license binding differs')
        inputs.append(case['audio_identity'])
    for item in inputs:
        check(item)
    return home, news, inputs


def prepare(args, resampler=None):
    # Reusing an output is rejected before input reads, imports or computation.
    args.outdir.mkdir(parents=True, exist_ok=False)
    journal = {'schema_version': 1, 'kind': 'w16_repair_derivation_preparation', 'started_at': now(),
               'preparation_kind': 'fake_unit_fixture' if resampler is not None else 'deterministic_derivation',
               'passed': False, 'derivation_attempts': 0, 'home': [], 'news': [],
               'tts_calls': 0, 'asr_calls': 0, 'microphone_calls': 0, 'playback_calls': 0, 'control_calls': 0,
               'limitations': ['Source PCM decoding is not TTS regeneration; first original floats/WAVs remain frozen.',
                               'Upsampling cannot recover high frequencies; downsampled public news is not household speech.',
                               'No gain, trim, requantization, clipping or transcript edits.']}
    inputs = []
    with (args.outdir / 'journal.jsonl').open('x') as ledger:
        try:
            journal['source_git'] = git_info()
            if resampler is None and journal['source_git']['status']:
                raise ValueError('Formal derivation requires clean committed child sources')
            home, news, inputs = frozen_inputs(args)
            journal['source_inputs_before'] = inputs
            journal['prepare_source_script'] = identity(__file__)
            if resampler is None:
                resampler, environment, dependency_inputs = load_resampler(args.environment_lock)
                inputs += dependency_inputs
            else:
                import numpy as np
                environment = {'python': platform.python_version(), 'executable': str(Path(sys.executable).absolute()),
                               'numpy': np.__version__, 'scipy': 'not_loaded_fake_unit_fixture'}
            journal['derivation_environment'] = environment
            journal['source_inputs_before'] = list(inputs)
            journal['resampler'] = resampler_policy(environment['scipy'])
            (args.outdir / 'home16k').mkdir()
            (args.outdir / 'news8k').mkdir()
            import numpy as np
            for group, clips, rate, up, down, output_rate, field, folder in (
                    ('home', home['clips'], 8000, 2, 1, 16000, 'resampled_float_identity', 'home16k'),
                    ('news', news['cases'], 16000, 1, 2, 8000, 'downsampled_float_identity', 'news8k')):
                for clip in clips:
                    ledger.write(json.dumps({'event': 'derivation_started', 'group': group, 'id': clip['id']}) + '\n'); ledger.flush()
                    source = read_pcm16(clip['audio_identity']['path'], rate)
                    if group == 'home':
                        original_float = np.load(clip['float_npy']['path'], allow_pickle=False)
                        validate_float(original_float, len(source))
                        rounded = np.rint(original_float.astype(np.float64) * 32768.0)
                        if (np.any(rounded < -32768) or np.any(rounded > 32767)
                                or not np.array_equal(rounded / 32768.0, source.astype(np.float64))):
                            raise ValueError('Original TTS float/PCM quantization binding differs')
                    journal['derivation_attempts'] += 1
                    started = perf_counter()
                    derived = derive(source, up, down, resampler)
                    wall_ms = (perf_counter() - started) * 1000
                    target = args.outdir / folder / (clip['id'] + '.npy')
                    with target.open('xb') as output:
                        np.save(output, derived, allow_pickle=False)
                    result = {'id': clip['id'], 'original_wav_identity': clip['audio_identity'],
                              field: identity(target), 'source_sample_rate': rate,
                              'frames_before': len(source), 'frames_after': len(derived),
                              'input_dtype': str(source.dtype), 'output_dtype': str(derived.dtype),
                              'peak_before': float(np.max(np.abs(source))), 'peak_after': float(np.max(np.abs(derived))),
                              'derivation_wall_ms': wall_ms}
                    if group == 'home':
                        result.update(case_id=clip['case_id'], original_float_identity=clip['float_npy'],
                                      resampled_sample_rate=output_rate, reference_text=clip['reference_text'],
                                      source_kind=clip['source_kind'])
                    else:
                        result.update(reference_text=clip['expected'], downsampled_sample_rate=output_rate,
                                      source_kind='human_public_news', source_url=clip['source_url'],
                                      license=clip['license'], attribution=clip['attribution'])
                    journal[group].append(result)
                    ledger.write(json.dumps({'event': 'derivation_saved', 'group': group, 'id': clip['id'],
                                             'output': identity(target)}) + '\n'); ledger.flush()
            after = [identity(v['path']) for v in inputs]
            if after != inputs or identity(__file__) != journal['prepare_source_script']:
                raise ValueError('Frozen input or preparation source changed during derivation')
            journal['source_inputs_after'] = after
            journal['source_git_after'] = git_info()
            if journal['preparation_kind'] == 'deterministic_derivation' and journal['source_git_after'] != journal['source_git']:
                raise ValueError('Source revision changed during formal derivation')
            journal['passed'] = True
            manifest = {'schema_version': 1, 'kind': 'w16_repair_derived_audio_manifest',
                        **{k: journal[k] for k in ('preparation_kind', 'source_git', 'prepare_source_script',
                                                 'derivation_environment', 'resampler', 'home', 'news',
                                                 'source_inputs_before', 'source_inputs_after')},
                        'original_w16_manifest_identity': home['manifest'],
                        'original_w16_protocol_identity': identity(args.home_protocol),
                        'original_w14_protocol_identity': identity(args.news_protocol),
                        'original_w14_manifest_identity': news['manifest'],
                        'first_waveforms_preserved': True, 'reference_texts_changed': False,
                        'new_tts_generations': 0, 'no_requantization': True}
            write(args.outdir / 'manifest.json', manifest)
        except Exception as error:
            journal['passed'] = False
            journal['error'] = {'type': type(error).__name__, 'detail': str(error)}
            # Also retain current identities after a detected mutation or partial
            # failure; never overwrite the expected frozen identities.
            journal['observed_inputs_after_failure'] = []
            for item in inputs:
                try:
                    journal['observed_inputs_after_failure'].append(identity(item['path']))
                except OSError as missing:
                    journal['observed_inputs_after_failure'].append({'path': item['path'], 'error': str(missing)})
            raise
        finally:
            journal['finished_at'] = now()
            write(args.outdir / 'preparation.json', journal)
    return {'passed': True, 'manifest': str((args.outdir / 'manifest.json').resolve()),
            'home': len(journal['home']), 'news': len(journal['news']),
            'derivation_attempts': journal['derivation_attempts'], 'preparation_kind': journal['preparation_kind']}


def self_check(args, resampler=None):
    """Algorithm probes only; they are not speech or ASR accuracy samples."""
    args.outdir.mkdir(parents=True, exist_ok=False)
    result = {'schema_version': 1, 'kind': 'w16_resampling_algorithm_self_check', 'passed': False,
              'preparation_kind': 'fake_unit_fixture' if resampler is not None else 'deterministic_derivation',
              'started_at': now(), 'speech_samples': 0, 'asr_calls': 0, 'tts_calls': 0}
    try:
        result['source_git'] = git_info()
        if resampler is None and result['source_git']['status']:
            raise ValueError('Formal algorithm self-check requires clean committed child sources')
        if resampler is None:
            resampler, environment, deps = load_resampler(args.environment_lock)
            result['derivation_environment'] = environment
        else:
            deps = []
        import numpy as np
        amplitude = 0.2
        def tone(frequency, rate):
            return np.asarray(amplitude * np.sin(2 * np.pi * frequency * np.arange(rate, dtype=np.float64) / rate), dtype=np.float32)
        def rms(values):
            core = values[256:-256]
            return float(np.sqrt(np.mean(core.astype(np.float64) ** 2)))
        expected = amplitude / 2 ** .5
        up = derive(tone(1000, 8000), 2, 1, resampler)
        down = derive(tone(1000, 16000), 1, 2, resampler)
        stop = derive(tone(6000, 16000), 1, 2, resampler)
        result['measurements'] = {'amplitude': amplitude, 'reference_1khz_rms': expected,
                                  'upsampled_1khz_rms_ratio': rms(up) / expected,
                                  'downsampled_1khz_rms_ratio': rms(down) / expected,
                                  'downsampled_6khz_alias_rms_ratio': rms(stop) / expected}
        result['thresholds'] = {'one_khz_passband_rms_relative_error_max': 0.02,
                                'six_khz_alias_rms_ratio_max': 0.01,
                                'edge_discard_samples_for_metrics': 256}
        m = result['measurements']
        if (abs(m['upsampled_1khz_rms_ratio'] - 1) > .02
                or abs(m['downsampled_1khz_rms_ratio'] - 1) > .02
                or m['downsampled_6khz_alias_rms_ratio'] > .01):
            raise ValueError('Declared passband/antialias algorithm self-check failed')
        for item in deps:
            check(item)
        if result['preparation_kind'] == 'deterministic_derivation' and git_info() != result['source_git']:
            raise ValueError('Source revision changed during algorithm self-check')
        result['passed'] = True
    except Exception as error:
        result['error'] = {'type': type(error).__name__, 'detail': str(error)}
        raise
    finally:
        result['finished_at'] = now()
        write(args.outdir / 'self-check.json', result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home-protocol', type=Path, default=ROOT / 'evaluation/20261113-w16-chain/protocol.json')
    parser.add_argument('--news-protocol', type=Path, default=ROOT / 'evaluation/20261113-w14-protocol/protocol.json')
    parser.add_argument('--environment-lock', type=Path, default=ROOT / 'evaluation/20261113-w16-fix-dependencies/environment-lock.json')
    parser.add_argument('--self-check', action='store_true')
    parser.add_argument('--outdir', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = self_check(args) if args.self_check else prepare(args)
    except Exception as error:
        print(json.dumps({'passed': False, 'error': f'{type(error).__name__}: {error}'}), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
