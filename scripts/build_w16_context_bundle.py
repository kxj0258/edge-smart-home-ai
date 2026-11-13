"""Compile the predeclared temperature-only W16 context resource bundle.

Pynini is imported only for toolchain freeze/compilation/compiled verification.
verify_bundle() is standard-library-only and safe in the original TTS runtime.
No TTS/ASR model, microphone, player or control interface is imported here.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
from pathlib import Path, PurePosixPath
import platform
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent
PREREG_SHA256 = '0c4576a8fe4089b204ceab71f4833273753ac2cac751b91ba6bc983e22a5d401'
PYNINI_VERSION = '2.1.6.post1'
WHEEL_SHA256 = '10f0717fc6bf09330c41978e4d958a000b790b9b344cbc1117d33b008edf4fde'
ORIGINAL_SUMMARY_SHA256 = '26b02e033c501e1fe29292d5e2c4b07f481fe617198b75001b82cdf68625d76c'
ORIGINAL_REPORT_SHA256 = '4e1d419f9e36a508b58c2a6d5dfe8d2bb0df06c6f840127e0b00f14fa893a0a7'
ENTRY_KEY = 'context_temperature_tiaodao_v1'
FAR_NAME = ENTRY_KEY + '.far'
LEXICON_NAME = 'lexicon-context-temperature-tiaodao-v1.txt'
LEFT = '#$|温度|$#'
MATCH = '调到'
REPLACEMENT = '#$|调到|$#'
NUMERALS = '零〇一二两三四五六七八九十百'
PHONES = ['t', 'iao2', '#0', 'd', 'ao4', '#0']
ADDED_LINE = '调到 t iao2 #0 d ao4 #0\n'
TARGET_IDS = ['W16-TN02', 'W16-TA02']
PATTERN = re.compile(r'(?<=#\$\|温度\|\$#)调到(?=[' + NUMERALS + r']+度)')
RULE_SPEC = {'schema_version': 1, 'kind': 'w16_temperature_context_rule',
             'entry_key': ENTRY_KEY, 'arc_type': 'standard', 'far_type': 'sttable',
             'token_type': 'byte', 'sigma': 'pynini.lib.byte.BYTE.star',
             'direction': 'ltr', 'mode': 'obl', 'left_literal': LEFT,
             'match_literal': MATCH, 'right_numerals': NUMERALS,
             'right_suffix': '度', 'replacement_literal': REPLACEMENT,
             'added_lexicon_line': ADDED_LINE,
             'normalizer_order': ['phone.fst', 'number.fst', 'original_rule.far[46 entries]', FAR_NAME],
             'candidate_normalization_steps_expected': 49,
             'factor': 'one inseparable on/off FAR plus sidecar-lexicon resource bundle'}


def now(): return datetime.now(timezone.utc).isoformat()
def read(path): return json.loads(Path(path).read_text())
def write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
def identity(path):
    path = Path(path).resolve()
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1048576), b''): h.update(block)
    return {'path': str(path), 'bytes': path.stat().st_size, 'sha256': h.hexdigest()}
def _path(path):
    p = Path(path).absolute()
    if any(q.is_symlink() for q in [p, *p.parents]): raise ValueError('Symlink data path refused: ' + str(p))
    return p.resolve()
def check_identity(item):
    p = Path(item['path'])
    p = p if p.is_absolute() else WORKSPACE / p
    actual = identity(p)
    if any(actual[k] != item[k] for k in ['bytes', 'sha256']):
        raise ValueError('Frozen file identity changed: ' + str(p))
    return p
def _fresh(outdir, sources):
    outdir = _path(outdir)
    if outdir.exists(): raise FileExistsError('Output exists; choose a fresh directory: ' + str(outdir))
    for source in sources:
        source = _path(source)
        if outdir.is_relative_to(source) or source.is_relative_to(outdir):
            raise ValueError('Output overlaps a frozen input: ' + str(source))
    return outdir
def _relative_file(root, name):
    name = PurePosixPath(name)
    if name.is_absolute() or '..' in name.parts or not name.parts: raise ValueError('Unsafe relative artifact')
    p = _path(Path(root) / str(name))
    if not p.is_file(): raise ValueError('Missing artifact: ' + str(p))
    return p


def compiler_identity(wheel):
    """Fingerprint actual installed compiler bytes; never infer an OpenFst commit."""
    import pynini
    import _pynini
    import _pywrapfst
    # Normalize import state before every /proc snapshot. Compilation and
    # rewrite validation use these modules, including any native dependencies.
    from pynini.lib import byte, rewrite
    wheel = Path(wheel).resolve()
    if identity(wheel)['sha256'] != WHEEL_SHA256: raise ValueError('Expected pinned cp310 wheel')
    if importlib.metadata.version('pynini') != PYNINI_VERSION or pynini.__version__ != PYNINI_VERSION:
        raise ValueError('Expected Pynini ' + PYNINI_VERSION)
    if sys.version_info[:2] != (3, 10): raise ValueError('Pinned wheel requires Python3.10')
    dist = importlib.metadata.distribution('pynini')
    files = {Path(_pynini.__file__).resolve(), Path(_pywrapfst.__file__).resolve()}
    for f in dist.files:
        p = Path(dist.locate_file(f)).resolve()
        if p.is_file() and (p.suffix == '.py' or '.so' in p.name or p.name in ['METADATA', 'WHEEL']): files.add(p)
    loaded = set()
    for line in Path('/proc/self/maps').read_text().splitlines():
        name = line.split()[-1]
        if name.startswith('/') and '.so' in name and Path(name).is_file(): loaded.add(Path(name).resolve())
    return {'kind': 'w16_pynini_compiler_identity', 'python_version': sys.version,
            'python_executable': identity(sys.executable), 'platform': platform.platform(),
            'pynini_distribution_version': importlib.metadata.version('pynini'),
            'pynini_module_version': pynini.__version__, 'wheel': identity(wheel),
            'installed_distribution_files': [identity(p) for p in sorted(files)],
            'loaded_shared_libraries': [identity(p) for p in sorted(loaded)],
            'all_installed_packages': {d.metadata['Name']: d.version for d in sorted(
                importlib.metadata.distributions(), key=lambda d: d.metadata['Name'].lower())},
            'openfst_version_scope': 'Wheel native-library filenames and bytes are frozen; exact upstream OpenFst source revision is not advertised or inferred.',
            'string_labels': 'BYTE: fixed kaldifst v1.8.0 TextNormalizer traverses uint8_t input/output labels.'}


def freeze_toolchain(wheel, outdir):
    outdir = _fresh(outdir, [wheel])
    tool = compiler_identity(wheel)
    outdir.mkdir(parents=True)
    write(outdir / 'toolchain.json', {'schema_version': 1, 'state': 'frozen', 'created_at': now(), 'compiler': tool})
    (outdir / 'requirements.txt').write_text('pynini==' + PYNINI_VERSION + ' --hash=sha256:' + WHEEL_SHA256 + '\n')
    return {'passed': True, 'toolchain': identity(outdir / 'toolchain.json')}


def validate_toolchain(path, *, current=False):
    path = _path(path)
    tool = read(path)
    if tool.get('schema_version') != 1 or tool.get('state') != 'frozen': raise ValueError('Expected frozen compiler')
    compiler = tool['compiler']
    if compiler['pynini_distribution_version'] != PYNINI_VERSION or compiler['wheel']['sha256'] != WHEEL_SHA256:
        raise ValueError('Compiler version/wheel differs')
    for a in [compiler['python_executable'], compiler['wheel'], *compiler['installed_distribution_files'],
              *compiler['loaded_shared_libraries']]: check_identity(a)
    if current and compiler_identity(compiler['wheel']['path']) != compiler:
        raise ValueError('Current compiler differs from frozen toolchain')
    return tool


def parse_tokens(data):
    result = {}
    for line in data.decode().splitlines():
        if line.strip():
            token, number = line.rsplit(maxsplit=1)
            result.setdefault(token, int(number))
    return result


def parse_lexicon(data, tokens):
    result = {}
    for line in data.decode().splitlines():
        fields = line.split()
        if len(fields) >= 2 and fields[0].lower() not in result and all(p in tokens for p in fields[1:]):
            result[fields[0].lower()] = fields[1:]
    return result


def sidecar_bytes(original, tokens):
    before = parse_lexicon(original, tokens)
    if MATCH in before: raise ValueError('Predeclared missing entry now exists; do not override it')
    if not all(p in tokens for p in PHONES): raise ValueError('New phones outside original alphabet')
    data = original + (b'' if original.endswith(b'\n') else b'\n') + ADDED_LINE.encode()
    after = parse_lexicon(data, tokens)
    if after != {**before, MATCH: PHONES}: raise ValueError('Sidecar changes original lexicon mappings')
    return data, {'passed': True, 'original_mapping_count': len(before), 'sidecar_mapping_count': len(after),
                  'original_bytes_prefix_preserved': data.startswith(original), 'added_line': ADDED_LINE,
                  'added_phones': PHONES, 'added_token_ids': [tokens[p] for p in PHONES],
                  'all_original_mappings_unchanged': True}


def expected_rewrite(text): return PATTERN.sub(lambda _: REPLACEMENT, text)
def surface(text): return text.replace('#$|', '').replace('|$#', '')


def original_inputs(preregistration, original_run):
    preregistration, original_run = _path(preregistration), _path(original_run)
    if identity(preregistration)['sha256'] != PREREG_SHA256: raise ValueError('Original preregistration changed')
    spec = read(preregistration)
    protocol_path = check_identity(spec['original_run_protocol'])
    if protocol_path.parent != original_run: raise ValueError('Expected original frozen B run')
    raw_path = check_identity(spec['original_raw_index'])
    protocol = read(protocol_path)
    fixture = check_identity(spec['fixture'])
    raw = read(raw_path)['artifacts']
    frozen = [identity(preregistration), identity(protocol_path), identity(raw_path), identity(fixture)]
    names = set()
    for a in raw:
        p = _relative_file(original_run, a['path'])
        if a['path'] in names: raise ValueError('Duplicate raw identity')
        names.add(a['path'])
        actual = identity(p)
        if any(actual[k] != a[k] for k in ['bytes', 'sha256']): raise ValueError('Original B raw artifact changed')
        frozen.append(actual)
    actual_names = set()
    for p in original_run.rglob('*'):
        if p.is_symlink(): raise ValueError('Symlink in frozen original run')
        if p.is_file(): actual_names.add(str(p.relative_to(original_run)))
    if len(raw) != 247 or actual_names != names | {'raw-index.json', 'summary.json', 'report.md'}:
        raise ValueError('Original frozen run file set changed')
    for name, digest in [('summary.json', ORIGINAL_SUMMARY_SHA256), ('report.md', ORIGINAL_REPORT_SHA256)]:
        item = identity(original_run / name)
        if item['sha256'] != digest: raise ValueError('Original failed summary/report changed')
        frozen.append(item)
    for a in spec['original_model_files']: frozen.append(identity(check_identity(a)))
    records = [read(p) for p in sorted((original_run / 'records').glob('*.json'))]
    records = [r for r in records if r['arm'] == 'B']
    if len(records) != 18 or [r['id'] for r in records] != [c['id'] for c in protocol['cases']]:
        raise ValueError('Exactly original B18, original order required')
    for r, case in zip(records, protocol['cases']):
        if r['case'] != case or not r['completed']: raise ValueError('Original cases or successful generation identity changed')
        if len(r['native_trace']['normalization_steps']) != 48: raise ValueError('Original B48 normalization trace required')
    return spec, protocol, records, list({a['path']: a for a in frozen}.values())


def compile_rule():
    import pynini
    from pynini.lib import byte
    with pynini.default_token_type('byte'):
        lit = lambda text: pynini.accep(pynini.escape(text), token_type='byte')
        right = pynini.union(*(lit(c) for c in NUMERALS)).plus + lit('度')
        rule = pynini.cdrewrite(pynini.cross(lit(MATCH), lit(REPLACEMENT)), lit(LEFT), right,
                               byte.BYTE.star, direction='ltr', mode='obl').optimize()
    rule.set_input_symbols(None)
    rule.set_output_symbols(None)
    if rule.arc_type() != 'standard' or rule.weight_type() != 'tropical': raise ValueError('Expected standard arcs')
    if any(not 0 <= label <= 255 for state in rule.states() for arc in rule.arcs(state)
           for label in [arc.ilabel, arc.olabel]): raise ValueError('Rule must use BYTE labels only')
    return rule


def compiled_rewrite(rule, text):
    from pynini.lib import rewrite
    return rewrite.one_top_rewrite(text, rule, input_token_type='byte', output_token_type='byte')


def far_structure(rule):
    labels = [label for state in rule.states() for arc in rule.arcs(state) for label in [arc.ilabel, arc.olabel]]
    return {'entries': [ENTRY_KEY], 'arc_type': rule.arc_type(), 'weight_type': rule.weight_type(),
            'fst_type': rule.fst_type(), 'states': rule.num_states(), 'token_type': 'byte',
            'min_label': min(labels), 'max_label': max(labels)}


def validate_far_structure(value):
    if (value['entries'] != [ENTRY_KEY] or value['arc_type'] != 'standard' or value['weight_type'] != 'tropical'
            or value['fst_type'] != 'vector' or value['token_type'] != 'byte'
            or type(value['states']) is not int or value['states'] <= 0
            or type(value['min_label']) is not int or type(value['max_label']) is not int
            or not 0 <= value['min_label'] <= value['max_label'] <= 255):
        raise ValueError('FAR must have one standard/vector/tropical BYTE entry')


def static_validation(rule, records):
    cases = []
    for row in records:
        text = row['native_trace']['normalization_steps'][-1]
        actual, expected = compiled_rewrite(rule, text), expected_rewrite(text)
        target = row['id'] in TARGET_IDS
        cases.append({'id': row['id'], 'input_normalized': text, 'output_normalized': actual,
                      'expected_normalized': expected, 'target': target, 'changed': actual != text,
                      'surface_preserved': surface(actual) == row['case']['reference_text'],
                      'passed': actual == expected and (actual != text) == target
                                and surface(actual) == row['case']['reference_text']})
    guards = [
        ('调查', '调查'), ('调到二十四度', '调到二十四度'),
        ('#$|亮度|$#调到二十四度', '#$|亮度|$#调到二十四度'),
        ('#$|温度|$#调查', '#$|温度|$#调查'),
        ('#$|温度|$#调到上海', '#$|温度|$#调到上海'),
        ('#$|温度|$#调到度', '#$|温度|$#调到度'),
        ('#$|温度|$#調到二十四度', '#$|温度|$#調到二十四度'),
        ('#$|温度|$#调到24度', '#$|温度|$#调到24度'),
        ('#$|温度|$#调到一度', '#$|温度|$##$|调到|$#一度'),
        ('#$|温度|$#调到九十九度', '#$|温度|$##$|调到|$#九十九度'),
        ('#$|温度|$#调到三十二度', '#$|温度|$##$|调到|$#三十二度')]
    checks = [{'input': text, 'expected': expected, 'actual': compiled_rewrite(rule, text)} for text, expected in guards]
    for check in checks: check['passed'] = check['actual'] == check['expected']
    passed = (len(cases) == 18 and [c['id'] for c in cases if c['changed']] == TARGET_IDS
              and all(c['passed'] for c in cases) and all(c['passed'] for c in checks))
    return {'passed': passed, 'cases': cases, 'guards': checks, 'targets_changed': 2,
            'non_target_cases_unchanged': 16, 'raw_text_surface_preserved': 18,
            'scope': 'Actual compiled FAR rewrite on saved original B normalized text; no TTS or acoustic inference.'}


def build(preregistration, original_run, toolchain, outdir):
    import pynini
    outdir = _fresh(outdir, [original_run, preregistration, toolchain, ROOT.parent / 'models'])
    tool = validate_toolchain(toolchain, current=True)
    spec, protocol, records, frozen = original_inputs(preregistration, original_run)
    model_dir = Path(protocol['model_dir'])
    tokens = parse_tokens((model_dir / 'tokens.txt').read_bytes())
    data, lexicon_check = sidecar_bytes((model_dir / 'lexicon.txt').read_bytes(), tokens)
    if lexicon_check['added_token_ids'] != spec['targets'][0]['expected_ids']:
        raise ValueError('New phones do not match preregistered IDs')
    source = {'commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
              'status': subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT, text=True).strip()}
    if source['status']: raise ValueError('Build requires clean committed sources')
    outdir.mkdir(parents=True)
    (outdir / LEXICON_NAME).write_bytes(data)
    write(outdir / 'rule-spec.json', RULE_SPEC)
    rule = compile_rule()
    with pynini.Far(str(outdir / FAR_NAME), mode='w', arc_type='standard', far_type='sttable') as far:
        far[ENTRY_KEY] = rule
    with pynini.Far(str(outdir / FAR_NAME), mode='r') as far:
        entries = list(far)
    if len(entries) != 1 or entries[0][0] != ENTRY_KEY: raise ValueError('Exactly one FAR entry required')
    actual_rule = entries[0][1]
    validation = static_validation(actual_rule, records)
    validation['lexicon'] = lexicon_check
    write(outdir / 'static-validation.json', validation)
    for a in frozen: check_identity(a)
    validate_toolchain(toolchain, current=True)
    resources = {'context_far': identity(outdir / FAR_NAME), 'sidecar_lexicon': identity(outdir / LEXICON_NAME)}
    artifacts = [{**identity(p), 'path': str(p.relative_to(outdir))} for p in sorted(outdir.iterdir()) if p.is_file()]
    manifest = {'schema_version': 1, 'kind': 'w16_temperature_context_bundle', 'state': 'frozen',
                'created_at': now(), 'passed': validation['passed'], 'source_git': source,
                'builder_source': identity(__file__), 'preregistration': identity(preregistration),
                'original_run_protocol': identity(original_run / 'protocol.json'),
                'original_raw_index': identity(original_run / 'raw-index.json'), 'fixture': identity(check_identity(spec['fixture'])),
                'compiler': identity(toolchain), 'compiler_details': tool['compiler'],
                'rule_spec': RULE_SPEC, 'resources': resources, 'static_validation': validation,
                'far_structure': far_structure(actual_rule),
                'frozen_inputs': frozen, 'artifacts': artifacts,
                'current_calls': {'tts': 0, 'asr': 0, 'playback': 0, 'microphone': 0, 'control': 0},
                'human_listening_accepted': False, 'personal_understanding_accepted': False,
                'automatic_product_promotion': False}
    write(outdir / 'manifest.json', manifest)
    if not validation['passed']: raise ValueError('Compiled static gates failed; retain full failed bundle')
    verify_bundle(outdir)
    return manifest


def verify_bundle(path):
    """Read-only byte/provenance/static-proof verification; no Pynini import."""
    path = _path(path)
    if path.name == 'manifest.json': path = path.parent
    m = read(_path(path / 'manifest.json'))
    if (m.get('schema_version') != 1 or m.get('kind') != 'w16_temperature_context_bundle'
            or m.get('state') != 'frozen' or m.get('passed') is not True or m.get('rule_spec') != RULE_SPEC):
        raise ValueError('Expected passed frozen temperature context bundle')
    if m['source_git']['status'] or not re.fullmatch(r'[0-9a-f]{40}', m['source_git']['commit']):
        raise ValueError('Clean committed builder source required')
    validate_far_structure(m['far_structure'])
    expected = {FAR_NAME, LEXICON_NAME, 'rule-spec.json', 'static-validation.json'}
    if len(m['artifacts']) != 4 or {a['path'] for a in m['artifacts']} != expected: raise ValueError('Bundle artifact set changed')
    actual = set()
    for p in path.rglob('*'):
        if p.is_symlink(): raise ValueError('Symlink bundle entry')
        if p.is_file(): actual.add(str(p.relative_to(path)))
    if actual != expected | {'manifest.json'}: raise ValueError('Bundle file set changed')
    for a in m['artifacts']:
        actual_id = identity(_relative_file(path, a['path']))
        if any(actual_id[k] != a[k] for k in ['bytes', 'sha256']): raise ValueError('Bundle artifact modified')
    for key, filename in [('context_far', FAR_NAME), ('sidecar_lexicon', LEXICON_NAME)]:
        if m['resources'][key] != identity(path / filename): raise ValueError('Resource identity/path differs')
    for a in [m['preregistration'], m['original_run_protocol'], m['original_raw_index'], m['fixture'],
              m['compiler'], m['builder_source'], *m['frozen_inputs']]: check_identity(a)
    if m['preregistration']['sha256'] != PREREG_SHA256: raise ValueError('Preregistration differs')
    validate_toolchain(m['compiler']['path'], current=False)
    if m['compiler_details'] != read(m['compiler']['path'])['compiler']: raise ValueError('Compiler metadata differs')
    validation = read(path / 'static-validation.json')
    if validation != m['static_validation'] or read(path / 'rule-spec.json') != RULE_SPEC: raise ValueError('Static proof/spec differs')
    cases = validation['cases']
    if (not validation['passed'] or len(cases) != 18 or [c['id'] for c in cases if c['changed']] != TARGET_IDS
            or not all(c['passed'] and c['surface_preserved'] for c in cases)
            or not all(g['passed'] for g in validation['guards'])): raise ValueError('Static gates fail')
    for k in ['human_listening_accepted', 'personal_understanding_accepted', 'automatic_product_promotion']:
        if m[k] is not False: raise ValueError('Build cannot accept human/product results')
    if any(m['current_calls'].values()): raise ValueError('Build cannot have model/device calls')
    spec, protocol, records, _ = original_inputs(m['preregistration']['path'], Path(m['original_run_protocol']['path']).parent)
    for saved, record in zip(cases, records):
        text = record['native_trace']['normalization_steps'][-1]
        if (saved['id'] != record['id'] or saved['input_normalized'] != text
                or saved['output_normalized'] != expected_rewrite(text)
                or saved['expected_normalized'] != expected_rewrite(text)
                or saved['target'] != (record['id'] in TARGET_IDS)
                or surface(saved['output_normalized']) != record['case']['reference_text']):
            raise ValueError('Static proof is not bound to original B18')
    model_dir = Path(protocol['model_dir'])
    sidecar, lexicon = sidecar_bytes((model_dir / 'lexicon.txt').read_bytes(), parse_tokens((model_dir / 'tokens.txt').read_bytes()))
    if ((path / LEXICON_NAME).read_bytes() != sidecar or validation['lexicon'] != lexicon):
        raise ValueError('Sidecar does not preserve exact original bytes/mappings')
    return m


def verify_compiled(path):
    import pynini
    m = verify_bundle(path)
    validate_toolchain(m['compiler']['path'], current=True)
    spec, protocol, records, _ = original_inputs(m['preregistration']['path'], Path(m['original_run_protocol']['path']).parent)
    with pynini.Far(m['resources']['context_far']['path']) as far: entries = list(far)
    if len(entries) != 1 or entries[0][0] != ENTRY_KEY: raise ValueError('FAR entry set differs')
    validate_far_structure(far_structure(entries[0][1]))
    if far_structure(entries[0][1]) != m['far_structure']: raise ValueError('FAR structural metadata differs')
    v = static_validation(entries[0][1], records)
    v['lexicon'] = sidecar_bytes((Path(protocol['model_dir']) / 'lexicon.txt').read_bytes(),
                               parse_tokens((Path(protocol['model_dir']) / 'tokens.txt').read_bytes()))[1]
    if v != m['static_validation']: raise ValueError('Compiled static recheck differs')
    return {'passed': True, 'manifest': identity(Path(m['resources']['context_far']['path']).parent / 'manifest.json'),
            'compiled_cases': 18, 'guard_cases': len(v['guards']), 'model_or_device_calls': 0}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    tool = sub.add_parser('freeze-toolchain')
    tool.add_argument('--wheel', type=Path, required=True); tool.add_argument('--outdir', type=Path, required=True)
    build_parser = sub.add_parser('build')
    build_parser.add_argument('--preregistration', type=Path, default=ROOT / 'configs/w16-context-repair-prereg-v1.json')
    build_parser.add_argument('--original-run', type=Path, default=ROOT / 'evaluation/20261113-w16-pronunciation-run')
    build_parser.add_argument('--toolchain', type=Path, required=True); build_parser.add_argument('--outdir', type=Path, required=True)
    verify = sub.add_parser('verify'); verify.add_argument('--bundle', type=Path, required=True); verify.add_argument('--compiled', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.action == 'freeze-toolchain': result = freeze_toolchain(args.wheel, args.outdir)
        elif args.action == 'build':
            m = build(args.preregistration, args.original_run, args.toolchain, args.outdir)
            result = {'passed': m['passed'], 'resources': m['resources'], 'source_git': m['source_git']}
        elif args.compiled: result = verify_compiled(args.bundle)
        else:
            m = verify_bundle(args.bundle); result = {'passed': m['passed'], 'scope': 'stdlib bytes/provenance; no compilation/model'}
        print(json.dumps(result, ensure_ascii=False, indent=2)); return 0
    except (ValueError, OSError, KeyError, TypeError, ImportError) as exc:
        print('ERROR: ' + str(exc), file=sys.stderr); return 2


if __name__ == '__main__': raise SystemExit(main())
