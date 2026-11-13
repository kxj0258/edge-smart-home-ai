"""Prepare and validate an all-slot human review of frozen W16 waveforms.

Standard library only. No TTS, ASR, playback, expected-answer scoring or controls.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime
import hashlib
from html import escape
import io
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import sys


SOURCE_RAW_INDEX_SHA256 = '07158dbbfdb8c2d23043ca1c5f071cb9b5e291417d2af4ff5e62721831a5b238'
SOURCE_PROTOCOL_SHA256 = '84f6d454d00a1651f0de393ca307e5c37cc2a88065643c67f98e78447774993a'
SOURCE_SUMMARY_SHA256 = '26b02e033c501e1fe29292d5e2c4b07f481fe617198b75001b82cdf68625d76c'
SOURCE_REPORT_SHA256 = '4e1d419f9e36a508b58c2a6d5dfe8d2bb0df06c6f840127e0b00f14fa893a0a7'
FIXTURE_SHA256 = 'edd9c02dc8e0e1d7a07633597c7f42639f34e9dc3213f8ad02a04f13b894a522'
RUNTIME_COMMIT = 'f758ec0c1cb9b9752d5aec023133df3c5a613daa'
FIELDS = ['review_id', 'slot', 'audio_sha256', 'reviewer_id', 'reviewed_at',
          'audibility', 'heard_words', 'heard_pronunciation', 'pronunciation_confidence',
          'reference_access', 'attestation', 'status', 'comments']
IDENTITY_FIELDS = FIELDS[:3]
HUMAN_FIELDS = FIELDS[3:]
ENUMS = {
    'audibility': {'clear', 'partly_audible', 'unintelligible', 'uncertain'},
    'pronunciation_confidence': {'certain', 'uncertain', 'unintelligible', 'not_applicable'},
    'reference_access': {'not_consulted', 'previously_known', 'consulted_before_response',
                         'consulted_after_response'},
    'attestation': {'human_listening'},
    'status': {'pending', 'reviewed'},
}
SOURCE_HUMAN_FIELDS = ['heard_words', 'heard_target_pinyin', 'pronunciation_correct',
                       'intelligibility_score', 'reviewer', 'reviewed_at', 'comments']


def read_json(path):
    return json.loads(Path(path).read_text())


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def identity(path):
    path = Path(path).absolute()
    return {'path': str(path), 'bytes': path.stat().st_size, 'sha256': sha256(path)}


def _no_symlinks(path):
    path = Path(path).absolute()
    for part in [path, *path.parents]:
        if part.is_symlink():
            raise ValueError('Symlink paths are not allowed: ' + str(part))
    return path.resolve()


def _file(path, root=None):
    path = _no_symlinks(path)
    if root is not None and not path.is_relative_to(Path(root).absolute()):
        raise ValueError('Artifact escapes its source root: ' + str(path))
    if not path.is_file():
        raise ValueError('Required regular file missing: ' + str(path))
    return path


def _relative_file(root, relative):
    relative = PurePosixPath(relative)
    if relative.is_absolute() or '..' in relative.parts or not relative.parts:
        raise ValueError('Unsafe indexed relative path')
    return _file(Path(root) / str(relative), root)


def _file_set(root):
    names = set()
    for path in Path(root).rglob('*'):
        if path.is_symlink():
            raise ValueError('Symlink entry is not allowed: ' + str(path))
        if path.is_file():
            names.add(str(path.relative_to(root)))
    return names


def _check_identity(entry, root=None):
    p = _file(entry['path'], root)
    if identity(p) != entry:
        raise ValueError('Frozen artifact identity changed: ' + str(p))
    return p


def _csv_rows(path):
    with Path(path).open(newline='') as stream:
        reader = csv.DictReader(stream)
        rows = list(reader)
        if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames):
            raise ValueError('Duplicate or absent CSV header')
        if any(None in row or any(value is None for value in row.values()) for row in rows):
            raise ValueError('CSV row width differs from header')
        return reader.fieldnames, rows


def verify_source(run):
    """Verify the fixed run, all indexed files, original mapping and model identity."""
    run = _no_symlinks(run)
    raw_path = _file(run / 'raw-index.json', run)
    protocol_path = _file(run / 'protocol.json', run)
    if sha256(raw_path) != SOURCE_RAW_INDEX_SHA256 or sha256(protocol_path) != SOURCE_PROTOCOL_SHA256:
        raise ValueError('This workflow requires the original frozen W16 pronunciation run')
    if (sha256(_file(run / 'summary.json', run)) != SOURCE_SUMMARY_SHA256
            or sha256(_file(run / 'report.md', run)) != SOURCE_REPORT_SHA256):
        raise ValueError('Original W16 results must remain unchanged')
    raw = read_json(raw_path)['artifacts']
    names = set()
    for entry in raw:
        if entry['path'] in names:
            raise ValueError('Duplicate source raw-index path')
        names.add(entry['path'])
        p = _relative_file(run, entry['path'])
        if p.stat().st_size != entry['bytes'] or sha256(p) != entry['sha256']:
            raise ValueError('Source raw-index mismatch: ' + entry['path'])
    actual = _file_set(run)
    if actual != names | {'raw-index.json', 'summary.json', 'report.md'}:
        raise ValueError('Frozen source run file set changed')
    protocol = read_json(protocol_path)
    if protocol['source_git'] != {'commit': RUNTIME_COMMIT, 'status': ''}:
        raise ValueError('Unexpected runtime source identity')
    fixture = protocol['case_fixture']
    if fixture['sha256'] != FIXTURE_SHA256:
        raise ValueError('Unexpected fixed 18-case fixture')
    _check_identity(fixture)
    cases = protocol['cases']
    case_ids = [case['id'] for case in cases]
    if (len(cases) != 18 or len(set(case_ids)) != 18
            or sum(len(case.get('focal_pronunciation', [])) for case in cases) != 15):
        raise ValueError('All 18 unique original cases are required')
    model_files = protocol['model_files']
    if len(model_files) != 9 or len({m['path'] for m in model_files}) != 9:
        raise ValueError('All nine original TTS model resources are required')
    for model in model_files:
        _check_identity(model)
    mapping_path = _file(run / 'listening/mapping-key.json', run)
    mapping = read_json(mapping_path)['mapping']
    if len(mapping) != 36:
        raise ValueError('Exactly 36 original audio slots are required')
    for n, entry in enumerate(mapping):
        index, pair_slot = divmod(n, 2)
        slot = ('X', 'Y')[pair_slot]
        arm = ('A', 'B')[pair_slot] if index % 2 == 0 else ('B', 'A')[pair_slot]
        if (entry['review_id'] != f'review-{index + 1:03d}-{slot}' or entry['case_id'] != case_ids[index]
                or entry['slot'] != slot or entry['arm'] != arm):
            raise ValueError('Original all-slot presentation mapping changed')
        original = _check_identity(entry['source_wav'], run)
        copied = _check_identity(entry['copied_wav'], run)
        if original != run / 'workers' / arm / 'pcm16' / (case_ids[index] + '.wav'):
            raise ValueError('Unexpected original waveform location')
        if copied != run / 'listening/audio' / (entry['review_id'] + '.wav'):
            raise ValueError('Unexpected listening waveform location')
        if entry['source_wav']['sha256'] != entry['copied_wav']['sha256']:
            raise ValueError('Listening waveform differs from original waveform')
    source_csv = run / 'listening/ratings.csv'
    _, ratings = _csv_rows(source_csv)
    if len(ratings) != 36:
        raise ValueError('All 36 original blank rating rows are required')
    for row, entry in zip(ratings, mapping):
        if any(row[field] != entry[field] for field in ['review_id', 'case_id', 'slot']):
            raise ValueError('Original rating row identity changed')
        case = cases[case_ids.index(entry['case_id'])]
        expected = ' / '.join(' '.join(f['base_pinyin']) for f in case.get('focal_pronunciation', []))
        if row['reference_text'] != case['reference_text'] or row['expected_base_pinyin'] != expected:
            raise ValueError('Original reference labels changed')
        if row['status'] != 'pending' or any(row[field] for field in SOURCE_HUMAN_FIELDS):
            raise ValueError('Original frozen human rating table must remain blank')
    return {'run': str(run), 'raw_index': identity(raw_path), 'protocol': identity(protocol_path),
            'summary': identity(run / 'summary.json'), 'report': identity(run / 'report.md'),
            'fixture': fixture, 'model_files': model_files, 'mapping': mapping,
            'original_ratings': identity(source_csv), 'source_mapping': identity(mapping_path),
            'source_raw_artifacts': len(raw), 'source_audio_slots': 36}


def _template(mapping):
    rows = []
    for entry in mapping:
        row = dict.fromkeys(FIELDS, '')
        row.update(review_id=entry['review_id'], slot=entry['slot'],
                   audio_sha256=entry['source_wav']['sha256'], status='pending')
        rows.append(row)
    output = io.StringIO(newline='')
    writer = csv.DictWriter(output, fieldnames=FIELDS, lineterminator='\n')
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


def _html(mapping):
    rows = ''.join('<tr><td>' + escape(entry['review_id']) + '</td><td>'
                   + '<audio controls preload="none" src="audio/' + escape(entry['review_id'])
                   + '.wav"></audio></td></tr>' for entry in mapping)
    return ('<!doctype html><html lang="zh"><meta charset="utf-8">'
            '<title>W16 原波形全量听辨</title><h1>全36槽位，待人工听辨</h1>'
            '<p>这是已知开发材料的展示遮蔽，不是新的盲测。此页不显示原文、预期拼音或处理组。'
            '手动播放全部音频，先独立填写实际听到的文字和拼音；听不清请如实选择uncertain或unintelligible。'
            '不要用ASR或词典代填。将ratings-empty.csv复制到本包之外的新CSV，源空表与音频保持不变。'
            '如之前已知原文或查看过参考，请在reference_access中如实记录；最后再查看协调者映射。'
            '不自动播放，不以提交结构完整代替发音正确、真人或个人理解验收。</p>'
            '<table><thead><tr><th>槽位</th><th>原始声音</th></tr></thead><tbody>' + rows
            + '</tbody></table></html>\n')


def _adjudication_templates(source):
    """Second-pass references, never pre-filled with a correctness judgement."""
    cases = {c['id']: c for c in read_json(source['protocol']['path'])['cases']}
    slot_fields = ['review_id', 'case_id', 'slot', 'audio_sha256', 'reference_text',
                   'focal_count', 'first_pass_ratings_sha256', 'hearing_verdict',
                   'reviewer_id', 'reviewed_at', 'comments']
    focal_fields = ['review_id', 'case_id', 'slot', 'focal_index', 'text', 'char_start',
                    'char_end', 'expected_base_pinyin', 'expected_phones',
                    'first_pass_ratings_sha256', 'hearing_verdict', 'reviewer_id',
                    'reviewed_at', 'comments']
    slots, focals = [], []
    for m in source['mapping']:
        case = cases[m['case_id']]
        slot = dict.fromkeys(slot_fields, '')
        slot.update(review_id=m['review_id'], case_id=m['case_id'], slot=m['slot'],
                    audio_sha256=m['source_wav']['sha256'], reference_text=case['reference_text'],
                    focal_count=len(case.get('focal_pronunciation', [])))
        slots.append(slot)
        for index, f in enumerate(case.get('focal_pronunciation', [])):
            row = dict.fromkeys(focal_fields, '')
            row.update(review_id=m['review_id'], case_id=m['case_id'], slot=m['slot'],
                       focal_index=index, text=f['text'], char_start=f['char_start'],
                       char_end=f['char_end'], expected_base_pinyin=' '.join(f['base_pinyin']),
                       expected_phones=' '.join(f['expected_phones']))
            focals.append(row)
    result = {}
    for name, fields, rows in [('slot-adjudication-empty.csv', slot_fields, slots),
                               ('focal-adjudication-empty.csv', focal_fields, focals)]:
        output = io.StringIO(newline='')
        writer = csv.DictWriter(output, fieldnames=fields, lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)
        result[name] = output.getvalue()
    return result


def _outside(path, source):
    path, source = _no_symlinks(path), _no_symlinks(source)
    if path.is_relative_to(source) or source.is_relative_to(path):
        raise ValueError('Output/submission and source directories must be separate')
    return path


def prepare(run, outdir):
    outdir = _outside(outdir, run)
    if outdir.exists() or outdir.is_symlink():
        raise FileExistsError('Output already exists: ' + str(outdir))
    source = verify_source(run)
    outdir.mkdir(parents=True, exist_ok=False)
    reviewer = outdir / 'reviewer'
    (reviewer / 'audio').mkdir(parents=True)
    coordinator = outdir / 'coordinator'
    coordinator.mkdir()
    for entry in source['mapping']:
        target = reviewer / 'audio' / (entry['review_id'] + '.wav')
        shutil.copyfile(entry['source_wav']['path'], target)
        if target.stat().st_size != entry['source_wav']['bytes'] or sha256(target) != entry['source_wav']['sha256']:
            raise ValueError('Copied review audio differs from the frozen waveform')
    (reviewer / 'ratings-empty.csv').write_text(_template(source['mapping']))
    (reviewer / 'listen.html').write_text(_html(source['mapping']))
    shutil.copyfile(source['source_mapping']['path'], coordinator / 'mapping-key.json')
    shutil.copyfile(source['original_ratings']['path'], coordinator / 'ratings-original.csv')
    for name, content in _adjudication_templates(source).items():
        (coordinator / name).write_text(content)
    if verify_source(run) != source:
        raise ValueError('Source changed during packet copy; retain incomplete output for diagnosis')
    artifacts = []
    for p in sorted(outdir.rglob('*')):
        if p.is_file():
            artifacts.append({'path': str(p.relative_to(outdir)), 'bytes': p.stat().st_size, 'sha256': sha256(p)})
    manifest = {'schema_version': 1, 'kind': 'w16_frozen_audio_human_review_workflow',
                'source': source, 'artifacts': artifacts,
                'policy': 'All 36 original waveforms copied unchanged. Presentation masking only; not a new blind experiment. Human submissions must be separate files outside this bundle and the source run.',
                'human_listening_accepted': False, 'actual_acoustic_pronunciation_accepted': False,
                'human_household_material_accepted': False, 'personal_understanding_accepted': False,
                'tts_asr_playback_control_calls': 0}
    (outdir / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    return {'status': 'pending', 'audio_slots': 36, 'submitted_rows': 0,
            'second_pass_slot_rows': 36, 'second_pass_focal_rows': 30,
            'bundle': str(outdir), 'manifest': identity(outdir / 'manifest.json'),
            'human_listening_accepted': False, 'actual_acoustic_pronunciation_accepted': False,
            'human_household_material_accepted': False, 'personal_understanding_accepted': False,
            'tts_asr_playback_control_calls': 0}


def _verify_bundle(bundle):
    bundle = _no_symlinks(bundle)
    manifest = read_json(_file(bundle / 'manifest.json', bundle))
    if manifest['kind'] != 'w16_frozen_audio_human_review_workflow' or manifest['schema_version'] != 1:
        raise ValueError('Unknown review bundle schema')
    source = verify_source(manifest['source']['run'])
    if source != manifest['source']:
        raise ValueError('Source provenance in review manifest changed')
    expected = {'reviewer/listen.html', 'reviewer/ratings-empty.csv',
                'coordinator/mapping-key.json', 'coordinator/ratings-original.csv',
                'coordinator/slot-adjudication-empty.csv', 'coordinator/focal-adjudication-empty.csv'}
    expected.update('reviewer/audio/' + m['review_id'] + '.wav' for m in source['mapping'])
    artifacts = manifest['artifacts']
    if len(artifacts) != len(expected) or {a['path'] for a in artifacts} != expected:
        raise ValueError('Review bundle must preserve every original slot and template')
    actual = _file_set(bundle)
    if actual != expected | {'manifest.json'}:
        raise ValueError('Review bundle file set changed; save submissions outside it')
    for a in artifacts:
        p = _relative_file(bundle, a['path'])
        if p.stat().st_size != a['bytes'] or sha256(p) != a['sha256']:
            raise ValueError('Review bundle artifact identity changed: ' + a['path'])
    if (bundle / 'reviewer/ratings-empty.csv').read_text() != _template(source['mapping']):
        raise ValueError('Immutable review template changed')
    if (bundle / 'reviewer/listen.html').read_text() != _html(source['mapping']):
        raise ValueError('Masked player changed')
    for name, content in _adjudication_templates(source).items():
        if (bundle / 'coordinator' / name).read_text() != content:
            raise ValueError('Immutable second-pass template changed')
    for m in source['mapping']:
        if sha256(bundle / 'reviewer/audio' / (m['review_id'] + '.wav')) != m['source_wav']['sha256']:
            raise ValueError('Review audio no longer matches the original waveform')
    for field, local in [('source_mapping', 'coordinator/mapping-key.json'),
                         ('original_ratings', 'coordinator/ratings-original.csv')]:
        if sha256(bundle / local) != source[field]['sha256']:
            raise ValueError('Original coordinator labels changed')
    for field in ['human_listening_accepted', 'actual_acoustic_pronunciation_accepted',
                  'human_household_material_accepted', 'personal_understanding_accepted']:
        if manifest[field] is not False:
            raise ValueError('Preparation cannot claim human acceptance')
    if manifest['tts_asr_playback_control_calls'] != 0:
        raise ValueError('Unexpected model or device operation in review manifest')
    return bundle, source


def validate(bundle, ratings):
    manifest_before = identity(_file(Path(bundle) / 'manifest.json'))
    bundle, source = _verify_bundle(bundle)
    ratings = _outside(ratings, bundle)
    _outside(ratings, source['run'])
    ratings = _file(ratings)
    ratings_before = identity(ratings)
    headers, rows = _csv_rows(ratings)
    if headers != FIELDS:
        raise ValueError('Human review CSV must have the exact documented header')
    if len(rows) != 36 or len({r['review_id'] for r in rows}) != 36:
        raise ValueError('Exactly 36 unique review rows are required; never omit failed audio')
    expected = {m['review_id']: m for m in source['mapping']}
    if set(expected) != {row['review_id'] for row in rows}:
        raise ValueError('Review CSV slot set differs from the fixed 36 slots')
    completed = 0
    reference_access = {}
    reviewer_ids = set()
    for row in rows:
        m = expected[row['review_id']]
        if row['slot'] != m['slot'] or row['audio_sha256'] != m['source_wav']['sha256']:
            raise ValueError('Human review CSV immutable identity changed')
        for field, choices in ENUMS.items():
            if row[field] and row[field] not in choices:
                raise ValueError('Invalid human review value for ' + field)
        if row['status'] not in {'pending', 'reviewed'}:
            raise ValueError('Every review row requires pending or reviewed status')
        if row['reviewer_id'] and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}', row['reviewer_id']):
            raise ValueError('Use a nonempty ASCII pseudonym as reviewer_id')
        if row['reviewed_at']:
            try:
                timestamp = datetime.fromisoformat(row['reviewed_at'].replace('Z', '+00:00'))
            except ValueError as exc:
                raise ValueError('reviewed_at must be an ISO timestamp with timezone') from exc
            if 'T' not in row['reviewed_at'] or timestamp.tzinfo is None or timestamp.utcoffset() is None:
                raise ValueError('reviewed_at must include time and timezone')
        for field in HUMAN_FIELDS:
            if len(row[field]) > 4000 or '\x00' in row[field]:
                raise ValueError('Invalid or oversized human review text')
        if row['status'] == 'pending':
            continue
        required = ['reviewer_id', 'reviewed_at', 'audibility', 'heard_pronunciation',
                    'pronunciation_confidence', 'reference_access', 'attestation']
        if any(not row[field].strip() for field in required):
            raise ValueError('Reviewed rows require all human observation fields')
        if row['audibility'] != 'unintelligible' and not row['heard_words'].strip():
            raise ValueError('Record heard words, or explicitly mark unintelligible')
        if row['pronunciation_confidence'] == 'unintelligible' and row['audibility'] != 'unintelligible':
            raise ValueError('Unintelligible pronunciation requires unintelligible audibility')
        completed += 1
        reviewer_ids.add(row['reviewer_id'])
        reference_access[row['reference_access']] = reference_access.get(row['reference_access'], 0) + 1
    if len(reviewer_ids) > 1:
        raise ValueError('Use a separate full 36-slot submission for each reviewer')
    if identity(ratings) != ratings_before:
        raise ValueError('Human review CSV changed during validation')
    if identity(bundle / 'manifest.json') != manifest_before:
        raise ValueError('Review bundle manifest changed during validation')
    return {'status': 'structure_complete' if completed == 36 else 'pending',
            'structure_complete': completed == 36, 'total_slots': 36,
            'reviewed_slots': completed, 'pending_slots': 36 - completed,
            'reviewer_id': next(iter(reviewer_ids)) if reviewer_ids else None,
            'reference_access_counts': reference_access,
            'ratings': ratings_before, 'manifest': manifest_before,
            'human_listening_accepted': False, 'actual_acoustic_pronunciation_accepted': False,
            'human_household_material_accepted': False, 'personal_understanding_accepted': False,
            'scope': 'CSV completeness and self-attested observations only; no proof of listener identity, acoustic correctness, blind protocol or household human recordings.',
            'tts_asr_playback_control_calls': 0}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    prep = sub.add_parser('prepare')
    prep.add_argument('--run', type=Path, required=True)
    prep.add_argument('--outdir', type=Path, required=True)
    check = sub.add_parser('validate')
    check.add_argument('--bundle', type=Path, required=True)
    check.add_argument('--ratings', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = prepare(args.run, args.outdir) if args.command == 'prepare' else validate(args.bundle, args.ratings)
    except (ValueError, OSError, KeyError, TypeError, csv.Error, json.JSONDecodeError) as exc:
        print(json.dumps({'status': 'rejected', 'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 3 if args.command == 'validate' and not result['structure_complete'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
