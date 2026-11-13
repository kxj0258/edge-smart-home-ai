from argparse import Namespace
from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

import numpy as np

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location('w16_tts_worker',ROOT/'scripts/w16_tts_pronunciation_worker.py')
W=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(W)


class FakeTts:
    sample_rate=8000
    num_speakers=174
    def __init__(self,arm,*,fail_at=None,incomplete=False,hook=None):
        self.arm=arm;self.fail_at=fail_at;self.incomplete=incomplete;self.hook=hook;self.calls=[]
    def synthesize(self,text,*,speaker_id,speed):
        index=len(self.calls);self.calls.append((text,speaker_id,speed))
        os.write(2,('[fake native] Raw text: '+text+'\n').encode())
        if self.hook:self.hook(index)
        if index==self.fail_at:raise RuntimeError('Fake generation failure, not native')
        count=2 if self.arm=='A' else 48
        if self.incomplete:count-=1
        for _ in range(count):os.write(2,('[fake native] After normalizing: '+text+'\n').encode())
        os.write(2,('[fake native] Input text in string: '+text+'\nInput text in bytes: fake\nAfter splitting to words: '+' '.join(text)+'\n').encode())
        return SimpleNamespace(samples=np.array([.1,-.1,0,.1],np.float32),sample_rate=8000)


class W16TtsWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name);model=self.root/'model';model.mkdir()
        files=[]
        for name in sorted(W.MODEL_FILES):
            p=model/name;p.write_bytes(b'Fake source file, not native material');files.append(W.identity(p))
        sentinel=self.root/'frozen-source.json';sentinel.write_text('{}\n');self.sentinel=sentinel
        self.p={'schema_version':1,'kind':'w16_frozen_pronunciation_protocol','state':'frozen',
                'source_git':{'commit':'f'*40,'status':''},'sources_sha256':{'unit-only':'f'*64},'dependencies':{},
                'model_dir':str(model),'model_files':files,'frozen_inputs':[W.identity(sentinel)],
                'settings':deepcopy(W.EXPECTED),'cases':[{'id':f'CASE-{i:02d}','reference_text':'独立家庭口令'+str(i),'source_kind':'unit_fixture'} for i in range(18)]}
        self.protocol=self.root/'protocol.json';self.protocol.write_text(json.dumps(self.p,ensure_ascii=False))

    def args(self,arm='A',out='run'):
        return Namespace(protocol=self.protocol,arm=arm,outdir=self.root/out)

    def run_fake(self,arm='A',**options):
        engine=FakeTts(arm,**options);factory=Mock(return_value=engine)
        result=W.run(self.args(arm),tts_factory=factory)
        return result,engine,factory

    def test_protocol_has_fixed_single_far_factor_and_complete_eighteen_sources(self):
        W.validate_protocol(self.p)
        for change in ('noise','fst','far','count','id','newline','model'):
            p=deepcopy(self.p)
            if change=='noise':p['settings']['noise_scale']=.667
            if change=='fst':p['settings']['rule_fst_names'].append('date.fst')
            if change=='far':p['settings']['arm_rule_far_names']['A']=['rule.far']
            if change=='count':p['cases'].pop()
            if change=='id':p['cases'][1]['id']=p['cases'][0]['id']
            if change=='newline':p['cases'][0]['reference_text']='line\nbreak'
            if change=='model':p['model_files'].pop()
            with self.subTest(change=change),self.assertRaises(ValueError):W.validate_protocol(p)

    def test_numeric_boolean_is_not_silently_accepted(self):
        for key in ['speaker_id','num_threads','noise_scale','noise_scale_w','speed','silence_scale']:
            p=deepcopy(self.p);p['settings'][key]=False if key.startswith('noise') else True
            with self.subTest(key=key),self.assertRaises(ValueError):W.validate_protocol(p)

    def test_fake_A_generates_each_text_once_and_retains_native_trace_and_audio(self):
        result,engine,factory=self.run_fake()
        self.assertTrue(result['passed'],result);self.assertEqual(result['execution_kind'],'fake_tts_unit_fixture')
        self.assertEqual(len(engine.calls),18);self.assertEqual(result['generation_attempts'],18);self.assertEqual(result['completed'],18)
        self.assertEqual(factory.call_args.kwargs['rule_far_paths'],())
        self.assertEqual(factory.call_args.kwargs['noise_scale'],0)
        self.assertEqual(len((self.root/'run/ledger.jsonl').read_text().splitlines()),36)
        for i,case in enumerate(self.p['cases']):
            row=W.read(self.root/f'run/records/{i:04d}.json');self.assertTrue(row['completed']);self.assertEqual(row['case'],case)
            self.assertEqual(row['actual_native_raw_text'],case['reference_text']);self.assertEqual(len(row['actual_native_normalization_steps']),2)
            self.assertEqual(row['actual_native_words'],list(case['reference_text']))
            self.assertIsNotNone(row['source_float']);self.assertIsNotNone(row['pcm16_wav']);self.assertEqual(row['conversion']['clipped_sample_count'],0)
        self.assertFalse(result['acoustic_pronunciation_accepted']);self.assertFalse(result['human_listening_accepted']);self.assertEqual(result['asr_calls'],0)

    def test_fake_B_uses_only_far_and_requires_all_fortyeight_native_steps(self):
        result,engine,factory=self.run_fake('B');self.assertTrue(result['passed'])
        self.assertEqual(factory.call_args.kwargs['rule_far_paths'],(Path(self.p['model_dir'])/'rule.far',))
        row=W.read(self.root/'run/records/0000.json');self.assertEqual(len(row['actual_native_normalization_steps']),48)
        self.assertTrue(row['native_trace']['consistent']);self.assertEqual(result['generation_attempts'],18)

    def test_incomplete_native_trace_does_not_promote_saved_audio(self):
        result,engine,_=self.run_fake(incomplete=True)
        self.assertFalse(result['passed']);self.assertEqual(len(engine.calls),18)
        row=W.read(self.root/'run/records/0000.json');self.assertTrue(row['generated_audio_saved']);self.assertFalse(row['completed'])
        self.assertIn('trace is incomplete',row['error']['detail'])

    def test_generation_failure_retained_without_retry_and_remaining_cases_continue(self):
        result,engine,_=self.run_fake(fail_at=3)
        self.assertFalse(result['passed']);self.assertEqual(len(engine.calls),18);self.assertEqual(result['completed'],17)
        row=W.read(self.root/'run/records/0003.json');self.assertTrue(row['generation_attempted']);self.assertIsNone(row['pcm16_wav']);self.assertIn('Fake generation failure',row['error']['detail'])
        self.assertTrue(W.read(self.root/'run/records/0017.json')['completed'])

    def test_model_init_failure_retains_eighteen_non_generated_records(self):
        factory=Mock(side_effect=RuntimeError('Fake initialization error'))
        result=W.run(self.args(),tts_factory=factory)
        self.assertFalse(result['passed']);self.assertEqual(factory.call_count,1);self.assertEqual(result['generation_attempts'],0)
        self.assertEqual(len(list((self.root/'run/records').glob('*.json'))),18)
        row=W.read(self.root/'run/records/0000.json');self.assertFalse(row['generation_attempted']);self.assertIsNotNone(row['error'])

    def test_before_after_mutation_fails_integrity_without_changing_expected_identity(self):
        result,engine,_=self.run_fake(hook=lambda i:self.sentinel.write_text('mutated') if i==0 else None)
        self.assertFalse(result['passed']);self.assertFalse(result['frozen_inputs_and_runtime_preserved']);self.assertEqual(len(engine.calls),18)
        self.assertEqual(result['input_identities_before'][9],self.p['frozen_inputs'][0])

    def test_file_descriptor_capture_restores_stderr_even_on_exception(self):
        original=os.fstat(2);target=self.root/'capture.stderr'
        with self.assertRaises(RuntimeError):
            with W.captured_native_stderr(target):
                os.write(2,b'actual descriptor fixture\n');raise RuntimeError('expected')
        after=os.fstat(2);self.assertEqual((original.st_dev,original.st_ino),(after.st_dev,after.st_ino))
        self.assertEqual(target.read_text(),'actual descriptor fixture\n')

    def test_misbound_or_duplicate_native_case_markers_are_rejected(self):
        target=self.root/'CASE-00.stderr';text='独立家庭口令0'
        base='W16_CASE_START A CASE-00\nRaw text: '+text+'\nAfter normalizing: '+text+'\nAfter normalizing: '+text+'\nInput text in string: '+text+'\nAfter splitting to words: 独 立 家 庭\nW16_CASE_END A CASE-00\n'
        target.write_text(base);self.assertTrue(W.parse_native_trace(target,text,'A')['consistent'])
        for changed in [base.replace('CASE-00','OTHER',1),base+'W16_CASE_END A CASE-00\n',base.replace('Raw text: '+text,'Raw text: 改标签')]:
            target.write_text(changed);self.assertFalse(W.parse_native_trace(target,text,'A')['consistent'])

    def test_existing_output_rejected_before_protocol_read_or_factory(self):
        with patch.object(W,'read',side_effect=AssertionError('must not read')),patch.object(W,'native_factory',side_effect=AssertionError('must not construct')):
            with self.assertRaises(FileExistsError):W.run(Namespace(protocol=self.root/'missing',arm='A',outdir=self.root))

    def test_native_preflight_rejects_dirty_git_before_model_factory(self):
        with patch.object(W.DEV,'git',return_value={'commit':'f'*40,'status':' M changed'}),patch.object(W,'native_factory',side_effect=AssertionError('must not construct')):
            with self.assertRaises(ValueError):W.run(self.args())


if __name__=='__main__':unittest.main()
