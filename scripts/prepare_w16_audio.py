"""Prepare fixed project-authored command audio; never select by ASR output.

The existing, source-verified AISHELL3 TTS model is reused. Generated speech,
programmatic silence and human recordings are distinct provenance categories.
No microphone, playback, ASR, model service or device is opened here.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import sys
from time import perf_counter
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

MODEL_FILES = frozenset({'model.onnx', 'lexicon.txt', 'tokens.txt', 'speakers.txt',
                         'phone.fst', 'number.fst', 'date.fst', 'new_heteronym.fst', 'rule.far'})
SETTINGS = {'speaker_id': 13, 'speed': 1.0, 'num_threads': 2, 'provider': 'cpu',
            'model_sample_rate': 8000}


def read_json(path):
    return json.loads(Path(path).read_text())


def write_json(path, value):
    with Path(path).open('x') as target:
        json.dump(value, target, ensure_ascii=False, indent=2, allow_nan=False)
        target.write('\n')


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def identity(path):
    path = Path(path).resolve()
    return {'path': str(path), 'bytes': path.stat().st_size, 'sha256': sha(path)}


def git_info():
    return {'commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
            'status': subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT, text=True)}


def validate_cases(value):
    if value.get('schema_version') != 1 or value.get('kind') != 'w16_voice_home_cases':
        raise ValueError('expected w16_voice_home_cases schema_version1')
    cases = value.get('cases')
    if not isinstance(cases, list) or not cases:
        raise ValueError('cases must be a nonempty list')
    seen = set()
    for case in cases:
        if (not isinstance(case, dict) or not isinstance(case.get('id'), str)
                or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', case['id']) or case['id'] in seen):
            raise ValueError('unique safe case IDs are required')
        if not isinstance(case.get('reference_text'), str):
            raise ValueError('reference_text must be a string')
        if not case['reference_text'].strip() and case.get('audio_kind') != 'silence':
            raise ValueError('empty reference requires explicit silence audio_kind')
        if case['reference_text'].strip() and case.get('audio_kind') == 'silence':
            raise ValueError('silence cannot have a nonempty speech reference')
        seen.add(case['id'])
    return cases


def verify_model(inventory, model_dir=None):
    tts = inventory['tts']
    if tts.get('license') != 'Apache-2.0' or tts.get('source_data_license') != 'Apache-2.0':
        raise ValueError('expected explicitly reviewed model and data Apache-2.0 declarations')
    if any(tts.get(key) != value for key, value in SETTINGS.items()):
        raise ValueError('frozen speaker/speed/threads/provider/sample-rate configuration differs')
    listed = tts.get('files', [])
    names = [Path(item['path']).name for item in listed]
    if len(listed) != 9 or set(names) != MODEL_FILES:
        raise ValueError('source inventory must identify all nine frozen model files')
    model_dir = Path(model_dir or tts['model_dir']).resolve()
    observed = []
    for item in listed:
        path = model_dir / Path(item['path']).name
        current = identity(path)
        if current['bytes'] != item['bytes'] or current['sha256'] != item['sha256']:
            raise ValueError('frozen TTS file differs: ' + path.name)
        observed.append(current)
    return model_dir, observed


def quantize_pcm16(samples, sample_rate):
    import numpy as np
    if type(sample_rate) is not int or sample_rate != 8000:
        raise ValueError('fixed TTS/silence sample rate must be 8000 Hz')
    original = np.asarray(samples)
    if original.ndim != 1 or original.size == 0 or original.dtype.kind != 'f':
        raise ValueError('TTS samples must be nonempty mono floating samples')
    with np.errstate(over='ignore', invalid='ignore'):
        source = original.astype(np.float64)
    if not np.isfinite(source).all():
        raise ValueError('TTS samples must be finite representable floating values')
    with np.errstate(over='ignore'):
        rounded = np.rint(source * 32768.0)
    clipped = int(np.count_nonzero((rounded < -32768) | (rounded > 32767)))
    pcm = np.clip(rounded, -32768, 32767).astype('<i2')
    output = io.BytesIO()
    with wave.open(output, 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm.tobytes())
    return original, output.getvalue(), {
        'sample_rate': sample_rate, 'channels': 1, 'sample_frames': len(original),
        'audio_seconds': len(original) / sample_rate, 'original_dtype': str(original.dtype),
        'format': 'PCM16_LE', 'formula': 'q=clip(rint(float64(x)*32768),-32768,32767); ties to even',
        'origin_peak_abs': float(np.max(np.abs(source))), 'clipped_sample_count': clipped,
        'derived_peak_abs': float(np.max(np.abs(pcm.astype(np.float64) / 32768.0))),
        'max_abs_quantization_error': float(np.max(np.abs(source - pcm.astype(np.float64) / 32768.0))),
        'resampled': False, 'trimmed': False,
    }


def native_tts(model_dir):
    from edge_smart_home.audio.tts import SherpaOnnxVitsTts
    return SherpaOnnxVitsTts(model_dir, num_threads=2, provider='cpu')


def prepare(args, tts_factory=None):
    # Existing output rejection precedes config/input reads and native loading.
    args.outdir.mkdir(parents=True, exist_ok=False)
    cases_path = args.cases.resolve()
    inventory_path = args.source_inventory.resolve()
    cases = validate_cases(read_json(cases_path))
    inventory = read_json(inventory_path)
    model_dir, before = verify_model(inventory, args.model_dir)
    source_git = git_info()
    if tts_factory is None and source_git['status']:
        raise ValueError('native audio preparation requires a clean committed child repository')
    import numpy as np
    (args.outdir / 'float').mkdir()
    (args.outdir / 'pcm16').mkdir()
    clips = []
    preparation = {'schema_version': 1, 'kind': 'w16_audio_preparation', 'started_at': datetime.now(timezone.utc).isoformat(),
                   'source_git': source_git, 'preparation_kind': 'fake_tts_unit_fixture' if tts_factory else 'native_tts_audio',
                   'cases_file': identity(cases_path), 'material_source_inventory': identity(inventory_path),
                   'generator': SETTINGS, 'model_files_before': before, 'synthesis_attempts': 0,
                   'synthetic_signals_created': 0, 'clips': clips, 'passed': False,
                   'human_recordings': 0, 'asr_calls': 0, 'microphone_calls': 0, 'playback_calls': 0,
                   'limitations': ['Project-authored fixture text, generated waveforms and upstream licenses are separate.',
                                   'No generation-noise seed API is asserted; fresh generation need not be byte identical.',
                                   'Preserve the first generated waveform SHA; no regeneration or ASR-based selection.',
                                   'Synthetic speech/silence do not establish human or live microphone acceptance.']}
    try:
        started = perf_counter()
        tts = (tts_factory or native_tts)(model_dir)
        preparation['tts_model_load_wall_ms'] = (perf_counter() - started) * 1000
        if int(tts.num_speakers) != 174 or int(tts.sample_rate) != 8000:
            raise ValueError('native TTS model speaker/sample-rate metadata differs')
        for case in cases:
            text = case['reference_text']
            if text.strip():
                started = perf_counter()
                preparation['synthesis_attempts'] += 1
                audio = tts.synthesize(text, speaker_id=13, speed=1.0)
                generation_ms = (perf_counter() - started) * 1000
                samples, rate, kind = audio.samples, audio.sample_rate, 'synthetic_audio'
            else:
                preparation['synthetic_signals_created'] += 1
                samples, rate, kind = np.zeros(8000, dtype=np.float32), 8000, 'synthetic_signal'
                generation_ms = None
            original, data, conversion = quantize_pcm16(samples, rate)
            float_path = args.outdir / 'float' / (case['id'] + '.npy')
            with float_path.open('xb') as target:
                np.save(target, original, allow_pickle=False)
            wav_path = args.outdir / 'pcm16' / (case['id'] + '.wav')
            with wav_path.open('xb') as target:
                target.write(data)
            clips.append({'id': case['id'], 'case_id': case['id'], 'reference_text': text,
                          'wav': str(wav_path.resolve()), 'sha256': sha(wav_path), 'bytes': len(data),
                          'source_kind': kind, 'source_url': inventory['tts']['source_repository'] + '/tree/' + inventory['tts']['source_revision'] if text.strip() else 'project://w16/programmatic-silence',
                          'license': 'project-generated; upstream model/data Apache-2.0' if text.strip() else 'project-generated synthetic signal',
                          'attribution': 'Project-authored household fixture; k2-fsa/icefall AISHELL3 VITS model and AISHELL-3 corpus' if text.strip() else 'Project-authored deterministic zero waveform',
                          'provenance_note': 'Synthetic TTS audio, fixed sid13/speed1/CPU/2threads; not a human recording' if text.strip() else 'One second of programmatic silence; not synthesized speech or a human recording',
                          'float_npy': identity(float_path), 'conversion': conversion, 'tts_generation_wall_ms': generation_ms})
        _, after = verify_model(inventory, model_dir)
        if before != after:
            raise ValueError('TTS model changed during preparation')
        if identity(cases_path) != preparation['cases_file'] or identity(inventory_path) != preparation['material_source_inventory']:
            raise ValueError('frozen case/source inventory changed during preparation')
        source_after = git_info()
        if tts_factory is None and source_after != source_git:
            raise ValueError('source revision changed during native audio preparation')
        preparation.update(model_files_after=after, source_git_after=source_after, passed=True)
    except Exception as error:
        preparation['error'] = {'type': type(error).__name__, 'detail': str(error)}
        raise
    finally:
        preparation['finished_at'] = datetime.now(timezone.utc).isoformat()
        write_json(args.outdir / 'preparation.json', preparation)
    manifest = {key: preparation[key] for key in ('preparation_kind', 'source_git', 'cases_file', 'material_source_inventory', 'generator')}
    manifest.update(schema_version=1, kind='w16_audio_manifest', clips=clips,
                    first_generation_preserved=True, cross_generation_bitwise_determinism_claimed=False)
    write_json(args.outdir / 'manifest.json', manifest)
    return {'passed': True, 'manifest': str(args.outdir / 'manifest.json'),
            'synthesis_attempts': preparation['synthesis_attempts'], 'synthetic_signals': preparation['synthetic_signals_created']}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', type=Path, default=ROOT / 'configs/evaluation/w16-voice-home-v1.json')
    parser.add_argument('--source-inventory', type=Path, default=ROOT / 'configs/w16-material-source-v1.json')
    parser.add_argument('--model-dir', type=Path)
    parser.add_argument('--outdir', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = prepare(args)
    except Exception as error:
        parser.exit(2, f'{type(error).__name__}: {error}\n')
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
