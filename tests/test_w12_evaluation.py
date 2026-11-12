from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import evaluate_home_intents as evaluation
from edge_smart_home.agent.local_llm import LlmHttpError, LlmTransportError


def metadata():
    details={'format':'gguf','family':'qwen3','parameter_size':'4.0B','quantization_level':'Q4_K_M'}
    info={'general.architecture':'qwen3','qwen3.block_count':36,'qwen3.embedding_length':2560,
          'qwen3.attention.head_count':32,'qwen3.attention.head_count_kv':8,
          'qwen3.attention.key_length':128,'qwen3.attention.value_length':128,'qwen3.context_length':262144}
    return {'version':{'version':evaluation.SERVER_VERSION},'tags':{'models':[{'name':'qwen3:4b','digest':evaluation.MODEL_DIGEST,'details':details}]},
            'show':{'details':details,'model_info':info,'thinking':{'values':[True],'default':True},'template':'fixed','license':'Apache-2.0'},
            'ps':{'models':[]}}


class FakeClient:
    def __init__(self):self.value=metadata();self.calls=[]
    def get_json(self,path):self.calls.append(path);return deepcopy(self.value[path.split('/')[-1]])
    def show(self):self.calls.append('/api/show');return deepcopy(self.value['show'])


class FakeTransport:
    def __init__(self,p,overrides=None):self.p=p;self.calls=[];self.overrides=overrides or {}
    def post_json(self,endpoint,payload,**timeouts):
        index=len(self.calls);self.calls.append({'endpoint':endpoint,'payload':deepcopy(payload),'timeouts':timeouts})
        case=self.p['corpus']['cases'][index];expected=case['expected']
        answer={'kind':expected['allowed_kinds'][0],'message':'准备计划。' if expected['commands'] else '说明。','commands':deepcopy(expected['commands'])}
        result={'model':'qwen3:4b','done':True,'done_reason':'stop','response':json.dumps(answer,ensure_ascii=False),
                'eval_count':12,'eval_duration':100000000,'prompt_eval_count':3752,'prompt_eval_cached_count':3700,'prompt_eval_duration':10000000}
        override=self.overrides.get(index)
        if isinstance(override,Exception):raise override
        if isinstance(override,dict):result.update(override)
        return result


class W12EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.base=Path(self.temp.name)
        self.gitpatch=patch.object(evaluation,'git_info',return_value={'commit':'1'*40,'status':''});self.gitpatch.start();self.addCleanup(self.gitpatch.stop)
        self.sourcepatch=patch.object(evaluation,'check_sources',wraps=evaluation.check_sources)
        self.config=evaluation.ROOT/'configs'

    def prepared(self):
        directory=self.base/'prepared';directory.mkdir()
        evaluation.prepare(SimpleNamespace(config_dir=self.config,corpus=evaluation.CORPUS,outdir=directory),client=FakeClient())
        return directory/'protocol.json',evaluation.read_json(directory/'protocol.json')

    def execute(self,overrides=None):
        path,p=self.prepared();out=self.base/'run';out.mkdir();transport=FakeTransport(p,overrides)
        result=evaluation.run(SimpleNamespace(protocol=path,outdir=out),client=FakeClient(),transport=transport)
        return result,out,p,transport

    def reindex(self,out):
        index=evaluation.read_json(out/'raw-index.json')
        for name in index['sha256']:index['sha256'][name]=evaluation.sha256(out/name)
        (out/'raw-index.json').write_text(json.dumps(index,ensure_ascii=False,indent=2)+'\n')

    def test_frozen_prepare_is_metadata_only_and_exact_original_payload(self):
        path,p=self.prepared();self.assertEqual(p['generation_calls'],0);self.assertEqual(len(p['requests']),29)
        self.assertEqual(p['corpus_content_sha256'],evaluation.signature(p['corpus']))
        self.assertIn('configs/evaluation/w12-home-intents-v1.json',p['sources_sha256'])
        for request in p['requests']:
            payload=request['payload'];self.assertIs(payload['stream'],False);self.assertIs(payload['think'],False)
            self.assertEqual(payload['options'],{'temperature':0.0,'num_predict':256,'seed':42})
            self.assertNotIn('allowed_kinds',payload['prompt']);self.assertNotIn('frozen oracle',payload['prompt'])
            self.assertEqual(payload['format'],p['schema'])

    def test_full_fake29_calls_no_device_execution_and_offline_rebuild(self):
        with patch.object(evaluation.MockDeviceBackend,'execute',side_effect=AssertionError('device execution forbidden')) as execute:
            result,out,p,transport=self.execute();self.assertTrue(result['passed']);self.assertEqual(len(transport.calls),29);execute.assert_not_called()
        summary=evaluation.read_json(out/'summary.json')
        self.assertEqual(summary['model_raw_exact_matches'],29);self.assertEqual(summary['guarded_exact_matches'],29)
        self.assertEqual(summary['offline_injected_fixture_suite']['count'],26)
        self.assertEqual(summary['offline_injected_fixture_suite']['matched'],26)
        self.assertEqual(summary['generation_calls'],29);self.assertEqual(summary['device_execution_calls'],0)
        self.assertEqual(len((out/'calls.jsonl').read_text().splitlines()),58)
        with patch.object(evaluation,'MetadataClient',side_effect=AssertionError('report must be offline')):
            self.assertEqual(evaluation.rebuild(out),summary)
        for call in transport.calls:self.assertEqual(call['timeouts'],{'connect_timeout_seconds':2.0,'total_timeout_seconds':30.0})

    def test_timer_guard_improvement_does_not_improve_raw_semantics(self):
        _,p=self.prepared();timer=next((i,c) for i,c in enumerate(p['corpus']['cases']) if c['category']=='deferred')
        i,case=timer
        raw={'kind':'plan','message':'准备关灯。','commands':[{'device_id':'living_room_light','action':'turn_off','arguments':{}}]}
        envelope={'response':{'model':'qwen3:4b','done':True,'done_reason':'stop','response':json.dumps(raw,ensure_ascii=False)},'error':None}
        actual=evaluation.analyze_generation(envelope,case,p)
        self.assertTrue(actual['wire_contract_valid']);self.assertTrue(actual['raw_unsafe_plan'])
        self.assertFalse(actual['raw_semantic_match']['passed']);self.assertTrue(actual['guarded_semantic_match']['passed'])
        self.assertEqual(actual['diagnosis']['guard_result']['kind'],'unavailable')
        self.assertFalse(actual['guarded_unsafe_plan'])

    def test_invalid_json_and_transport_failure_keep_full_denominator_and_continue(self):
        result,out,p,transport=self.execute({0:{'response':'{"kind":'},1:LlmHttpError(503)})
        self.assertFalse(result['passed']);self.assertEqual(len(transport.calls),29)
        summary=evaluation.read_json(out/'summary.json');self.assertEqual(summary['case_count'],29)
        self.assertEqual(summary['wire_contract_valid_count'],27);self.assertEqual(summary['model_completed_count'],28)
        self.assertEqual(summary['primary_error_counts']['model_json_syntax'],1)
        self.assertEqual(summary['primary_error_counts']['transport_http_error'],1)
        self.assertEqual(summary['raw_kind_confusion'][0]['expected_kind'],'clarification')

    def test_truncation_is_not_accepted_even_if_partial_json_is_complete(self):
        result,out,_,transport=self.execute({0:{'done_reason':'length'}})
        self.assertFalse(result['passed']);self.assertEqual(len(transport.calls),29)
        record=evaluation.read_json(out/'records/00000.json');self.assertFalse(record['analysis']['wire_contract_valid'])
        self.assertEqual(record['analysis']['primary_error']['category'],'generation_truncated')
        self.assertTrue(record['analysis']['diagnosis']['product_parse_accepted'])

    def test_semantic_mismatch_is_quality_result_not_relabelled_structural_failure(self):
        result,out,_,_=self.execute({0:{'response':json.dumps({'kind':'rejected','message':'不支持。','commands':[]},ensure_ascii=False)}})
        self.assertTrue(result['passed']);summary=evaluation.read_json(out/'summary.json')
        self.assertEqual(summary['model_raw_exact_matches'],28);self.assertFalse(summary['model_raw_all_semantics_passed'])
        self.assertTrue(summary['mandatory_gates']['all_wire_contracts_valid'])
        self.assertEqual(summary['primary_error_counts']['semantic_mismatch'],1)

    def test_raw_plan_validator_error_not_hidden_by_semantics(self):
        path,p=self.prepared();case=p['corpus']['cases'][0]
        raw={'kind':'plan','message':'计划。','commands':[{'device_id':'not_a_device','action':'turn_on','arguments':{}}]}
        actual=evaluation.analyze_generation({'response':{'model':'qwen3:4b','done':True,'done_reason':'stop','response':json.dumps(raw)},'error':None},case,p)
        self.assertTrue(actual['wire_contract_valid']);self.assertFalse(actual['guarded_plan_valid'])
        self.assertTrue(actual['raw_unsafe_plan']);self.assertEqual(actual['primary_error']['category'],'plan_validation')

    def test_rule_internal_unavailable_not_counted_as_wire_model_json(self):
        _,p=self.prepared();case=next(c for c in p['corpus']['cases'] if c['category']=='deferred')
        wire={'model':'qwen3:4b','done':True,'done_reason':'stop','response':json.dumps({'kind':'unavailable','message':'定时未实现。','commands':[]})}
        actual=evaluation.analyze_generation({'response':wire,'error':None},case,p)
        self.assertTrue(actual['wire_contract_valid']);self.assertIsNone(actual['rule_only_baseline']['result']['message'])
        self.assertEqual(actual['rule_only_baseline']['llm_calls'],0)
        self.assertIn('no JSON-wire validity claim',actual['rule_only_baseline']['scope'])

    def test_offline_rebuild_rejects_raw_hash_mutation_and_duplicate_index(self):
        _,out,_,_=self.execute();target=out/'records/00001.json';raw=evaluation.read_json(target);raw['call_index']=0
        target.write_text(json.dumps(raw,ensure_ascii=False,indent=2)+'\n')
        with self.assertRaisesRegex(ValueError,'Raw artifact'):evaluation.rebuild(out)
        self.reindex(out)
        with self.assertRaisesRegex(ValueError,'call indices'):evaluation.rebuild(out)

    def test_offline_rebuild_rejects_saved_score_tampering_even_reindexed(self):
        _,out,_,_=self.execute();target=out/'records/00000.json';raw=evaluation.read_json(target)
        raw['analysis']['raw_semantic_match']['passed']=False;target.write_text(json.dumps(raw,ensure_ascii=False,indent=2)+'\n');self.reindex(out)
        with self.assertRaisesRegex(ValueError,'Saved analysis'):evaluation.rebuild(out)

    def test_inline_oracle_tampering_rejected_against_literal_source(self):
        path,p=self.prepared();p['corpus']['cases'][0]['expected']['allowed_kinds']=['rejected']
        p['corpus_content_sha256']=evaluation.signature(p['corpus'])
        with self.assertRaisesRegex(ValueError,'Frozen inline input'):evaluation.check_sources(p)

    def test_existing_output_rejected_before_api_and_unchanged(self):
        out=self.base/'existing';out.mkdir();sentinel=out/'keep';sentinel.write_text('unchanged')
        with patch.object(evaluation,'MetadataClient',side_effect=AssertionError('must reject before API')) as client:
            with self.assertRaises(SystemExit) as error:evaluation.main(['run','--protocol','missing.json','--outdir',str(out)])
        self.assertEqual(error.exception.code,2);self.assertEqual(sentinel.read_text(),'unchanged');client.assert_not_called()

    def test_dirty_prepare_rejected_before_metadata(self):
        out=self.base/'dirty';out.mkdir()
        with patch.object(evaluation,'git_info',return_value={'commit':'1'*40,'status':' M product.py'}):
            with patch.object(evaluation,'MetadataClient') as client:
                with self.assertRaisesRegex(ValueError,'clean'):evaluation.prepare(SimpleNamespace(config_dir=self.config,corpus=evaluation.CORPUS,outdir=out))
                client.assert_not_called()

    def test_transport_outer_json_category_uses_exception_cause_not_message_guess(self):
        error=LlmTransportError('opaque transport message')
        try:json.loads('{')
        except json.JSONDecodeError as cause:error.__cause__=cause
        self.assertEqual(evaluation.transport_error(error)['category'],'outer_json_decode')
        generic=LlmTransportError('HTTP response invalid JSON maybe')
        self.assertEqual(evaluation.transport_error(generic)['category'],'transport_error')
        self.assertIsNone(evaluation.transport_error(generic)['http_status'])


if __name__=='__main__':unittest.main()
