"""Derived lexicon evidence and a pending human listening packet.

These helpers never load a TTS/ASR model, infer acoustically realized phones,
or fill a listener's answers. Native words must come from the saved C++ log.
"""
from __future__ import annotations

import csv
from html import escape
import hashlib
import json
from pathlib import Path
import shutil


def identity(path):
    path = Path(path).resolve()
    return {'path': str(path), 'bytes': path.stat().st_size,
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def load_resources(model_dir):
    model_dir = Path(model_dir)
    token_path, lexicon_path = model_dir / 'tokens.txt', model_dir / 'lexicon.txt'
    tokens = {}
    for line in token_path.read_text().splitlines():
        if not line.strip():
            continue
        token, number = line.rsplit(maxsplit=1)
        tokens.setdefault(token.strip(), int(number))
    lexicon, invalid, duplicates = {}, [], []
    for line in lexicon_path.read_text().splitlines():
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
    return {'lexicon': lexicon, 'token_ids': tokens, 'invalid_entries': invalid,
            'duplicate_entries': duplicates, 'files': [identity(token_path), identity(lexicon_path)],
            'scope': 'Official first valid lexicon entry wins; no historical pronunciation override.'}


def _syllables(phones):
    result, current = [], []
    for phone in phones:
        current.append(phone)
        if phone == '#0':
            result.append(current)
            current = []
    return result if not current else None


def derive_frontend(case, native_words, resources):
    text = case['reference_text']
    valid_words = (isinstance(native_words, list) and bool(native_words)
                   and all(isinstance(word, str) and word for word in native_words))
    words = native_words if valid_words else []
    surface = ''.join(words)
    preserved = valid_words and surface == text.lower()
    phones, characters, oov, errors = [], [], [], []
    offset = 0
    if not valid_words:
        errors.append('Actual native word trace is unavailable; no reference-text substitution.')
    for word in words:
        value = resources['lexicon'].get(word.lower())
        syllables = _syllables(value) if value else None
        if value is None:
            oov.append(word)
        else:
            phones.extend(value)
        aligned = syllables is not None and len(syllables) == len(word)
        if not aligned:
            errors.append('Cannot align #0 syllables to characters in native word: ' + word)
        for i, char in enumerate(word):
            characters.append({'char': char, 'start': offset + i, 'end': offset + i + 1,
                               'source_word': word, 'phones': syllables[i] if aligned else None})
        offset += len(word)
    checks = []
    for focal in case.get('focal_pronunciation', []):
        start, end = focal['char_start'], focal['char_end']
        if (type(start) is not int or type(end) is not int or start < 0 or end <= start or end > len(text)
                or text[start:end] != focal['text']):
            raise ValueError('Frozen focal span/text mismatch')
        selected = characters[start:end] if preserved else []
        available = (len(selected) == end - start and all(row['phones'] is not None for row in selected))
        actual = [phone for row in selected for phone in row['phones']] if available else None
        checks.append({**focal, 'actual_phones': actual,
                       'matched': actual is not None and actual == focal['expected_phones'],
                       'evidence': 'Derived from actual native words plus frozen lexicon; not acoustic listening.'})
    return {'evidence_kind': 'lexicon_reconstruction_from_native_words',
            'native_words': native_words if valid_words else None,
            'joined_surface': surface, 'surface_preserved': bool(preserved),
            'lexicon_derived_phones': phones,
            'lexicon_derived_token_ids': [resources['token_ids'][phone] for phone in phones],
            'token_scope': 'Word phones only; excludes sentence sil/eos and VITS blanks; not a captured tensor.',
            'oov_words': oov, 'alignment_errors': errors, 'characters': characters,
            'focal_checks': checks, 'focal_total': len(checks),
            'focal_observed': sum(row['actual_phones'] is not None for row in checks),
            'focal_passed': sum(row['matched'] for row in checks),
            'all_focal_matched': all(row['matched'] for row in checks) if checks else None}


def make_listening_packet(protocol, rows, outdir):
    """Copy every available audio slot once; retain missing slots and blank ratings."""
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=False)
    (outdir / 'audio').mkdir()
    cases = protocol['cases']
    row_map = {(row['arm'], row['id']): row for row in rows}
    expected = {(arm, case['id']) for arm in ('A', 'B') for case in cases}
    if len(rows) != len(expected) or set(row_map) != expected:
        raise ValueError('Listening packet requires all fixed case/arm records, never selected successes')
    mapping, ratings, html_rows = [], [], []
    for index, case in enumerate(cases):
        slots = ('A', 'B') if index % 2 == 0 else ('B', 'A')
        cells = []
        for slot, arm in zip(('X', 'Y'), slots):
            row = row_map[(arm, case['id'])]
            review_id = f'review-{index + 1:03d}-{slot}'
            source = row.get('pcm16_wav')
            copied = None
            if source is not None:
                if identity(source['path']) != source:
                    raise ValueError('Generated source WAV changed before listening copy')
                target = outdir / 'audio' / (review_id + '.wav')
                shutil.copyfile(source['path'], target)
                copied = identity(target)
                if copied['sha256'] != source['sha256']:
                    raise ValueError('Listening copy is not the unchanged generated waveform')
            mapping.append({'review_id': review_id, 'case_id': case['id'], 'slot': slot, 'arm': arm,
                            'source_wav': source, 'copied_wav': copied})
            ratings.append({'review_id': review_id, 'case_id': case['id'], 'slot': slot,
                            'reference_text': case['reference_text'],
                            'expected_base_pinyin': ' / '.join(' '.join(f['base_pinyin']) for f in case.get('focal_pronunciation', [])),
                            'heard_words': '', 'heard_target_pinyin': '', 'pronunciation_correct': '',
                            'intelligibility_score': '', 'reviewer': '', 'reviewed_at': '', 'comments': '',
                            'status': 'pending' if copied else 'missing_audio'})
            cells.append(f'<audio controls preload="none" src="audio/{review_id}.wav"></audio>'
                         if copied else '<span>生成失败，缺少音频；保留此槽位。</span>')
        html_rows.append('<tr><td>' + escape(case['id']) + '</td><td>' + escape(case['reference_text'])
                         + '</td><td>' + cells[0] + '</td><td>' + cells[1] + '</td></tr>')
    with (outdir / 'ratings.csv').open('w', newline='') as output:
        writer = csv.DictWriter(output, fieldnames=list(ratings[0]), lineterminator='\n')
        writer.writeheader()
        writer.writerows(ratings)
    (outdir / 'mapping-key.json').write_text(json.dumps({'kind': 'w16_listening_presentation_key',
        'policy': 'Even zero-based case index X=A/Y=B; odd X=B/Y=A. Presentation masking only, no human judgments yet.',
        'mapping': mapping}, ensure_ascii=False, indent=2) + '\n')
    (outdir / 'listen.html').write_text('<!doctype html><html lang="zh"><meta charset="utf-8">'
        '<title>W16 发音盲听材料（待验）</title><style>body{font-family:sans-serif;margin:2rem}'
        'table{border-collapse:collapse}td,th{border:1px solid #bbb;padding:.5rem}audio{width:250px}</style>'
        '<h1>W16 发音听辨：待人工填写</h1><p>本包是合成声音，未进行人耳验收。先独立听X/Y，'
        '先把ratings.csv复制到原run目录之外的新review目录并命名ratings-reviewed.csv，记录听到的词、焦点拼音与可懂度，再查看单列的mapping-key.json。'
        '不要用ASR或词典结果代填听感。播放器不自动播放；所有固定输入槽位均保留。</p>'
        '<table><thead><tr><th>案例</th><th>固定原文</th><th>X</th><th>Y</th></tr></thead><tbody>'
        + ''.join(html_rows) + '</tbody></table></html>\n')
    (outdir / 'README.txt').write_text('本地合成语音听辨材料；不是现场播放、真人口令或个人理解验收。\n'
        '打开listen.html手动播放，把ratings.csv复制到原run目录之外的新review目录，命名ratings-reviewed.csv独立填写；保留原空表及其哈希。\n'
        'X/Y仅作展示遮蔽，映射key单列，未宣称已有受盲审阅。\n'
        '原WAV只复制不处理；缺失音频槽位保留，不重生成或挑选。\n')
    artifacts = [identity(path) for path in sorted(outdir.rglob('*')) if path.is_file()]
    return {'kind': 'w16_pending_human_listening_packet', 'path': str(outdir.resolve()),
            'artifacts': artifacts, 'ratings_rows': len(ratings),
            'audio_copied': sum(item['copied_wav'] is not None for item in mapping),
            'human_listening_accepted': False, 'autoplay': False}
