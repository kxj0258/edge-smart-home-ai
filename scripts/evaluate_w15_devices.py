"""Frozen, network-disabled Mock device/readback regressions.

Voice cases inject synthetic transcripts into the existing voice text boundary;
no ASR model, microphone, real HA or physical device is exercised.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / 'src'):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from scripts.run_unit_tests import network_disabled
from edge_smart_home.agent import AgentPlanValidator
from edge_smart_home.app.orchestrator import AssistantOrchestrator
from edge_smart_home.app.pipeline import TextControlPipeline
from edge_smart_home.audio.interfaces import StreamingRecognition
from edge_smart_home.audio.pipeline import VoiceControlPipeline
from edge_smart_home.common import DeviceCommand, DeviceState, EventSource
from edge_smart_home.device import MockDeviceBackend, SafetyPolicy
from edge_smart_home.router import CommandDeduplicator, FastPathRouter, SceneRouter

CONFIG_NAMES = ('devices', 'intents', 'routing', 'scenes', 'safety', 'home_assistant')
LIMITATIONS = [
    'Mock in-memory state and explicit readback only; no real HA or physical completion acceptance.',
    'Voice input is a synthetic transcript, not ASR accuracy, live microphone, VAD or overflow validation.',
    'No Ollama/model service or request_id idempotency; scene execution may partially succeed and is not atomic.',
    'A returned/request-accepted snapshot is not proof of a physical target; metadata scope stays explicit.',
    'Fixed development cases and one valid example per capability are not exhaustive safety or personal-understanding acceptance.',
]


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    with Path(path).open('x') as output:
        json.dump(value, output, ensure_ascii=False, indent=2, allow_nan=False)
        output.write('\n')


def now():
    return datetime.now(timezone.utc).isoformat()


def git():
    return {'commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
            'status': subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT, text=True)}


def identity(path):
    path = Path(path).resolve()
    return {'path': str(path), 'bytes': path.stat().st_size, 'sha256': sha(path)}


def sources():
    names = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode().split('\0')
    return {name: sha(ROOT / name) for name in sorted(names) if name and
            (name.startswith(('src/', 'scripts/', 'configs/', 'schemas/', 'tests/')) or name == 'pyproject.toml')}


def state_value(state):
    return {'device_id': state.device_id, 'available': state.available, 'attributes': dict(state.attributes),
            'updated_at': state.updated_at.isoformat(), 'backend_kind': state.backend_kind,
            'completion_scope': state.completion_scope}


def command_value(command):
    return {'device_id': command.device_id, 'action': command.action, 'arguments': dict(command.arguments)}


def command(value):
    return DeviceCommand(value['device_id'], value['action'], EventSource.TEXT, value.get('arguments', {}))


class RecordingMock(MockDeviceBackend):
    def __init__(self, devices):
        self.execution_records = []
        super().__init__(devices)

    def execute(self, cmd):
        row = {'command': command_value(cmd), 'returned': None, 'readback': None, 'error_code': None}
        try:
            result = super().execute(cmd)
        except Exception as error:
            row.update(error_code=getattr(error, 'error_code', 'UNCLASSIFIED_ERROR'),
                       error_type=type(error).__name__, error=str(error))
            self.execution_records.append(row)
            raise
        row.update(returned=state_value(result), readback=state_value(self.get_state(cmd.device_id)))
        row['returned_matches_immediate_readback'] = row['returned'] == row['readback']
        self.execution_records.append(row)
        return result


def snapshot(backend):
    return {state.device_id: state_value(state) for state in backend.list_states()}


def catalog_cases(devices):
    """Freeze declared per-capability examples before executing any case."""
    args = {'set_volume': {'volume': 40}, 'set_brightness': {'brightness': 40},
            'set_temperature': {'temperature': 24}, 'set_position': {'position': 40},
            'set_speed': {'speed': 2}, 'set_mode': {'mode': 'sleep'},
            'set_hvac_mode': {'mode': 'heat'}, 'open_app': {'app': 'IPTV'},
            'set_channel': {'channel': 'CCTV-5'}}
    fixed = {'turn_on': {'power': True}, 'turn_off': {'power': False},
             'set_volume': {'volume': 40}, 'set_brightness': {'brightness': 40, 'power': True},
             'set_temperature': {'temperature': 24, 'power': True},
             'set_hvac_mode': {'hvac_mode': 'heat', 'power': True},
             'open': {'position': 'open', 'position_percent': 100},
             'close': {'position': 'closed', 'position_percent': 0},
             'set_position': {'position': 'partial', 'position_percent': 40},
             'enable_guest_wifi': {'guest_wifi_enabled': True}, 'disable_guest_wifi': {'guest_wifi_enabled': False},
             'play': {'power': True, 'playback': 'playing'}, 'pause': {'power': True, 'playback': 'paused'},
             'resume': {'power': True, 'playback': 'playing'}, 'home': {'power': True, 'app': 'home', 'playback': 'stopped'},
             'next': {'power': True, 'playback': 'playing', 'position_seconds': 0},
             'previous': {'power': True, 'playback': 'playing', 'position_seconds': 0},
             'open_app': {'power': True, 'app': 'IPTV'},
             'set_channel': {'power': True, 'channel': 'CCTV-5', 'app': 'IPTV', 'playback': 'playing'},
             'enable_privacy': {'privacy_mode': True, 'recording': False}, 'disable_privacy': {'privacy_mode': False},
             'start_recording': {'recording': True}, 'stop_recording': {'recording': False},
             'lock': {'locked': True}, 'unlock': {'locked': False},
             'set_speed': {'speed': 2, 'power': True}, 'set_mode': {'mode': 'sleep', 'power': True}}
    result = []
    for device_id, device in devices.items():
        initial = device['initial_state']
        for action in device['capabilities']:
            expected = dict(fixed.get(action, {}))
            if action in ('volume_up', 'volume_down'):
                expected['volume'] = min(100, max(0, initial['volume'] + (5 if action == 'volume_up' else -5)))
            elif action in ('speed_up', 'speed_down'):
                expected['speed'] = min(3, max(1, initial['speed'] + (1 if action == 'speed_up' else -1)))
                if action == 'speed_up':
                    expected['power'] = True
            elif action == 'restart':
                expected['restart_count'] = initial['restart_count'] + 1
            elif action not in fixed and action not in ('query_state', 'show_feed'):
                raise ValueError('No frozen example oracle for configured capability: ' + action)
            prerequisite = ('disable_privacy' if action in ('show_feed', 'start_recording')
                            else 'play' if action == 'pause' else None)
            setup = ([{'command': {'device_id': device_id, 'action': prerequisite, 'arguments': {}},
                       'expected_error_code': None}] if prerequisite else [])
            result.append({'id': 'CAT-' + device_id + '-' + action, 'path': 'direct', 'category': 'catalog_capability',
                           'command': {'device_id': device_id, 'action': action, 'arguments': args.get(action, {})},
                           'setup': setup, 'expect': {'status': 'completed', 'error_code': None,
                                                     'attributes': {device_id: expected},
                                                     'unchanged': action in ('query_state', 'show_feed')}})
    return result


def validate_cases(data):
    if data.get('schema_version') != 1 or data.get('kind') != 'w15_device_regressions' or len(data.get('cases', [])) < 20:
        raise ValueError('Expected fixed W15 device regressions with at least twenty cases')
    seen = set()
    for case in data['cases']:
        if case['id'] in seen or case['path'] not in ('direct', 'text', 'voice', 'scene'):
            raise ValueError('Duplicate case or unknown input path')
        seen.add(case['id'])
        expected = case['expect']
        if not isinstance(expected['status'], str) or not isinstance(expected.get('attributes'), dict):
            raise ValueError('Missing fixed result oracle')
        if expected['error_code'] is not None and not isinstance(expected['error_code'], str):
            raise ValueError('Error oracle must be a string or null')


def scene_audit(config):
    errors = []
    for scene_id, scene in config['scenes'].items():
        for value in scene['commands']:
            device = config['devices'].get(value['device_id'])
            if device is None or value['action'] not in device['capabilities']:
                errors.append(scene_id + ':' + value['device_id'] + '.' + value['action'])
            else:
                from edge_smart_home.agent.interfaces import PlannedDeviceCommand
                AgentPlanValidator(config['devices']).validate([PlannedDeviceCommand(value['device_id'], value['action'], value.get('arguments', {}))])
    return {'scenes': len(config['scenes']), 'commands': sum(len(s['commands']) for s in config['scenes'].values()),
            'all_references_and_parameters_valid': not errors, 'errors': errors}


def prepare(args):
    args.outdir.mkdir(parents=True, exist_ok=False)
    source_git = git()
    if source_git['status']:
        raise ValueError('Prepare requires a clean committed child repository')
    data = read(args.cases)
    validate_cases(data)
    config = {name: read(args.config_dir / (name + '.json')) for name in CONFIG_NAMES}
    p = {'schema_version': 1, 'kind': 'w15_frozen_mock_protocol', 'prepared_at': now(),
         'source_git': source_git, 'python': platform.python_version(), 'sources_sha256': sources(),
         'cases_file': identity(args.cases), 'config_files': [identity(args.config_dir / (n + '.json')) for n in CONFIG_NAMES],
         'configs': config, 'cases': data['cases'], 'catalog_cases': catalog_cases(config['devices']),
         'scene_audit': scene_audit(config), 'backend_kind': 'mock', 'completion_scope': 'mock_state_readback',
         'acceptance': 'Every fixed case and capability example must match; source/record/readback integrity and zero network mandatory.',
         'limitations': LIMITATIONS}
    write(args.outdir / 'protocol.json', p)
    return {'passed': True, 'case_count': len(p['cases']), 'capability_examples': len(p['catalog_cases']), 'devices': len(config['devices'])}


def validate_protocol(p, current=False):
    if p['schema_version'] != 1 or p['kind'] != 'w15_frozen_mock_protocol' or p['backend_kind'] != 'mock':
        raise ValueError('Expected frozen Mock-only W15 protocol')
    validate_cases({'schema_version': 1, 'kind': 'w15_device_regressions', 'cases': p['cases']})
    if p['catalog_cases'] != catalog_cases(p['configs']['devices']) or p['scene_audit'] != scene_audit(p['configs']):
        raise ValueError('Frozen capability/scene oracle differs')
    if p['source_git']['status'] or 'scripts/evaluate_w15_devices.py' not in p['sources_sha256']:
        raise ValueError('Protocol must bind committed evaluator/product sources')
    if current:
        if git() != p['source_git'] or sources() != p['sources_sha256'] or platform.python_version() != p['python']:
            raise ValueError('Run requires exact clean source and Python identity')
        for item in [p['cases_file'], *p['config_files']]:
            if identity(item['path']) != item:
                raise ValueError('Frozen input/config file differs')


def make_orchestrator(backend, config):
    routing = config['routing']
    control = TextControlPipeline(FastPathRouter(config['devices'], config['intents'], routing.get('default_devices', {})),
                                  SceneRouter(config['scenes']), backend,
                                  SafetyPolicy(config['safety'].get('action_risks', {})), CommandDeduplicator(0))
    # No intelligent provider is needed for these fixed deterministic paths.
    return AssistantOrchestrator(control, plan_validator=AgentPlanValidator(config['devices']))


def outcome_value(outcome):
    if outcome is None:
        return None
    if isinstance(outcome, DeviceState):
        return {'state': state_value(outcome)}
    return {'scene_id': outcome.scene_id, 'succeeded': outcome.succeeded,
            'results': [{'command': command_value(v.command), 'succeeded': v.succeeded,
                         'state': state_value(v.state) if v.state else None, 'error': v.error,
                         'error_code': v.error_code} for v in outcome.results]}


def execute_case(case, config):
    backend = RecordingMock(config['devices'])
    setup = []
    for step in case.get('setup', []):
        if 'availability' in step:
            value = step['availability']
            backend.set_available(value['device_id'], value['value'])
            setup.append({'operation': step, 'matched': True})
        else:
            code = None
            try:
                backend.execute(command(step['command']))
            except Exception as error:
                code = getattr(error, 'error_code', 'UNCLASSIFIED_ERROR')
            setup.append({'operation': step, 'observed_error_code': code, 'matched': code == step.get('expected_error_code')})
    setup_executions = backend.execution_records
    backend.execution_records = []
    row = {'id': case['id'], 'path': case['path'], 'category': case['category'], 'case': case,
           'input_kind': 'synthetic_transcript' if case['path'] == 'voice' else 'authored_command',
           'backend_kind': 'mock', 'completion_scope': 'mock_state_readback',
           'before': snapshot(backend), 'setup': setup, 'setup_executions': setup_executions,
           'status': None, 'error_code': None, 'error': None, 'outcome': None, 'llm_calls': 0}
    started = perf_counter()
    try:
        if case['path'] == 'direct':
            state = backend.execute(command(case['command']))
            row.update(status='completed', outcome={'state': state_value(state)})
        else:
            orchestrator = make_orchestrator(backend, config)
            if case['path'] == 'voice':
                class ForbiddenInput:
                    def chunks(self):
                        raise AssertionError('Synthetic transcript probe must not capture audio')
                    def accept(self, chunk):
                        raise AssertionError('Synthetic transcript probe must not call ASR')
                    def finish(self):
                        raise AssertionError('Synthetic transcript probe must not finish ASR')
                voice = VoiceControlPipeline(ForbiddenInput(), ForbiddenInput(), orchestrator)
                response = voice.process_recognition(StreamingRecognition(case['text'], 0.0, 0.0))
                if response is None:
                    raise AssertionError('Fixed nonempty transcript produced no result')
                row.update(status=response.status, error_code=response.error_code, error=response.error,
                           outcome=outcome_value(response.state), llm_calls=response.llm_calls)
            else:
                response = orchestrator.handle_text(case['text'])
                row.update(status=response.status, error_code=response.error_code, error=response.error,
                           outcome=outcome_value(response.outcome), llm_calls=response.llm_calls)
    except Exception as error:
        row.update(status='error', error_code=getattr(error, 'error_code', 'UNCLASSIFIED_ERROR'),
                   error=f'{type(error).__name__}: {error}')
    row.update(wall_ms=(perf_counter() - started) * 1000, after=snapshot(backend), execution_records=backend.execution_records)
    row['checks'] = check_case(row)
    row['passed'] = all(row['checks'].values())
    return row


def check_case(row):
    expected = row['case']['expect']
    attributes_match = all(device in row['after'] and all(
        key in row['after'][device]['attributes'] and type(row['after'][device]['attributes'][key]) is type(value)
        and row['after'][device]['attributes'][key] == value for key, value in attrs.items())
        for device, attrs in expected['attributes'].items())
    touched = {r['command']['device_id'] for r in row['execution_records']}
    checks = {'status': row['status'] == expected['status'], 'error_code': row['error_code'] == expected['error_code'],
              'expected_attributes': attributes_match, 'setup': all(s['matched'] for s in row['setup']),
              'zero_llm_calls': row['llm_calls'] == 0,
              'mock_snapshot_scope': all(s['backend_kind'] == 'mock' and s['completion_scope'] == 'mock_state_snapshot'
                                         for s in [*row['before'].values(), *row['after'].values()]),
              'returned_matches_explicit_readback': all(r['returned'] is None or r['returned'] == r['readback'] for r in row['execution_records']),
              'failed_execution_has_no_success_snapshot': all(r['error_code'] is None or r['returned'] is None for r in row['execution_records'])}
    checks['unrelated_devices_unchanged'] = all(row['after'][device] == state for device, state in row['before'].items()
                                               if device not in touched)
    if expected.get('unchanged'):
        checks['whole_state_unchanged'] = row['before'] == row['after']
    if expected.get('timestamp') == 'unchanged':
        checks['attribute_timestamps_unchanged'] = all(s['updated_at'] == row['after'][key]['updated_at'] for key, s in row['before'].items())
    if 'scene_error_codes' in expected:
        observed = [r['error_code'] for r in row['outcome']['results'] if r['error_code']]
        checks['scene_error_codes'] = observed == expected['scene_error_codes']
        checks['scene_reports_failure'] = row['outcome']['succeeded'] is False
    return checks


def aggregate(p, rows, execution):
    expected = p['cases'] + p['catalog_cases']
    gates = {'exact_case_order_and_count': [r['id'] for r in rows] == [c['id'] for c in expected],
             'all_fixed_oracles_passed': bool(rows) and all(r['passed'] for r in rows),
             'scene_catalog_valid': p['scene_audit']['all_references_and_parameters_valid'],
             'zero_network_attempts': execution['network_attempts'] == [], 'no_global_failure': execution.get('error') is None}
    authored = [r for r in rows if r['category'] != 'catalog_capability']
    catalog = [r for r in rows if r['category'] == 'catalog_capability']
    return {'schema_version': 1, 'kind': 'w15_mock_device_results', 'passed': all(gates.values()), 'gates': gates,
            'runtime_source_commit': p['source_git']['commit'], 'devices': len(p['configs']['devices']),
            'scenes': p['scene_audit'], 'authored_cases': len(authored), 'authored_passed': sum(r['passed'] for r in authored),
            'capability_examples': len(catalog), 'capability_passed': sum(r['passed'] for r in catalog),
            'status_counts': dict(Counter(r['status'] for r in authored)),
            'error_code_counts': dict(Counter(r['error_code'] or 'OK_OR_NOT_APPLICABLE' for r in authored)),
            'mock_execute_attempts': sum(len(r['execution_records']) for r in rows),
            'mock_setup_execute_attempts': sum(len(r['setup_executions']) for r in rows),
            'mock_successful_readbacks': sum(r['returned'] is not None for row in rows for r in row['execution_records']),
            'network_attempts': len(execution['network_attempts']), 'real_ha_calls': 0, 'physical_device_calls': 0,
            'asr_model_calls': 0, 'ollama_calls': 0, 'microphone_calls': 0,
            'backend_kind': 'mock', 'completion_scope': 'mock_state_readback',
            'physical_completion_accepted': False, 'personal_understanding_accepted': False, 'limitations': p['limitations']}


def markdown(summary, rows):
    lines = ['# W15 Mock control/readback regression', '', 'Engineering evidence passed: ' + str(summary['passed']), '',
             '| ID | Path | Status | Code | Passed |', '|---|---|---|---|---|']
    lines += [f"| {r['id']} | {r['path']} | {r['status']} | {r['error_code'] or ''} | {r['passed']} |" for r in rows]
    lines += ['', '## Boundaries', ''] + ['- ' + x for x in summary['limitations']]
    return '\n'.join(lines) + '\n'


def run(args):
    args.outdir.mkdir(parents=True, exist_ok=False)
    p = read(args.protocol)
    validate_protocol(p, current=True)
    write(args.outdir / 'protocol.json', p)
    (args.outdir / 'records').mkdir()
    rows = []
    execution = {'started_at': now(), 'finished_at': None, 'source_git': git(), 'network_attempts': [], 'error': None}
    with network_disabled() as attempts:
        try:
            with (args.outdir / 'ledger.jsonl').open('x') as ledger:
                for index, case in enumerate(p['cases'] + p['catalog_cases']):
                    ledger.write(json.dumps({'event': 'case_started', 'index': index, 'id': case['id']}) + '\n'); ledger.flush()
                    row = execute_case(case, p['configs'])
                    rows.append(row)
                    write(args.outdir / 'records' / f'{index:04d}.json', row)
                    ledger.write(json.dumps({'event': 'case_recorded', 'index': index, 'id': case['id'], 'passed': row['passed']}) + '\n'); ledger.flush()
        except Exception as error:
            execution['error'] = {'type': type(error).__name__, 'detail': str(error)}
        execution['network_attempts'] = list(attempts)
    execution['finished_at'] = now()
    write(args.outdir / 'execution.json', execution)
    raw = [args.outdir / 'protocol.json', args.outdir / 'execution.json', *sorted((args.outdir / 'records').glob('*.json'))]
    if (args.outdir / 'ledger.jsonl').exists():
        raw.append(args.outdir / 'ledger.jsonl')
    write(args.outdir / 'raw-index.json', {'artifacts': [{'path': str(f.relative_to(args.outdir)), 'bytes': f.stat().st_size, 'sha256': sha(f)} for f in raw]})
    result = aggregate(p, rows, execution)
    write(args.outdir / 'summary.json', result)
    (args.outdir / 'report.md').write_text(markdown(result, rows))
    return result


def report(args):
    args.outdir.mkdir(parents=True, exist_ok=False)
    raw_index = read(args.run / 'raw-index.json')['artifacts']
    files = {'protocol.json', 'execution.json', 'ledger.jsonl', *[str(f.relative_to(args.run)) for f in (args.run / 'records').glob('*.json')]}
    if {v['path'] for v in raw_index} != files or len(raw_index) != len(files):
        raise ValueError('Raw index must cover exact protocol/execution/ledger/record set')
    for value in raw_index:
        f = args.run / value['path']
        if f.stat().st_size != value['bytes'] or sha(f) != value['sha256']:
            raise ValueError('Raw evidence hash mismatch')
    p = read(args.run / 'protocol.json')
    validate_protocol(p)
    rows = [read(f) for f in sorted((args.run / 'records').glob('*.json'))]
    cases = p['cases'] + p['catalog_cases']
    for index, row in enumerate(rows):
        if (index >= len(cases) or row['case'] != cases[index]
                or any(row[key] != cases[index][key] for key in ('id', 'path', 'category'))
                or row['input_kind'] != ('synthetic_transcript' if row['path'] == 'voice' else 'authored_command')
                or row['backend_kind'] != 'mock' or row['completion_scope'] != 'mock_state_readback'
                or row['checks'] != check_case(row) or row['passed'] != all(check_case(row).values())):
            raise ValueError('Recorded case/oracle/check differs')
    ledger = [json.loads(line) for line in (args.run / 'ledger.jsonl').read_text().splitlines()]
    expected_ledger = [event for i, row in enumerate(rows) for event in (
        {'event': 'case_started', 'index': i, 'id': row['id']},
        {'event': 'case_recorded', 'index': i, 'id': row['id'], 'passed': row['passed']})]
    if ledger != expected_ledger:
        raise ValueError('Recorded ledger order/count differs')
    result = aggregate(p, rows, read(args.run / 'execution.json'))
    write(args.outdir / 'summary.json', result)
    (args.outdir / 'report.md').write_text(markdown(result, rows))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='operation', required=True)
    prep = sub.add_parser('prepare')
    prep.add_argument('--cases', type=Path, default=ROOT / 'configs/evaluation/w15-device-regressions-v1.json')
    prep.add_argument('--config-dir', type=Path, default=ROOT / 'configs')
    for cmd in (prep, sub.add_parser('run'), sub.add_parser('report')):
        cmd.add_argument('--outdir', type=Path, required=True)
    sub.choices['run'].add_argument('--protocol', type=Path, required=True)
    sub.choices['report'].add_argument('--run', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        value = {'prepare': prepare, 'run': run, 'report': report}[args.operation](args)
    except Exception as error:
        print(json.dumps({'passed': False, 'error': f'{type(error).__name__}: {error}'}), file=sys.stderr)
        return 2
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0 if value['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
