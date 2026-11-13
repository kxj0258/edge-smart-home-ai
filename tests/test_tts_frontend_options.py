from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import Mock, patch

import numpy as np
from edge_smart_home.audio.tts import SherpaOnnxVitsTts


class TtsFrontendOptionsTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ('model.onnx', 'tokens.txt', 'lexicon.txt', 'phone.fst', 'number.fst', 'date.fst', 'rule.far'):
            (self.root / name).write_bytes(b'Fake file identity fixture, never loaded')
        self.module = SimpleNamespace(
            OfflineTtsVitsModelConfig=Mock(side_effect=lambda **kw: SimpleNamespace(**kw)),
            OfflineTtsModelConfig=Mock(side_effect=lambda **kw: SimpleNamespace(**kw)),
            OfflineTtsConfig=Mock(side_effect=lambda **kw: SimpleNamespace(**kw, validate=lambda: True)),
            OfflineTts=Mock(return_value=SimpleNamespace(sample_rate=8000, num_speakers=174,
                           generate=Mock(return_value=SimpleNamespace(samples=[.1, -.1], sample_rate=8000)))))
        self.patch = patch.dict(sys.modules, {'sherpa_onnx': self.module}); self.patch.start(); self.addCleanup(self.patch.stop)

    def test_defaults_keep_legacy_native_kwargs_and_fst_order(self):
        engine = SherpaOnnxVitsTts(self.root)
        self.assertEqual(self.module.OfflineTtsVitsModelConfig.call_args.kwargs,
                         {'model':str(self.root/'model.onnx'),'tokens':str(self.root/'tokens.txt'),'lexicon':str(self.root/'lexicon.txt')})
        self.assertEqual(set(self.module.OfflineTtsModelConfig.call_args.kwargs), {'vits','num_threads','provider'})
        config = self.module.OfflineTtsConfig.call_args.kwargs
        self.assertEqual(set(config), {'model','rule_fsts'})
        self.assertEqual(config['rule_fsts'], str(self.root/'phone.fst')+','+str(self.root/'number.fst'))
        self.assertEqual(engine.sample_rate, 8000); self.assertEqual(engine.num_speakers, 174)

    def test_zero_noise_debug_and_explicit_far_are_forwarded(self):
        SherpaOnnxVitsTts(self.root, rule_far_paths=(self.root/'rule.far',), noise_scale=0.0, noise_scale_w=0.0, debug=True)
        self.assertEqual(self.module.OfflineTtsVitsModelConfig.call_args.kwargs['noise_scale'], 0.0)
        self.assertEqual(self.module.OfflineTtsVitsModelConfig.call_args.kwargs['noise_scale_w'], 0.0)
        self.assertIs(self.module.OfflineTtsModelConfig.call_args.kwargs['debug'], True)
        self.assertEqual(self.module.OfflineTtsConfig.call_args.kwargs['rule_fars'],str(self.root/'rule.far'))

    def test_single_factor_far_does_not_change_other_native_arguments(self):
        common = {'noise_scale':0.0,'noise_scale_w':0.0,'debug':True}
        SherpaOnnxVitsTts(self.root, **common)
        vits_a = self.module.OfflineTtsVitsModelConfig.call_args.kwargs
        model_a = {k:v for k,v in self.module.OfflineTtsModelConfig.call_args.kwargs.items() if k!='vits'}
        config_a = self.module.OfflineTtsConfig.call_args.kwargs
        SherpaOnnxVitsTts(self.root,rule_far_paths=(self.root/'rule.far',),**common)
        self.assertEqual(vits_a,self.module.OfflineTtsVitsModelConfig.call_args.kwargs)
        self.assertEqual(model_a,{k:v for k,v in self.module.OfflineTtsModelConfig.call_args.kwargs.items() if k!='vits'})
        config_b=self.module.OfflineTtsConfig.call_args.kwargs
        self.assertEqual(config_a['rule_fsts'],config_b['rule_fsts']);self.assertNotIn('rule_fars',config_a)

    def test_noise_rejects_bool_nonfinite_negative_or_nonnumeric_before_native(self):
        for key in ('noise_scale','noise_scale_w'):
            for value in (True,False,-.1,float('nan'),float('inf'),'0',[] ):
                with self.subTest(key=key,value=value), self.assertRaises(ValueError):
                    SherpaOnnxVitsTts(self.root,**{key:value})
        self.module.OfflineTts.assert_not_called()
        self.module.OfflineTtsVitsModelConfig.assert_not_called()

    def test_missing_far_or_lexicon_is_rejected_before_native(self):
        for kwargs in ({'rule_far_paths':(self.root/'missing.far',)}, {'lexicon_path':self.root/'missing.txt'}):
            with self.subTest(kwargs=kwargs),self.assertRaises(FileNotFoundError):SherpaOnnxVitsTts(self.root,**kwargs)
        self.module.OfflineTts.assert_not_called()

    def test_duplicate_far_or_wrong_debug_type_is_rejected(self):
        for kwargs in ({'rule_far_paths':(self.root/'rule.far',self.root/'rule.far')}, {'rule_far_paths':'rule.far'}, {'debug':1}):
            with self.subTest(kwargs=kwargs),self.assertRaises(ValueError):SherpaOnnxVitsTts(self.root,**kwargs)
        self.module.OfflineTts.assert_not_called()

    def test_legacy_synthesis_api_and_float32_output_are_preserved(self):
        engine=SherpaOnnxVitsTts(self.root);audio=engine.synthesize('新的家庭口令',speaker_id=13,speed=1.0)
        self.module.OfflineTts.return_value.generate.assert_called_once_with(text='新的家庭口令',sid=13,speed=1.0)
        self.assertEqual(audio.samples.dtype,np.float32);self.assertEqual(audio.sample_rate,8000)

    def test_invalid_native_config_is_not_instantiated(self):
        self.module.OfflineTtsConfig.side_effect=lambda **kw:SimpleNamespace(**kw,validate=lambda:False)
        with self.assertRaises(ValueError):SherpaOnnxVitsTts(self.root)
        self.module.OfflineTts.assert_not_called()


if __name__=='__main__':unittest.main()
