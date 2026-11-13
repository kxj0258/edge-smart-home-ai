"""Save TTS waveforms from the primary run's actual console feedback.

This is a separate artifact-generation step, not acoustic playback or part of
the audio-to-state latency. It never substitutes reference-control messages.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import prepare_w16_audio as AUDIO


def verified_feedback(run):
    """Verify complete raw evidence and preserve primary message provenance."""
    index_path = run / 'raw-index.json'
    entries = AUDIO.read_json(index_path)['artifacts']
    records = sorted((run / 'records').glob('*.json'))
    expected = {'protocol.json', 'execution.json', 'ledger.jsonl',
                *(str(path.relative_to(run)) for path in records)}
    if len(entries) != len(expected) or {v['path'] for v in entries} != expected:
        raise ValueError('raw index must cover exact protocol/execution/ledger/record set')
    inputs = [AUDIO.identity(index_path)]
    for item in entries:
        path = run / item['path']
        actual = AUDIO.identity(path)
        if actual['bytes'] != item['bytes'] or actual['sha256'] != item['sha256']:
            raise ValueError('source run raw hash differs: ' + item['path'])
        inputs.append(actual)
    protocol = AUDIO.read_json(run / 'protocol.json')
    execution = AUDIO.read_json(run / 'execution.json')
    current = AUDIO.git_info()
    if (current['status'] or protocol['source_git'] != current or execution['source_git'] != current):
        raise ValueError('feedback generation requires the same clean runtime HEAD as the source run')
    if execution.get('error') is not None:
        raise ValueError('source run has a global execution failure')
    rows = [AUDIO.read_json(path) for path in records]
    if len(rows) != len(protocol['clips']):
        raise ValueError('source run record count differs from frozen clips')
    messages = []
    for i, row in enumerate(rows):
        if (row['id'] != protocol['clips'][i]['id'] or row.get('clip') != protocol['clips'][i]
                or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', row['id'])):
            raise ValueError('source record identity/order differs from frozen clip')
        feedback = (row.get('primary') or {}).get('console_feedback')
        if not isinstance(feedback, list) or not feedback or any(not isinstance(v, str) or not v.strip() for v in feedback):
            raise ValueError('each primary record must contain nonempty actual console feedback')
        messages.append({'id': row['id'], 'messages': feedback, 'text': '\n'.join(feedback),
                         'source_record': AUDIO.identity(records[i]),
                         'primary_semantics_passed': row['primary_semantics_passed'],
                         'primary_status': row['primary']['status'],
                         'primary_error_code': row['primary']['error_code']})
    ledger = [json.loads(line) for line in (run / 'ledger.jsonl').read_text().splitlines()]
    expected_ledger = [v for i, row in enumerate(rows) for v in (
        {'event': 'clip_started', 'index': i, 'id': row['id']},
        {'event': 'clip_recorded', 'index': i, 'id': row['id'], 'completed': row['completed']})]
    if ledger != expected_ledger:
        raise ValueError('source run ledger order/count differs')
    return current, inputs, messages


def synthesize(args, tts_factory=None):
    # Existing destination rejection must precede source reads/native creation.
    args.outdir.mkdir(parents=True, exist_ok=False)
    source_git, input_files, messages = verified_feedback(args.run.resolve())
    inventory_path = args.source_inventory.resolve()
    inventory = AUDIO.read_json(inventory_path)
    model_dir, model_before = AUDIO.verify_model(inventory)
    input_files.append(AUDIO.identity(inventory_path))
    import numpy as np
    (args.outdir / 'float').mkdir()
    (args.outdir / 'pcm16').mkdir()
    summary = {'schema_version': 1,
               'kind': 'fake_tts_feedback_fixture' if tts_factory else 'tts_native_feedback_generation',
               'started_at': datetime.now(timezone.utc).isoformat(), 'source_git': source_git,
               'source_run': str(args.run.resolve()), 'input_files_before': input_files,
               'model_files_before': model_before, 'generator': AUDIO.SETTINGS,
               'tts_source_repository': inventory['tts'].get('source_repository'),
               'tts_source_revision': inventory['tts'].get('source_revision'),
               'upstream_model_data_license': 'Apache-2.0',
               'output_rights_note': 'Project feedback messages and generated waveforms are separate from upstream licenses; no blanket output-copyright claim.',
               'message_source': 'record.primary.console_feedback', 'feedback': [],
               'synthesis_attempts': 0, 'passed': False,
               'playback_calls': 0, 'asr_calls': 0, 'control_calls': 0, 'microphone_calls': 0,
               'primary_latency_modified': False, 'source_primary_semantics_promoted': False,
               'limitations': ['Saved waveforms only; no speaker device, echo, acoustic latency or live feedback test.',
                               'This standalone generation wall time is not added to the primary audio-to-state latency.',
                               'Original primary messages and semantic verdicts are preserved; generation does not repair or certify them.',
                               'No retries, regeneration-based selection or cross-generation bitwise guarantee.']}
    try:
        start = perf_counter()
        tts = (tts_factory or AUDIO.native_tts)(model_dir)
        summary['tts_model_load_wall_ms'] = (perf_counter() - start) * 1000
        if int(tts.num_speakers) != 174 or int(tts.sample_rate) != 8000:
            raise ValueError('TTS speaker/sample-rate metadata differs')
        for message in messages:
            start = perf_counter()
            summary['synthesis_attempts'] += 1
            audio = tts.synthesize(message['text'], speaker_id=13, speed=1.0)
            generation_ms = (perf_counter() - start) * 1000
            original, data, conversion = AUDIO.quantize_pcm16(audio.samples, audio.sample_rate)
            float_path = args.outdir / 'float' / (message['id'] + '.npy')
            with float_path.open('xb') as target:
                np.save(target, original, allow_pickle=False)
            wav_path = args.outdir / 'pcm16' / (message['id'] + '.wav')
            with wav_path.open('xb') as target:
                target.write(data)
            summary['feedback'].append({**message, 'source_kind': 'synthetic_feedback_audio',
                                        'float_npy': AUDIO.identity(float_path), 'wav': AUDIO.identity(wav_path),
                                        'tts_generation_wall_ms': generation_ms, 'conversion': conversion})
        _, after = AUDIO.verify_model(inventory)
        if after != model_before or any(AUDIO.identity(v['path']) != v for v in input_files):
            raise ValueError('source run, inventory or TTS model changed during feedback generation')
        if AUDIO.git_info() != source_git:
            raise ValueError('runtime revision changed during feedback generation')
        summary.update(model_files_after=after, passed=True)
    except Exception as error:
        summary['error'] = {'type': type(error).__name__, 'detail': str(error)}
        raise
    finally:
        summary['finished_at'] = datetime.now(timezone.utc).isoformat()
        AUDIO.write_json(args.outdir / 'summary.json', summary)
    return {'passed': True, 'summary': str(args.outdir / 'summary.json'),
            'synthesis_attempts': summary['synthesis_attempts'], 'feedback_artifacts': len(summary['feedback'])}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--source-inventory', type=Path, default=ROOT / 'configs/w16-material-source-v1.json')
    parser.add_argument('--outdir', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = synthesize(args)
    except Exception as error:
        parser.exit(2, f'{type(error).__name__}: {error}\n')
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
