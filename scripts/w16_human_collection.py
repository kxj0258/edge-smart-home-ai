"""Prepare/audit declared human recordings without recording, ASR or playback.

Authorization fields are provider declarations, not proof of authenticity or
legal advice. Complete structure never accepts human/acoustic performance.
"""
from __future__ import annotations

import argparse
import array
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys
import wave

FIXTURE_SHA256 = 'edd9c02dc8e0e1d7a07633597c7f42639f34e9dc3213f8ad02a04f13b894a522'
ROLES = [('P01', 'session-dev', 'development'),
         ('P02', 'session-validation', 'speaker_validation')]
PARTICIPANT_FIELDS = ['participant_id', 'session_id', 'split', 'source_kind',
                      'adult_self_attested', 'recorder_is_participant',
                      'local_evaluation_consent', 'public_release_authorized',
                      'consent_recorded_at', 'consent_record_id', 'recording_context']
RECORDING_FIELDS = ['slot_id', 'participant_id', 'session_id', 'split', 'case_id',
                    'reference_text', 'wav_path', 'captured_at', 'take_index',
                    'source_kind', 'spoken_text_verbatim', 'notes']
POLICY = {'participants': 2, 'slots_per_participant': 18,
          'split_unit': 'participant_and_session', 'same_texts_in_both_splits': True,
          'unseen_text_heldout': False, 'format': 'WAVE_FORMAT_PCM',
          'channels': 1, 'sample_width_bytes': 2, 'sample_rate': 16000,
          'min_seconds': 0.2, 'max_seconds': 20.0, 'max_file_bytes': 2097152,
          'attempts_per_slot': 1, 'public_release_authorized': False,
          'resample_trim_gain_or_asr_selection': False}


def read(path: Path):
    return json.loads(path.read_text(encoding='utf-8'))


def identity(path: Path) -> dict:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1048576), b''):
            digest.update(block)
    return {'path': str(path.resolve()), 'bytes': path.stat().st_size,
            'sha256': digest.hexdigest()}


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2,
                               allow_nan=False) + '\n', encoding='utf-8')


def no_symlink(path: Path) -> Path:
    path = path.absolute()
    if any(p.is_symlink() for p in [path, *path.parents]):
        raise ValueError(f'symlink is not allowed: {path}')
    return path.resolve()


def fresh_output(path: Path, forbidden: list[Path]) -> Path:
    path = no_symlink(path)
    if path.exists():
        raise FileExistsError(f'output already exists: {path}')
    for source in forbidden:
        source = no_symlink(source)
        if path == source or path.is_relative_to(source) or source.is_relative_to(path):
            raise ValueError(f'output overlaps input: {source}')
    return path


def load_cases(path: Path) -> list[dict]:
    path = no_symlink(path)
    if identity(path)['sha256'] != FIXTURE_SHA256:
        raise ValueError('frozen W16 fixture hash mismatch')
    data = read(path)
    cases = data['home_cases'] + data['pronunciation_probes']
    if len(cases) != 18 or len({c['id'] for c in cases}) != 18:
        raise ValueError('expected all 18 fixed cases')
    return [{'case_id': c['id'], 'reference_text': c['reference_text'],
             'category': c['category']} for c in cases]


def slots(cases: list[dict]) -> list[dict]:
    return [{'slot_id': f'{participant}-{i:02d}', 'participant_id': participant,
             'session_id': session, 'split': split, **case}
            for participant, session, split in ROLES
            for i, case in enumerate(cases, 1)]


def write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    with path.open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path: Path, fields: list[str], expected_count: int) -> list[dict]:
    path = no_symlink(path)
    with path.open(newline='', encoding='utf-8') as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != fields:
            raise ValueError(f'CSV header mismatch: {path}')
        rows = list(reader)
    if len(rows) != expected_count or any(None in row or None in row.values() for row in rows):
        raise ValueError(f'CSV must retain exactly {expected_count} complete-schema rows: {path}')
    return rows


def prepare(cases_path: Path, outdir: Path) -> dict:
    cases_path = no_symlink(cases_path)
    cases = load_cases(cases_path)
    outdir = fresh_output(outdir, [cases_path])
    plan = {'schema_version': 1, 'kind': 'w16_declared_human_collection_plan',
            'created_at': datetime.now(timezone.utc).isoformat(),
            'fixture': identity(cases_path), 'policy': POLICY, 'slots': slots(cases),
            'human_household_recordings_accepted': False,
            'human_listening_accepted': False, 'personal_understanding_accepted': False,
            'source_declarations_verified_as_true': False,
            'recording_asr_tts_playback_or_device_calls': 0}
    outdir.mkdir(parents=True, exist_ok=False)
    write_json(outdir / 'plan.json', plan)
    participants = [dict.fromkeys(PARTICIPANT_FIELDS, '') for _ in ROLES]
    for row, (participant, session, split) in zip(participants, ROLES):
        row.update(participant_id=participant, session_id=session, split=split,
                   public_release_authorized='false')
    recordings = []
    for slot in plan['slots']:
        row = dict.fromkeys(RECORDING_FIELDS, '')
        row.update({key: slot[key] for key in RECORDING_FIELDS if key in slot})
        recordings.append(row)
    write_csv(outdir / 'participants-empty.csv', PARTICIPANT_FIELDS, participants)
    write_csv(outdir / 'recordings-empty.csv', RECORDING_FIELDS, recordings)
    (outdir / 'commands.txt').write_text('\n'.join(c['reference_text'] for c in cases) + '\n', encoding='utf-8')
    (outdir / 'README.txt').write_text(
        'PENDING: no consent, adult status or real recordings are supplied.\n'
        'Keep this package immutable. Copy both empty CSVs outside it before entering declarations.\n'
        'P01/session-dev and P02/session-validation each keep all 18 first takes.\n'
        'Different participants are required; public texts overlap, so this is not unseen-text heldout.\n'
        'Only adult self-recordings with explicit prior local-evaluation consent may be imported.\n'
        'Use pseudonyms, not names/addresses/identity documents. Public release remains false.\n'
        'Capture original PCM16 mono 16000 Hz WAV, 0.2-20 seconds; retain failures and first takes.\n'
        'Do not use TTS, old unauthorised captures, ASR feedback, trim/resample/gain or selected retries.\n'
        'Spoken text may differ from the prompt: record it honestly; never change reference_text.\n'
        'Audit checks declarations/bytes/structure, not authenticity, legal validity or pronunciation.\n',
        encoding='utf-8')
    files = ['plan.json', 'participants-empty.csv', 'recordings-empty.csv', 'commands.txt', 'README.txt']
    write_json(outdir / 'package-index.json', {'kind': 'w16_human_collection_package_index',
               'artifacts': [{**identity(outdir / name), 'path': name} for name in files]})
    return {'status': 'pending', 'planned_participants': 2, 'planned_slots': 36,
            'human_household_recordings_accepted': False, 'outdir': str(outdir)}


def load_plan(collection: Path) -> dict:
    collection = no_symlink(collection)
    index = read(no_symlink(collection / 'package-index.json'))
    expected = {'plan.json', 'participants-empty.csv', 'recordings-empty.csv', 'commands.txt', 'README.txt'}
    if {p.name for p in collection.iterdir()} != expected | {'package-index.json'}:
        raise ValueError('unexpected file/directory in immutable collection package')
    if {a['path'] for a in index['artifacts']} != expected or len(index['artifacts']) != 5:
        raise ValueError('collection package index mismatch')
    for a in index['artifacts']:
        actual = identity(no_symlink(collection / a['path']))
        if any(actual[k] != a[k] for k in ['bytes', 'sha256']):
            raise ValueError(f'immutable collection package modified: {a["path"]}')
    plan = read(collection / 'plan.json')
    cases = load_cases(Path(plan['fixture']['path']))
    if (plan['schema_version'] != 1 or plan['kind'] != 'w16_declared_human_collection_plan'
            or plan['policy'] != POLICY or plan['slots'] != slots(cases)
            or plan['fixture'] != identity(Path(plan['fixture']['path']))):
        raise ValueError('collection plan/policy/frozen labels changed')
    return plan


def timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError('timezone required')
    return parsed


def audio_stats(path: Path) -> tuple[dict, list[str]]:
    result = identity(path)
    errors = []
    if result['bytes'] > POLICY['max_file_bytes']:
        return result, ['WAV_FILE_TOO_LARGE']
    try:
        with wave.open(str(path), 'rb') as stream:
            channels, width, rate, frames, compression, _ = stream.getparams()
            result.update(channels=channels, sample_width_bytes=width, sample_rate=rate,
                          frames=frames, compression=compression,
                          duration_seconds=frames / rate if rate else None)
            if (channels, width, rate, compression) != (1, 2, 16000, 'NONE'):
                return result, ['WAV_FORMAT_MUST_BE_PCM16_MONO_16000']
            if not POLICY['min_seconds'] <= frames / rate <= POLICY['max_seconds']:
                errors.append('WAV_DURATION_OUTSIDE_FROZEN_LIMITS')
            data = stream.readframes(frames + 1)
            if len(data) != frames * 2:
                errors.append('WAV_TRUNCATED_OR_FRAME_COUNT_MISMATCH')
            samples = array.array('h')
            samples.frombytes(data[:len(data) - len(data) % 2])
            if sys.byteorder != 'little':
                samples.byteswap()
            result.update(sample_count=len(samples),
                          clipped_sample_count=sum(s in (-32768, 32767) for s in samples),
                          peak_abs_pcm=max((abs(s) for s in samples), default=0),
                          silent=all(s == 0 for s in samples))
    except (wave.Error, EOFError, ValueError) as exc:
        errors.append('WAV_READ_ERROR:' + type(exc).__name__)
    return result, errors


def audit(collection: Path, participants_path: Path, recordings_path: Path, outdir: Path) -> dict:
    collection = no_symlink(collection)
    plan = load_plan(collection)
    participants_path, recordings_path = no_symlink(participants_path), no_symlink(recordings_path)
    participants = read_csv(participants_path, PARTICIPANT_FIELDS, 2)
    recordings = read_csv(recordings_path, RECORDING_FIELDS, 36)
    # Fixed order and unique roles prevent moving a favourable recording between splits.
    for row, (participant, session, split) in zip(participants, ROLES):
        if [row[k] for k in ['participant_id', 'session_id', 'split']] != [participant, session, split]:
            raise ValueError('participant/session/split identity or order changed')
    for row, slot in zip(recordings, plan['slots']):
        if any(row[k] != slot[k] for k in ['slot_id', 'participant_id', 'session_id', 'split', 'case_id', 'reference_text']):
            raise ValueError('slot/order/split/reference_text changed or duplicated')
    raw_paths = {}
    for row in recordings:
        if row['wav_path']:
            value = Path(row['wav_path'])
            raw_paths[row['slot_id']] = no_symlink(value if value.is_absolute() else recordings_path.parent / value)
    outdir = fresh_output(outdir, [collection, participants_path, recordings_path,
                                  *[p.parent for p in raw_paths.values()]])
    source_files = [participants_path, recordings_path, collection / 'package-index.json',
                    collection / 'plan.json', *raw_paths.values()]
    before = {str(p): identity(p) for p in source_files if p.is_file()}
    errors, missing, participant_states = [], [], {}
    for row in participants:
        key = row['participant_id']
        issues, pending = [], []
        required_true = ['adult_self_attested', 'recorder_is_participant', 'local_evaluation_consent']
        for field in required_true:
            if not row[field]:
                pending.append(field)
            elif row[field] != 'true':
                issues.append(field + '_MUST_BE_TRUE')
        if not row['source_kind']:
            pending.append('source_kind')
        elif row['source_kind'] != 'human_self_recording':
            issues.append('SOURCE_NOT_DECLARED_HUMAN_SELF_RECORDING')
        if row['public_release_authorized'] != 'false':
            issues.append('PUBLIC_RELEASE_NOT_AUTHORIZED_BY_THIS_PLAN')
        for field in ['consent_recorded_at', 'consent_record_id', 'recording_context']:
            if not row[field]:
                pending.append(field)
        if row['consent_recorded_at']:
            try:
                timestamp(row['consent_recorded_at'])
            except ValueError:
                issues.append('INVALID_CONSENT_TIMESTAMP')
        participant_states[key] = {'issues': issues, 'pending_fields': pending,
                                   'declaration_complete': not issues and not pending}
        errors.extend(key + ':' + issue for issue in issues)
        missing.extend(key + ':' + field for field in pending)
    results = []
    seen_audio = {}
    for row in recordings:
        slot, participant = row['slot_id'], row['participant_id']
        issues, pending = [], []
        for field in ['wav_path', 'captured_at', 'take_index', 'source_kind', 'spoken_text_verbatim']:
            if not row[field]:
                pending.append(field)
        if row['source_kind'] and row['source_kind'] != 'human_self_recording':
            issues.append('SOURCE_NOT_DECLARED_HUMAN_SELF_RECORDING')
        if row['take_index'] and row['take_index'] != '1':
            issues.append('FIRST_TAKE_REQUIRED_RETRIES_NEED_NEW_PROTOCOL')
        if row['captured_at']:
            try:
                captured = timestamp(row['captured_at'])
                consent = next(p['consent_recorded_at'] for p in participants if p['participant_id'] == participant)
                if consent and timestamp(consent) > captured:
                    issues.append('CONSENT_POSTDATES_CAPTURE')
            except ValueError:
                issues.append('INVALID_CAPTURE_OR_CONSENT_TIMESTAMP')
        result = {'slot_id': slot, 'participant_id': participant, 'session_id': row['session_id'],
                  'split': row['split'], 'case_id': row['case_id'], 'reference_text': row['reference_text'],
                  'provider_spoken_text_verbatim': row['spoken_text_verbatim'],
                  'provider_notes': row['notes'], 'source_audio': None, 'copied_original': None,
                  'copy_authorization_declared': bool(
                      participant_states[participant]['declaration_complete']
                      and row['source_kind'] == 'human_self_recording'
                      and row['captured_at']
                      and not any(issue in issues for issue in
                                  ['CONSENT_POSTDATES_CAPTURE', 'INVALID_CAPTURE_OR_CONSENT_TIMESTAMP']))}
        if slot in raw_paths:
            path = raw_paths[slot]
            if not path.is_file():
                issues.append('WAV_PATH_NOT_A_FILE')
            else:
                if not participant_states[participant]['declaration_complete']:
                    issues.append('AUDIO_SUPPLIED_WITHOUT_COMPLETE_PRIOR_AUTHORIZATION_DECLARATION')
                audio, audio_errors = audio_stats(path)
                result['source_audio'] = audio
                issues.extend(audio_errors)
                digest = audio['sha256']
                if digest in seen_audio:
                    issues.append('DUPLICATE_AUDIO_WITH:' + seen_audio[digest])
                else:
                    seen_audio[digest] = slot
        errors.extend(slot + ':' + issue for issue in issues)
        missing.extend(slot + ':' + field for field in pending)
        result.update(issues=issues, pending_fields=pending)
        results.append(result)
    status = 'rejected' if errors else 'pending' if missing else 'structure_complete_declarations_unverified'
    outdir.mkdir(parents=True, exist_ok=False)
    originals = outdir / 'original-wav'
    originals.mkdir()
    # Retain authorized bounded originals, including failed/duplicate/clipped WAVs.
    # Missing/invalid authorization keeps source hashes, never imports the audio.
    for result in results:
        audio = result['source_audio']
        if audio and result['copy_authorization_declared'] and audio['bytes'] <= POLICY['max_file_bytes']:
            dest = originals / (result['slot_id'] + '.wav')
            shutil.copyfile(raw_paths[result['slot_id']], dest)
            copied = identity(dest)
            if copied['sha256'] != audio['sha256']:
                raise ValueError('source audio changed while copying')
            result['copied_original'] = copied
    submitted_csv = {}
    for source, name in [(participants_path, 'participants-submitted.csv'),
                         (recordings_path, 'recordings-submitted.csv')]:
        dest = outdir / name
        shutil.copyfile(source, dest)
        copied = identity(dest)
        if copied['sha256'] != before[str(source)]['sha256']:
            raise ValueError('submitted CSV changed while copying')
        submitted_csv[name] = copied
    after = {str(p): identity(p) for p in source_files if p.is_file()}
    if before != after:
        raise ValueError('source files changed during audit')
    report = {'schema_version': 1, 'kind': 'w16_human_collection_declaration_audit',
              'created_at': datetime.now(timezone.utc).isoformat(), 'status': status,
              'structure_complete': status == 'structure_complete_declarations_unverified',
              'plan': identity(collection / 'plan.json'), 'policy': plan['policy'],
              'source_files': list(before.values()), 'sources_unchanged': before == after,
              'submitted_csv_copies': submitted_csv,
              'participant_declarations': participant_states, 'slots': results,
              'required_slots': 36, 'supplied_audio_files': sum(r['source_audio'] is not None for r in results),
              'copied_original_files': sum(r['copied_original'] is not None for r in results),
              'issues': errors, 'missing_fields': missing,
              'human_household_recordings_accepted': False, 'human_listening_accepted': False,
              'personal_understanding_accepted': False, 'source_declarations_verified_as_true': False,
              'automatic_product_promotion': False, 'public_release_authorized': False,
              'asr_accuracy_measured': False, 'recording_asr_tts_playback_or_device_calls': 0,
              'limitations': ['Declaration/format checks do not prove that recordings are human or that consent is genuine.',
                              'Different pseudonyms do not independently prove different speakers.',
                              'Same disclosed 18 texts in both splits; speaker-separated validation, not unseen-text heldout.',
                              'Authorized bounded originals and all observations retained; unauthorized originals remain at source with hashes.',
                              'Oversized originals remain at source; their hashes/errors are retained without copying.',
                              'No trimming, resampling, gain, public license grant, live/overflow/HA or acoustic acceptance.']}
    write_json(outdir / 'report.json', report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    prepare_parser = sub.add_parser('prepare')
    prepare_parser.add_argument('--cases', type=Path, default=Path('configs/w16-pronunciation-cases-v1.json'))
    prepare_parser.add_argument('--outdir', type=Path, required=True)
    audit_parser = sub.add_parser('audit')
    audit_parser.add_argument('--collection', type=Path, required=True)
    audit_parser.add_argument('--participants', type=Path, required=True)
    audit_parser.add_argument('--recordings', type=Path, required=True)
    audit_parser.add_argument('--outdir', type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.action == 'prepare':
            result = prepare(args.cases, args.outdir)
        else:
            result = audit(args.collection, args.participants, args.recordings, args.outdir)
        print(json.dumps({k: result[k] for k in ['status', 'structure_complete', 'required_slots',
                          'supplied_audio_files', 'human_household_recordings_accepted'] if k in result}, ensure_ascii=False))
        return 2 if result['status'] == 'rejected' else 3 if result['status'] == 'pending' and args.action == 'audit' else 0
    except (ValueError, OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
