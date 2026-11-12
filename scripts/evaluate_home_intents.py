"""W12: frozen model-only intent evaluation, separate guards/rules, no execution."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]
from inspect_llm_backend import MetadataClient, identity, load_config, sha256, snapshot, write_json, server_rates
from edge_smart_home.agent.contract import AGENT_RESPONSE_KINDS, parse_agent_response
from edge_smart_home.agent.evaluation_errors import evaluate_model_response, diagnose_fixture_suite
from edge_smart_home.agent.local_llm import build_agent_prompt, UrllibJsonTransport, LlmHttpError
from edge_smart_home.agent.rule_based import RuleBasedIntelligentPath
from edge_smart_home.agent.validation import AgentPlanValidator
from edge_smart_home.common import EventSource, PerceptionEvent
from edge_smart_home.device.mock import MockDeviceBackend

MODEL_DIGEST = '359d7dd4bcdab3d86b87d73ac27966f4dbb9f5efdfcc75d34a8764a09474fae7'
SERVER_VERSION = '0.40.2'
CORPUS = ROOT / 'configs/evaluation/w12-home-intents-v1.json'
REFERENCE = 'https://github.com/ollama/ollama/blob/b061384d90ff455462bc32745dd0de479de717a3/docs/api.md'


def now():
    return datetime.now(timezone.utc).isoformat()


def signature(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def git_info():
    def call(*args):
        return subprocess.check_output(['git', '-C', str(ROOT), *args], text=True).strip()
    return {'commit': call('rev-parse', 'HEAD'), 'status': call('status', '--short')}


def normalized(result):
    return {'kind': result.kind.value, 'message': result.message, 'reason': result.reason,
            'commands': [{'device_id': c.device_id, 'action': c.action, 'arguments': dict(c.arguments)} for c in result.commands]}


def validate_corpus(corpus, devices, max_commands):
    if corpus.get('schema_version') != 1 or corpus.get('kind') != 'w12_home_intent_cases':
        raise ValueError('Unsupported W12 corpus schema')
    cases = corpus.get('cases')
    if not isinstance(cases, list) or len(cases) < 20:
        raise ValueError('At least20 frozen cases required')
    ids = set(); counts = Counter()
    for case in cases:
        if not isinstance(case, dict) or not isinstance(case.get('id'), str) or not case['id'] or case['id'] in ids:
            raise ValueError('Case IDs must be nonempty and unique')
        ids.add(case['id']); counts[case['category']] += 1
        if case['category'] not in {'normal', 'ambiguous', 'illegal', 'deferred'}:
            raise ValueError('Unknown case category')
        if not isinstance(case.get('text'), str) or not case['text'].strip() or len(case['text']) > 512:
            raise ValueError('Case text must be nonempty and fit original512-character prompt boundary')
        expected = case['expected']; kinds = expected['allowed_kinds']
        if not isinstance(kinds, list) or not kinds or len(set(kinds)) != len(kinds) or set(kinds) - AGENT_RESPONSE_KINDS:
            raise ValueError('Expected kinds must be explicit existing contract kinds')
        if expected.get('must_not_execute') is not True:
            raise ValueError('All W12 cases must explicitly forbid device execution')
        if not isinstance(expected.get('commands'), list): raise ValueError('Expected commands must be explicit')
        for kind in kinds:
            result = parse_agent_response(json.dumps({'kind': kind, 'message': 'frozen oracle', 'commands': expected['commands']}), max_commands=max_commands)
            if result.commands: AgentPlanValidator(devices, max_commands).validate(result.commands)
        if case['category'] in {'ambiguous', 'illegal', 'deferred'} and ('plan' in kinds or expected['commands']):
            raise ValueError('Ambiguous/illegal/deferred oracle cannot allow an immediate plan')
        if case['category'] == 'deferred' and kinds != ['unavailable']:
            raise ValueError('Timer capability is deferred, expected unavailable only')
        if not isinstance(case.get('provenance'), dict): raise ValueError('Case provenance is required')
    if any(counts[category] == 0 for category in ['normal', 'ambiguous', 'illegal', 'deferred']):
        raise ValueError('Normal, ambiguous, illegal and deferred coverage all required')
    return cases


def source_hashes(corpus_path, corpus):
    names = ['scripts/evaluate_home_intents.py', 'scripts/inspect_llm_backend.py',
             'src/edge_smart_home/agent/evaluation_errors.py', 'src/edge_smart_home/agent/contract.py',
             'src/edge_smart_home/agent/local_llm.py', 'src/edge_smart_home/agent/rule_based.py',
             'src/edge_smart_home/agent/validation.py', 'src/edge_smart_home/agent/interfaces.py',
             'src/edge_smart_home/device/mock.py', 'src/edge_smart_home/common/timing_policy.py',
             'src/edge_smart_home/app/pipeline.py', 'src/edge_smart_home/app/orchestrator.py', 'configs/agent.json', 'configs/devices.json',
             'schemas/agent_response.schema.json', 'configs/llm-model-source-v1.json']
    paths = {ROOT / name for name in names}; paths.add(Path(corpus_path).resolve())
    for source in corpus.get('source_files', []):
        path = (ROOT / source['path']).resolve()
        if not path.is_relative_to(ROOT): raise ValueError('Historical source path escapes child repository')
        if not path.is_file() and source.get('role', '').startswith('historical_raw_run_'):
            continue  # Old raw is optional provenance, not current-model quality.
        if sha256(path) != source['sha256']: raise ValueError('Historical source provenance hash differs')
        paths.add(path)
    return {str(path.relative_to(ROOT)): sha256(path) for path in sorted(paths)}


def check_sources(p):
    for actual, frozen in [(ROOT/'configs/agent.json', p['config']), (ROOT/'configs/devices.json', p['devices']),
                           (ROOT/'schemas/agent_response.schema.json', p['schema']), (Path(p['corpus_path']), p['corpus'])]:
        if read_json(actual) != frozen: raise ValueError('Frozen inline input differs from source file: ' + str(actual))
    for name, digest in p['sources_sha256'].items():
        path = (ROOT / name).resolve()
        if not path.is_relative_to(ROOT) or not path.is_file() or sha256(path) != digest:
            raise ValueError('Frozen source/configuration/corpus changed: ' + name)


def payloads(config, devices, schema, cases):
    states = MockDeviceBackend(devices).list_states()
    o = config['ollama']
    result = []
    for index, case in enumerate(cases):
        prompt = build_agent_prompt(PerceptionEvent(source=EventSource.TEXT, label=case['text']), devices, states, schema, config['max_commands'])
        payload = {'model': o['model'], 'prompt': prompt, 'format': schema, 'stream': False,
                   'think': o['think'], 'keep_alive': o['keep_alive'],
                   'options': {'temperature': o['temperature'], 'num_predict': o['max_output_tokens'], 'seed': o['seed']}}
        result.append({'call_index': index, 'case_id': case['id'], 'payload': payload, 'payload_sha256': signature(payload)})
    return result


def validate_protocol(p):
    if p.get('schema_version') != 1 or p.get('kind') != 'w12_frozen_intent_protocol' or p.get('state') != 'frozen':
        raise ValueError('Expected frozen W12 protocol')
    if p['expected_digest'] != MODEL_DIGEST or p['expected_version'] != SERVER_VERSION:
        raise ValueError('W12 must retain W10/W11 model and server identity')
    o = p['config']['ollama']
    if (o['model'] != 'qwen3:4b' or o['think'] is not False or o['temperature'] != 0 or o['seed'] != 42
            or o['max_output_tokens'] != 256 or o['keep_alive'] != '10m'):
        raise ValueError('Expected unchanged original false/256/temperature0/seed42/keep_alive10m configuration')
    cases = validate_corpus(p['corpus'], p['devices'], p['config']['max_commands'])
    if signature(p['corpus']) != p['corpus_content_sha256']:
        raise ValueError('Frozen literal oracle content hash differs')
    if payloads(p['config'], p['devices'], p['schema'], cases) != p['requests']:
        raise ValueError('Frozen requests differ from original product prompt/schema/configuration')
    if p['acceptance_policy'] != acceptance_policy(): raise ValueError('Frozen acceptance policy differs')
    return p


def acceptance_policy():
    return {'minimum_cases': 20, 'one_model_call_per_case': True, 'no_retry_or_adaptive_tuning': True,
            'mandatory': ['evidence_integrity', 'all_attempts_classified', 'all_model_responses_completed',
                          'all_wire_contracts_valid', 'all_guarded_plans_valid'],
            'semantic_accuracy': 'Report raw-model and guarded exact-kind/commands matches separately; not required100percent; no post-hoc oracle changes',
            'timer': 'explicitly deferred/unavailable; no scheduler and no device execution',
            'malformed_output_fixtures': 'offline injected diagnostics only, never counted as actual model errors'}


def prepare(args, client=None):
    if args.config_dir.resolve() != (ROOT/'configs').resolve():
        raise ValueError('W12 reuses the canonical product configs directory; no candidate configuration override')
    config = load_config(args.config_dir); devices = read_json(args.config_dir / 'devices.json')
    schema = read_json(args.config_dir.parent / 'schemas/agent_response.schema.json')
    corpus_path = args.corpus.resolve()
    if not corpus_path.is_relative_to(ROOT): raise ValueError('Corpus must be inside child repository')
    corpus = read_json(corpus_path); cases = validate_corpus(corpus, devices, config['max_commands'])
    git = git_info()
    if git['status']: raise ValueError('Prepare requires committed clean child repository')
    client = client or MetadataClient(config['ollama'])
    before = snapshot(client, args.outdir / 'identity')
    pinned = identity(before, config['ollama']['model'], MODEL_DIGEST, SERVER_VERSION)
    p = {'schema_version': 1, 'kind': 'w12_frozen_intent_protocol', 'state': 'frozen', 'created_at': now(),
         'expected_digest': MODEL_DIGEST, 'expected_version': SERVER_VERSION, 'model_identity': pinned,
         'config_dir': str(args.config_dir.resolve()), 'corpus_path': str(corpus_path),
         'config': config, 'devices': devices, 'schema': schema, 'corpus': corpus,
         'corpus_content_sha256': signature(corpus), 'requests': payloads(config, devices, schema, cases),
         'sources_sha256': source_hashes(corpus_path, corpus), 'prepared_git': git,
         'acceptance_policy': acceptance_policy(), 'reference': REFERENCE,
         'scope': 'Raw model-only calls, separate read-only request guard and separate offline rule baseline; no full routed/fast-path/execution claim',
         'generation_calls': 0, 'explicit_load_calls': 0, 'explicit_unload_calls': 0}
    validate_protocol(p); write_json(args.outdir / 'protocol.json', p)
    return {'passed': True, 'protocol': str(args.outdir / 'protocol.json'), 'generation_calls': 0, 'cases': len(cases)}


def transport_error(error):
    chain = []; current = error
    while current is not None and len(chain) < 8:
        chain.append(type(current).__name__); current = current.__cause__
    category = 'transport_error'
    if isinstance(error, LlmHttpError): category = 'transport_http_error'
    elif any(t in chain for t in ['JSONDecodeError', 'UnicodeDecodeError']): category = 'outer_json_decode'
    elif any(t in chain for t in ['TimeoutError', 'timeout']): category = 'transport_timeout'
    return {'stage': 'transport', 'category': category, 'exception_type': type(error).__name__,
            'cause_types': chain, 'message': str(error), 'http_status': getattr(error, 'status', None)}


def envelope_errors(response, model):
    def error(category, message): return {'stage': 'envelope', 'category': category, 'message': message}
    if not isinstance(response, dict): return [error('outer_json_type', 'Outer response is not an object')]
    errors = []
    if response.get('model') != model or any(response.get(k) for k in ('remote_host', 'remote_model')):
        errors.append(error('model_identity', 'Response model is not the pinned local tag'))
    if response.get('done') is not True: errors.append(error('generation_incomplete', 'Expected explicit done=true'))
    elif response.get('done_reason') != 'stop': errors.append(error('generation_truncated', 'Expected normal stop; observed ' + str(response.get('done_reason'))))
    if response.get('error'): errors.append(error('server_error', str(response['error'])))
    text = response.get('response')
    if not isinstance(text, str) or not text.strip(): errors.append(error('empty_model_response', 'No nonempty response text'))
    if response.get('thinking') or (isinstance(text, str) and ('<think' in text.lower() or '</think' in text.lower())):
        errors.append(error('thinking_policy_violation', 'Observed thinking under frozen false-request policy'))
    return errors


def semantic_match(value, case):
    if value is None: return {'passed': False, 'kind_match': False, 'commands_match': False}
    kind_match = value['kind'] in case['expected']['allowed_kinds']
    commands_match = value['commands'] == case['expected']['commands']
    return {'passed': bool(kind_match and commands_match), 'kind_match': bool(kind_match), 'commands_match': commands_match}


def analyze_generation(generation, case, p):
    errors = [generation['error']] if generation.get('error') else envelope_errors(generation.get('response'), p['config']['ollama']['model'])
    response = generation.get('response'); text = response.get('response') if isinstance(response, dict) else None
    diagnosis = None
    if isinstance(text, str) and text.strip():
        diagnosis = evaluate_model_response(text, devices=p['devices'], max_commands=p['config']['max_commands'],
                                           event=PerceptionEvent(source=EventSource.TEXT, label=case['text']))
        errors += diagnosis['errors']
    parsed = diagnosis.get('parsed_result') if diagnosis else None
    guarded = diagnosis.get('guard_result') if diagnosis else None
    envelope_ok = not generation.get('error') and not envelope_errors(response, p['config']['ollama']['model'])
    completed = (isinstance(response, dict) and response.get('done') is True and response.get('done_reason') == 'stop')
    wire_ok = bool(envelope_ok and diagnosis and diagnosis['lexical_accepted'] and diagnosis['product_parse_accepted'])
    guarded_ok = bool(wire_ok and guarded is not None and (guarded['kind'] != 'plan' or diagnosis['plan_validation_accepted'] is True))
    raw_match = semantic_match(parsed, case); guarded_match = semantic_match(guarded, case)
    raw_match['passed'] = bool(wire_ok and raw_match['passed'])
    guarded_match['passed'] = bool(guarded_ok and guarded_match['passed'])
    if wire_ok and not raw_match['passed']: errors.append({'stage': 'oracle_raw', 'category': 'semantic_mismatch', 'message': 'Raw model kind/commands differ from frozen oracle'})
    if guarded_ok and not guarded_match['passed']: errors.append({'stage': 'oracle_guarded', 'category': 'semantic_mismatch', 'message': 'Guarded kind/commands differ from frozen oracle'})
    raw_unsafe = bool(parsed and parsed['kind'] == 'plan' and (case['category'] != 'normal' or not diagnosis.get('raw_plan_validation_accepted', False)))
    guarded_unsafe = bool(guarded and guarded['kind'] == 'plan' and (case['category'] != 'normal' or not diagnosis['plan_validation_accepted']))
    rule = RuleBasedIntelligentPath(p['config']['rules']).route(PerceptionEvent(source=EventSource.TEXT, label=case['text']))
    rule_value = normalized(rule); rule_error = None
    if rule.commands:
        try: AgentPlanValidator(p['devices'], p['config']['max_commands']).validate(rule.commands)
        except ValueError as error: rule_error = {'category': 'plan_validation', 'exception_type': type(error).__name__, 'message': str(error)}
    rule_match = semantic_match(rule_value, case); rule_match['passed'] = bool(rule_match['passed'] and rule_error is None)
    return {'model_completed': completed, 'envelope_valid': envelope_ok, 'wire_contract_valid': wire_ok,
            'guarded_plan_valid': guarded_ok, 'diagnosis': diagnosis, 'raw_semantic_match': raw_match,
            'guarded_semantic_match': guarded_match, 'raw_unsafe_plan': raw_unsafe, 'guarded_unsafe_plan': guarded_unsafe,
            'primary_error': errors[0] if errors else None, 'errors': errors,
            'rule_only_baseline': {'result': rule_value, 'plan_validation_error': rule_error, 'semantic_match': rule_match,
                                   'llm_calls': 0, 'scope': 'RuleBasedIntelligentPath only; not Fast Path or routed-final; no JSON-wire validity claim'},
            'server_rates_not_benchmark': server_rates(response), 'client_ttft_ms': None,
            'client_ttft_status': 'NOT_MEASURED: original stream=false transport'}


def run(args, client=None, transport=None):
    p = validate_protocol(read_json(args.protocol)); check_sources(p)
    git = git_info()
    if git['status']: raise ValueError('Run requires committed clean child repository')
    client = client or MetadataClient(p['config']['ollama']); transport = transport or UrllibJsonTransport()
    write_json(args.outdir / 'protocol.json', p); (args.outdir / 'records').mkdir()
    status = {'kind': 'w12_current_model_evaluation', 'started_at': now(), 'code_git': git,
              'protocol_sha256': sha256(args.outdir / 'protocol.json'), 'completed_calls': 0, 'evidence_integrity': False,
              'explicit_load_calls': 0, 'explicit_unload_calls': 0, 'device_execution_calls': 0, 'error': None}
    first = None; failure = None
    fixtures = diagnose_fixture_suite(p['devices'])
    write_json(args.outdir / 'offline-diagnostic-fixtures.json', fixtures)
    if not all(f['matched'] for f in fixtures): raise ValueError('Offline injected diagnostic fixture failed before model generation')
    try:
        before = snapshot(client, args.outdir / 'before')
        first = identity(before, p['config']['ollama']['model'], MODEL_DIGEST, SERVER_VERSION)
        if first != p['model_identity']: raise ValueError('Prepared model/API identity differs from run')
        with (args.outdir / 'calls.jsonl').open('x', encoding='utf-8') as ledger:
            for request, case in zip(p['requests'], p['corpus']['cases']):
                record = {'call_index': request['call_index'], 'case_id': case['id'], 'category': case['category'],
                          'text': case['text'], 'expected': case['expected'], 'payload': request['payload'],
                          'payload_sha256': request['payload_sha256'], 'started_at': now(), 'model_calls': 1, 'device_execution_calls': 0}
                ledger.write(json.dumps({'event': 'attempt', **record}, ensure_ascii=False) + '\n'); ledger.flush()
                generation = {'response': None, 'error': None}; started = perf_counter()
                try:
                    generation['response'] = transport.post_json(p['config']['ollama']['endpoint'], request['payload'],
                        connect_timeout_seconds=p['config']['ollama']['connect_timeout_seconds'],
                        total_timeout_seconds=p['config']['ollama']['total_timeout_seconds'])
                except Exception as error: generation['error'] = transport_error(error)
                generation['client_final_wall_ms'] = (perf_counter() - started) * 1000
                record.update(generation=generation, analysis=analyze_generation(generation, case, p), finished_at=now())
                write_json(args.outdir / 'records' / f"{request['call_index']:05d}.json", record)
                ledger.write(json.dumps({'event': 'completed', 'call_index': request['call_index'], 'case_id': case['id']}) + '\n'); ledger.flush()
                status['completed_calls'] += 1
        check_sources(p); status['evidence_integrity'] = True
    except Exception as error: failure = {'type': type(error).__name__, 'message': str(error)}
    finally:
        try:
            after = snapshot(client, args.outdir / 'after')
            last = identity(after, p['config']['ollama']['model'], MODEL_DIGEST, SERVER_VERSION)
            if first is not None and last != first: raise ValueError('Model/API identity drift during evaluation')
            check_sources(p)
        except Exception as error: failure = failure or {'type': type(error).__name__, 'message': str(error)}
        status['evidence_integrity'] = bool(status['evidence_integrity'] and failure is None)
        status.update(error=failure, finished_at=now()); write_json(args.outdir / 'run-status.json', status)
        write_json(args.outdir / 'raw-index.json', {'sha256': {str(f.relative_to(args.outdir)): sha256(f) for f in sorted(args.outdir.rglob('*')) if f.is_file()}})
    summary = rebuild(args.outdir); write_reports(args.outdir, summary)
    return {'passed': summary['passed'], 'outdir': str(args.outdir), 'calls': status['completed_calls'],
            'raw_exact_matches': summary['model_raw_exact_matches'], 'guarded_exact_matches': summary['guarded_exact_matches'],
            'total': summary['case_count'], 'error': failure}


def verify_artifacts(directory):
    index = read_json(directory / 'raw-index.json')['sha256']
    for name, digest in index.items():
        path = (directory / name).resolve()
        if not path.is_relative_to(directory.resolve()) or not path.is_file() or sha256(path) != digest:
            raise ValueError('Raw artifact missing or changed: ' + name)
    actual = {str(p.relative_to(directory)) for p in (directory / 'records').glob('*.json')}
    if actual != {n for n in index if n.startswith('records/')}:
        raise ValueError('Unindexed or missing model record')


def aggregate(records):
    analyses = [r['analysis'] for r in records]
    errors = Counter(a['primary_error']['category'] if a['primary_error'] else 'ok' for a in analyses)
    all_errors = Counter(e['category'] for a in analyses for e in a['errors'])
    confusion = Counter((r['expected']['allowed_kinds'][0], ((r['analysis']['diagnosis'] or {}).get('parsed_result') or {}).get('kind', 'unparsed')) for r in records)
    return {'case_count': len(records), 'model_completed_count': sum(a['model_completed'] for a in analyses),
            'wire_contract_valid_count': sum(a['wire_contract_valid'] for a in analyses),
            'guarded_plan_valid_count': sum(a['guarded_plan_valid'] for a in analyses),
            'model_raw_exact_matches': sum(a['raw_semantic_match']['passed'] for a in analyses),
            'guarded_exact_matches': sum(a['guarded_semantic_match']['passed'] for a in analyses),
            'rule_only_exact_matches': sum(a['rule_only_baseline']['semantic_match']['passed'] for a in analyses),
            'raw_plan_validation_checked_count': sum(bool(a['diagnosis'] and a['diagnosis'].get('parsed_result') and a['diagnosis']['parsed_result']['kind']=='plan') for a in analyses),
            'guarded_plan_validation_checked_count': sum(bool(a['diagnosis'] and a['diagnosis'].get('guard_result') and a['diagnosis']['guard_result']['kind']=='plan') for a in analyses),
            'raw_plan_validation_error_count': sum(bool(a['diagnosis'] and a['diagnosis'].get('raw_plan_validation_error')) for a in analyses),
            'guarded_plan_validation_error_count': sum(bool(a['diagnosis'] and a['diagnosis'].get('plan_validation_error')) for a in analyses),
            'raw_unsafe_plan_count': sum(a['raw_unsafe_plan'] for a in analyses),
            'guarded_unsafe_plan_count': sum(a['guarded_unsafe_plan'] for a in analyses),
            'primary_error_counts': dict(sorted(errors.items())), 'all_stage_error_counts_not_disjoint': dict(sorted(all_errors.items())),
            'raw_kind_confusion': [{'expected_kind': a, 'actual_kind': b, 'count': n} for (a,b),n in sorted(confusion.items())],
            'raw_semantic_mismatch_ids': [r['case_id'] for r in records if not r['analysis']['raw_semantic_match']['passed']],
            'guarded_semantic_mismatch_ids': [r['case_id'] for r in records if not r['analysis']['guarded_semantic_match']['passed']]}


def rebuild(directory):
    verify_artifacts(directory); p = validate_protocol(read_json(directory / 'protocol.json')); check_sources(p)
    status = read_json(directory / 'run-status.json')
    fixtures = read_json(directory / 'offline-diagnostic-fixtures.json')
    if fixtures != diagnose_fixture_suite(p['devices']) or not all(f['matched'] for f in fixtures):
        raise ValueError('Offline diagnostic fixture results differ from frozen runtime classification')
    if status['protocol_sha256'] != sha256(directory / 'protocol.json'): raise ValueError('Protocol hash differs from run status')
    before = {name: read_json(directory / 'before' / (name+'.json')) for name in ('version','tags','show','ps')}
    after = {name: read_json(directory / 'after' / (name+'.json')) for name in ('version','tags','show','ps')}
    for raw in [before, after]:
        if identity(raw, p['config']['ollama']['model'], MODEL_DIGEST, SERVER_VERSION) != p['model_identity']:
            raise ValueError('Raw identity differs from frozen model/API')
    records = [read_json(f) for f in sorted((directory / 'records').glob('*.json'))]
    if [r['call_index'] for r in records] != list(range(len(records))): raise ValueError('Duplicate/missing/reordered call indices')
    ledger = [json.loads(line) for line in (directory / 'calls.jsonl').read_text().splitlines()]
    if len(ledger) != len(records)*2: raise ValueError('Unpaired generation attempt/completed ledger')
    if len(records) > len(p['requests']): raise ValueError('Unexpected extra generation')
    for record, attempt, completed, request, case in zip(records, ledger[::2], ledger[1::2], p['requests'], p['corpus']['cases']):
        for key, expected in {'call_index': request['call_index'], 'case_id': case['id'], 'category': case['category'],
                              'text': case['text'], 'expected': case['expected'], 'payload': request['payload'],
                              'payload_sha256': request['payload_sha256'], 'model_calls': 1, 'device_execution_calls': 0}.items():
            if record[key] != expected or attempt[key] != expected: raise ValueError('Frozen case/request differs from record/ledger')
        if attempt['event'] != 'attempt' or completed != {'event':'completed','call_index':record['call_index'],'case_id':case['id']}:
            raise ValueError('Generation ledger pairing differs')
        if record['analysis'] != analyze_generation(record['generation'], case, p): raise ValueError('Saved analysis differs from raw classification')
    total = len(p['corpus']['cases']); result = aggregate(records)
    complete = status['evidence_integrity'] and len(records) == total and status['completed_calls'] == total
    gates = {'evidence_integrity': bool(complete), 'all_attempts_classified': len(records) == total,
             'all_model_responses_completed': result['model_completed_count'] == total,
             'all_wire_contracts_valid': result['wire_contract_valid_count'] == total,
             'all_guarded_plans_valid': result['guarded_plan_valid_count'] == total}
    return {'schema_version':1,'kind':'w12_current_model_evaluation','passed':all(gates.values()),
            'mandatory_gates':gates,'acceptance_policy':p['acceptance_policy'],'code_git':status['code_git'],
            'model_identity':p['model_identity'],'protocol_sha256':status['protocol_sha256'],
            'raw_index_sha256':sha256(directory/'raw-index.json'),'corpus_content_sha256':p['corpus_content_sha256'],
            **result,'by_category':{c:aggregate([r for r in records if r['category']==c]) for c in ['normal','ambiguous','illegal','deferred']},
            'model_raw_all_semantics_passed':result['model_raw_exact_matches']==total,
            'guarded_all_semantics_passed':result['guarded_exact_matches']==total,
            'offline_injected_fixture_suite':{'count':len(fixtures),'matched':sum(f['matched'] for f in fixtures),'evidence_kind':'intentional_injected_fixture_not_model_generation'},
            'generation_calls':len(records),'warmup_calls':0,'device_execution_calls':0,'error':status['error'],
            'limitations':['Single frozen29-case development suite; historical/newly authored cases, not independent held-out accuracy.',
              'Each case receives one model-only original stream=false call; no route/execution or Fast Path performance claim.',
              'Rule-only baseline, raw model and post-request-guard semantics are separate; guard repairs never improve raw score.',
              'Explicit timer unavailable policy is application behavior, not a scheduler or proof of model understanding.',
              'No TTFT Benchmark: client complete HTTP wall and server counters retained only as diagnostics.',
              'Injected malformed fixtures are offline tests, not actual model generation failures; personal understanding pending.']}


def write_reports(directory, summary):
    write_json(directory/'summary.json',summary)
    lines=['# W12 current-model household intent evaluation','',f"Mandatory structural/evidence gates passed: {summary['passed']}",
           f"Model-only calls: {summary['generation_calls']}; device execution:0; no warmup/retries.",'',
           '| group | n | wire valid | raw exact | guarded exact | rule-only exact | raw/guarded unsafe plans |',
           '|---|---:|---:|---:|---:|---:|---:|']
    for name,value in [('all',summary),*summary['by_category'].items()]:
        lines.append(f"| {name} | {value['case_count']} | {value['wire_contract_valid_count']} | {value['model_raw_exact_matches']} | {value['guarded_exact_matches']} | {value['rule_only_exact_matches']} | {value['raw_unsafe_plan_count']} / {value['guarded_unsafe_plan_count']} |")
    lines += ['', 'Primary error counts: '+json.dumps(summary['primary_error_counts'],ensure_ascii=False),
              'Raw mismatches: '+', '.join(summary['raw_semantic_mismatch_ids']),
              'Guarded mismatches: '+', '.join(summary['guarded_semantic_mismatch_ids']),'',*['- '+x for x in summary['limitations']]]
    with (directory/'report.md').open('x',encoding='utf-8') as f:f.write('\n'.join(lines)+'\n')


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);commands=parser.add_subparsers(dest='command',required=True)
    p=commands.add_parser('prepare',help='Freeze literal cases/requests/identity; metadata only,0generation')
    p.add_argument('--config-dir',type=Path,default=ROOT/'configs');p.add_argument('--corpus',type=Path,default=CORPUS);p.add_argument('--outdir',type=Path,required=True)
    p=commands.add_parser('run',help='Exactly one original model call per frozen case; no execution')
    p.add_argument('--protocol',type=Path,required=True);p.add_argument('--outdir',type=Path,required=True)
    p=commands.add_parser('report',help='Verify hashes and rebuild offline,0APIcalls')
    p.add_argument('--run-dir',type=Path,required=True);p.add_argument('--outdir',type=Path,required=True)
    args=parser.parse_args(argv);args.outdir=args.outdir.resolve()
    if args.outdir.exists():parser.error('Output directory exists; choose a fresh directory')
    args.outdir.mkdir(parents=True)
    try:
        if args.command=='prepare':result=prepare(args)
        elif args.command=='run':result=run(args)
        else:
            result=rebuild(args.run_dir.resolve());write_reports(args.outdir,result)
    except Exception as error:
        write_json(args.outdir/'error.json',{'type':type(error).__name__,'message':str(error)})
        print(f'W12 rejected: {type(error).__name__}: {error}',file=sys.stderr);return 2
    print(json.dumps({k:v for k,v in result.items() if k in ['passed','protocol','outdir','generation_calls','cases','calls','raw_exact_matches','guarded_exact_matches','total','error']},ensure_ascii=False,indent=2))
    return 0 if result['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
