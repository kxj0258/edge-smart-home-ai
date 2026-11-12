"""Explicit native probes, separate from speech accuracy and live capture.

Run only after freezing a clean source revision. This opens neither microphone
nor device backend; silence is synthetic and VAD processes licensed file audio
in shadow, without gating or changing the main ASR input.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
from time import perf_counter
import wave

import numpy as np

from edge_smart_home.audio.interfaces import AudioChunk
from edge_smart_home.audio.sherpa_onnx_adapters import SherpaOnnxStreamingAsr, SherpaOnnxVad
from edge_smart_home.audio.validation import AudioInputError, validate_audio_samples


ROOT = Path(__file__).resolve().parents[1]


def run(args: argparse.Namespace) -> dict:
    # Refuse overwrite before model creation or input reads.
    args.outdir.mkdir(parents=True, exist_ok=False)
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    dirty = subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT, text=True)
    if dirty:
        raise ValueError('native probe requires a clean child repository')
    config = json.loads((ROOT / 'configs/audio.json').read_text())
    manifest_bytes = args.manifest.read_bytes()
    manifest = json.loads(manifest_bytes)
    models = {name: args.model_dir / file for name, file in (
        ('tokens', 'tokens.txt'), ('encoder', 'encoder.int8.onnx'),
        ('decoder', 'decoder.onnx'), ('joiner', 'joiner.int8.onnx'))}
    identities = []
    for path in [*models.values(), args.vad_model, ROOT / 'configs/audio.json', ROOT / 'configs/hotwords_v2.txt']:
        identities.append({'path': str(path.resolve()), 'bytes': path.stat().st_size,
                           'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    result = {'schema_version': 1, 'kind': 'w14_native_boundary_probes', 'source_commit': head,
              'git_status': dirty, 'manifest_sha256': hashlib.sha256(manifest_bytes).hexdigest(),
              'identities': identities, 'synthetic_silence': [], 'rejected_inputs': [], 'shadow_vad': [],
              'live_capture': False, 'portaudio_overflow_count': None, 'queue_drop_count': None,
              'live_e2e_ms': None, 'device_execution_calls': 0, 'model_http_calls': 0,
              'notes': ['Synthetic silence is not human or synthesized speech accuracy.',
                        'Silero shadow segmentation is not enabled in the product streaming path.',
                        'File replay and decode-only RTF cannot establish overflow resolution.']}
    # Retain partial evidence if a native/runtime/file error follows.
    dest = args.outdir / 'probe.json'

    def save():
        dest.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n')

    save()
    try:
        start = perf_counter()
        asr = SherpaOnnxStreamingAsr(**models, sample_rate=config['sample_rate'], config=config['asr'],
                                    hotwords_file=ROOT / 'configs/hotwords_v2.txt')
        result['asr_model_init_ms'] = (perf_counter() - start) * 1000
        for rate in (8000, 16000, 48000):
            start = perf_counter()
            recognition = asr.transcribe_waveform(np.zeros(rate, dtype=np.float32), rate,
                                                   config['chunk_duration_ms'])
            result['synthetic_silence'].append({'source_kind': 'synthetic_signal', 'sample_rate': rate,
                                               'source_audio_seconds': 1.0, 'text': recognition.text,
                                               'decode_seconds': recognition.decode_seconds,
                                               'rtf_decode_only': recognition.rtf,
                                               'fileless_asr_wall_ms': (perf_counter() - start) * 1000})
            save()
        for code, value in [('AUDIO_SAMPLE_TYPE_INVALID', np.array([1], dtype=np.int16)),
                            ('AUDIO_SHAPE_INVALID', np.zeros((4, 2), dtype=np.float32)),
                            ('AUDIO_NONFINITE', np.array([float('nan')], dtype=np.float32)),
                            ('AUDIO_RANGE_INVALID', np.array([1.01], dtype=np.float64)),
                            ('AUDIO_EMPTY', np.array([], dtype=np.float32))]:
            try:
                validate_audio_samples(value, 16000)
            except AudioInputError as error:
                observed = error.code
            else:
                observed = None
            result['rejected_inputs'].append({'expected': code, 'observed': observed, 'matched': observed == code,
                                               'native_called': False, 'evidence_kind': 'intentional_injection'})
        asr.accept(AudioChunk(np.zeros(800, dtype=np.float32), 8000))
        try:
            asr.accept(AudioChunk(np.zeros(1600, dtype=np.float32), 16000))
        except AudioInputError as error:
            result['same_stream_rate_change'] = {'first_rate': 8000, 'second_rate': 16000,
                                                'code': error.code, 'rejected_before_native': True}
        else:
            raise AssertionError('same-stream changed rate was not rejected')
        asr.finish()
        for case in manifest['cases']:
            path = Path(case['wav'])
            if not path.is_absolute():
                path = ROOT / path
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() != case['sha256']:
                raise ValueError('changed PCM input: ' + case['id'])
            with wave.open(str(path), 'rb') as wav:
                if wav.getnchannels() != 1 or wav.getsampwidth() != 2 or wav.getframerate() != 16000:
                    raise ValueError('shadow VAD requires mono PCM16 at 16 kHz')
                frames = wav.getnframes()
                raw = wav.readframes(frames)
                if len(raw) != frames * 2:
                    raise ValueError('truncated shadow VAD input')
            samples = np.frombuffer(raw, dtype='<i2').astype(np.float32) / 32768.0
            start = perf_counter()
            vad = SherpaOnnxVad(args.vad_model, 16000, config['vad'])
            init_ms = (perf_counter() - start) * 1000
            segments = []
            start = perf_counter()
            for offset in range(0, len(samples), 1600):
                segments.extend(vad.accept(AudioChunk(samples[offset:offset+1600], 16000)))
            segments.extend(vad.finish())
            wall_ms = (perf_counter() - start) * 1000
            result['shadow_vad'].append({'id': case['id'], 'source_kind': case['source_kind'],
                                         'source_audio_seconds': frames / 16000,
                                         'segment_count': len(segments),
                                         'segment_sample_counts': [len(s.samples) for s in segments],
                                         'model_init_ms': init_ms, 'vad_accept_flush_wall_ms': wall_ms,
                                         'changed_asr_input': False, 'speech_gt_available': False})
            save()
        result['passed'] = (all(row['matched'] for row in result['rejected_inputs']) and
                            result['same_stream_rate_change']['code'] == 'AUDIO_SAMPLE_RATE_CHANGED' and
                            len(result['synthetic_silence']) == 3 and len(result['shadow_vad']) == len(manifest['cases']))
    except Exception as error:
        result['passed'] = False
        result['error'] = {'type': type(error).__name__, 'message': str(error)}
        save()
        raise
    save()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-dir', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--vad-model', type=Path, default=ROOT / 'models/silero_vad.onnx')
    parser.add_argument('--outdir', type=Path, required=True)
    args = parser.parse_args()
    try:
        result = run(args)
    except Exception as error:
        parser.exit(2, f'{type(error).__name__}: {error}\n')
    print(json.dumps({'passed': result['passed'], 'output': str(args.outdir / 'probe.json')}))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
