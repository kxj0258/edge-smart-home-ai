"""Frozen pronunciation material contract and reference-only Mock behavior.

These tests never synthesize, recognize or play speech. Dictionary facts are
predeclared lexical targets, not a claim to have heard the generated audio.
"""
from __future__ import annotations

import json
from pathlib import Path
import unittest
from urllib.parse import urlparse
from unittest.mock import patch

from scripts import evaluate_w16_voice_home as HOME

ROOT = Path(__file__).resolve().parents[1]
DATA = json.loads((ROOT / 'configs/w16-pronunciation-cases-v1.json').read_text())
CONFIG = {name: HOME.read(ROOT / 'configs' / (name + '.json')) for name in HOME.CONFIG_NAMES}


class PronunciationMaterialTests(unittest.TestCase):
    def test_five_normal_reference_commands_match_fixed_state_oracles(self):
        cases = [c for c in DATA['home_cases'] if c['category'] == 'normal']
        self.assertEqual(len(cases), 5)
        with patch.object(HOME.ASR, 'native_asr', side_effect=AssertionError('No ASR in reference preflight')):
            for case in cases:
                with self.subTest(case=case['id']):
                    result = HOME.reference_probe(case, CONFIG)
                    self.assertTrue(all(result['checks'].values()), result)
                    self.assertEqual((result['status'], result['error_code']), ('executed', None))
                    self.assertEqual(result['llm_calls'], 0)
                    self.assertEqual(len(result['execution_records']), 1)
                    self.assertEqual(result['backend_kind'], 'mock')

    def test_five_abnormal_reference_commands_reject_or_clarify_and_preserve_state(self):
        cases = [c for c in DATA['home_cases'] if c['category'] == 'abnormal']
        self.assertEqual(len(cases), 5)
        with patch.object(HOME.ASR, 'native_asr', side_effect=AssertionError('No ASR in reference preflight')):
            for case in cases:
                with self.subTest(case=case['id']):
                    result = HOME.reference_probe(case, CONFIG)
                    self.assertTrue(all(result['checks'].values()), result)
                    self.assertEqual(result['status'], case['expect']['status'])
                    self.assertEqual(result['error_code'], case['expect']['error_code'])
                    self.assertEqual(result['before'], result['after'])
                    self.assertEqual(result['llm_calls'], 0)
                    self.assertEqual(len(result['execution_records']), int(result['error_code'] == 'DEVICE_OFFLINE'))
                    self.assertIsNone(result['outcome'])

    def test_new_material_does_not_reuse_or_relabel_original_failed_home_inputs(self):
        old = HOME.read(ROOT / 'configs/evaluation/w16-voice-home-v1.json')
        old_text = {c['reference_text'] for c in old['cases']}
        values = DATA['home_cases'] + DATA['pronunciation_probes']
        self.assertEqual(len(values), 18)
        self.assertEqual(len({c['id'] for c in values}), 18)
        self.assertEqual(len({c['reference_text'] for c in values}), 18)
        self.assertFalse(old_text & {c['reference_text'] for c in values})
        self.assertTrue(all(c['reference_text'] for c in values))
        self.assertTrue(DATA['selection']['authored_before_native_output'])
        self.assertFalse(DATA['selection']['asr_driven_selection'])
        self.assertFalse(DATA['selection']['reference_texts_may_change_after_output'])

    def test_common_tts_parameters_define_new_controlled_domain_and_one_policy_factor(self):
        self.assertEqual(DATA['common_tts'], {
            'speaker_id': 13, 'speed': 1.0, 'num_threads': 2, 'provider': 'cpu',
            'sample_rate': 8000, 'num_speakers': 174, 'noise_scale': 0.0,
            'noise_scale_w': 0.0, 'debug': True, 'silence_scale': 0.2})
        policy = DATA['single_factor_ab']
        self.assertTrue(policy['unchanged_model_weights_voice_rate_noise_generation'])
        self.assertFalse(policy['historical_override_lexicon_used'])
        self.assertEqual(policy['per_arm_per_text_synthesis_attempts'], 1)
        self.assertTrue(policy['new_controlled_zero_noise_domain'])
        self.assertTrue(policy['not_a_reproduction_of_old_default_noise_wavs'])
        self.assertFalse(policy['selection_or_regeneration_from_asr_results'])

    def test_focal_spans_use_raw_character_offsets_and_cover_both_diao_occurrences(self):
        for case in DATA['home_cases'] + DATA['pronunciation_probes']:
            for focal in case['focal_pronunciation']:
                with self.subTest(case=case['id'], focal=focal['text']):
                    self.assertEqual(case['reference_text'][focal['char_start']:focal['char_end']], focal['text'])
                    self.assertEqual(len(focal['base_pinyin']), len(focal['text']))
                    self.assertEqual(focal['expected_phones'].count('#0'), len(focal['text']))
        for name in ('W16-TN02', 'W16-TA02'):
            case = next(c for c in DATA['home_cases'] if c['id'] == name)
            targets = case['focal_pronunciation']
            self.assertEqual([(p['text'], p['char_start'], p['char_end']) for p in targets],
                             [('空调', 2, 4), ('调到', 6, 8)])
            self.assertEqual(targets[0]['expected_phones'], ['k', 'ong1', '#0', 't', 'iao2', '#0'])
            self.assertEqual(targets[1]['expected_phones'], ['t', 'iao2', '#0', 'd', 'ao4', '#0'])

    def test_polyphone_oppositions_are_lexical_targets_and_probes_never_execute_devices(self):
        probes = DATA['pronunciation_probes']
        self.assertEqual([p['reference_text'] for p in probes],
                         ['空调', '调查', '重复', '重量', '音乐', '快乐', '客厅', '窗帘'])
        expected = {'空调': ['kong1', 'tiao2'], '调查': ['diao4', 'cha2'],
                    '重复': ['chong2', 'fu4'], '重量': ['zhong4', 'liang4'],
                    '音乐': ['yin1', 'yue4'], '快乐': ['kuai4', 'le4']}
        for p in probes:
            self.assertFalse(p['state_or_semantics_evaluated'])
            self.assertNotIn('expect', p)
            if p['category'] == 'polyphone_probe':
                self.assertEqual(p['focal_pronunciation'][0]['base_pinyin'], expected[p['reference_text']])
                self.assertIn(p['same_character'], p['reference_text'])
            else:
                self.assertEqual(p['category'], 'neutral_control')
                self.assertIn(p['reference_text'], ('客厅', '窗帘'))
        # The yue4 model alphabet is ^ ve4, not an invented "yue4" phone token.
        music = next(p for p in probes if p['reference_text'] == '音乐')
        self.assertEqual(music['focal_pronunciation'][0]['expected_phones'], ['^', 'in1', '#0', '^', 've4', '#0'])

    def test_rights_and_acceptance_layers_do_not_turn_generated_speech_into_human_evidence(self):
        sources = DATA['sources']
        self.assertEqual(sources['text']['source_kind'], 'project_authored_text')
        self.assertFalse(sources['text']['upstream_sentences_or_audio_copied'])
        self.assertEqual(sources['human_household_recordings'], 0)
        self.assertIn('separate', sources['generated_audio_rights'])
        self.assertIn('CC-BY-ND-3.0-TW', sources['dictionary_license'])
        for fact in DATA['phonetic_references']:
            self.assertEqual(urlparse(fact['dictionary_url']).hostname, 'dict.revised.moe.edu.tw')
            self.assertIn('not a full-sentence acoustic/sandhi oracle', fact['pronunciation_reference_kind'])
        acceptance = DATA['acceptance']
        for field in ('human_household_accepted', 'personal_understanding_accepted', 'human_listening_accepted',
                      'native_phone_log_is_acoustic_proof', 'asr_is_a_phonetic_judge', 'automatic_product_promotion'):
            self.assertFalse(acceptance[field])


if __name__ == '__main__':
    unittest.main()
