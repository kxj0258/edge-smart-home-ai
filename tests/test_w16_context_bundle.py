"""Stdlib tests; actual isolated compiler checks are recorded separately."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import build_w16_context_bundle as B


class ContextBundleTests(unittest.TestCase):
    def tokens(self): return {'t': 25, 'iao2': 100, '#0': 3, 'd': 11, 'ao4': 52, 'iao4': 102}

    def test_sidecar_preserves_bytes_and_adds_only_one_valid_entry(self):
        original = '调 d iao4 #0\n到 d ao4 #0\n'.encode()
        data, proof = B.sidecar_bytes(original, self.tokens())
        self.assertEqual(data, original + B.ADDED_LINE.encode())
        self.assertEqual(proof['added_token_ids'], [25, 100, 3, 11, 52, 3])
        self.assertTrue(proof['all_original_mappings_unchanged'])

    def test_no_newline_original_is_an_unchanged_prefix(self):
        original = '调 d iao4 #0'.encode()
        data, _ = B.sidecar_bytes(original, self.tokens())
        self.assertEqual(data, original + b'\n' + B.ADDED_LINE.encode())

    def test_existing_word_cannot_be_overridden(self):
        with self.assertRaisesRegex(ValueError, 'now exists'):
            B.sidecar_bytes(B.ADDED_LINE.encode(), self.tokens())

    def test_unknown_phone_rejected_before_append(self):
        tokens = self.tokens(); del tokens['iao2']
        with self.assertRaisesRegex(ValueError, 'alphabet'):
            B.sidecar_bytes(b'', tokens)

    def test_first_valid_mapping_wins_and_invalid_entry_does_not_shadow(self):
        data = '调 absent\n调 d iao4 #0\n调 t iao2 #0\n'.encode()
        self.assertEqual(B.parse_lexicon(data, self.tokens()), {'调': ['d', 'iao4', '#0']})

    def test_context_does_not_filter_legal_or_illegal_temperature(self):
        for number in ['二十四', '三十二', '一', '九十九', '零', '两百']:
            text = B.LEFT + '调到' + number + '度'
            self.assertEqual(B.expected_rewrite(text), B.LEFT + B.REPLACEMENT + number + '度')

    def test_context_does_not_globally_replace_diao_or_raw_temperature(self):
        for text in ['调查', '调到二十四度', '温度调到二十四度', '#$|亮度|$#调到二十四度',
                     B.LEFT + '調到二十四度', B.LEFT + '调到上海', B.LEFT + '调到度', B.LEFT + '调到24度']:
            self.assertEqual(B.expected_rewrite(text), text)

    def test_marker_change_preserves_original_characters(self):
        text = '卧室#$|空调|$#' + B.LEFT + '调到二十四度'
        self.assertEqual(B.surface(B.expected_rewrite(text)), '卧室空调温度调到二十四度')

    def records(self):
        records = []
        for i in range(18):
            name = B.TARGET_IDS[i] if i < 2 else 'fixed-' + str(i)
            text = B.LEFT + ('调到二十四度' if i == 0 else '调到三十二度') if i < 2 else '调查'
            records.append({'id': name, 'native_trace': {'normalization_steps': [text]},
                            'case': {'reference_text': B.surface(text)}})
        return records

    def test_static_validator_requires_both_targets_and_all16_other_cases(self):
        with patch.object(B, 'compiled_rewrite', side_effect=lambda rule, text: B.expected_rewrite(text)):
            v = B.static_validation(object(), self.records())
        self.assertTrue(v['passed'])
        self.assertEqual(sum(c['changed'] for c in v['cases']), 2)
        self.assertEqual(sum(not c['changed'] for c in v['cases']), 16)
        self.assertTrue(all(c['surface_preserved'] for c in v['cases']))

    def test_static_validator_keeps_target_missing_failure(self):
        with patch.object(B, 'compiled_rewrite', side_effect=lambda rule, text: text):
            v = B.static_validation(object(), self.records())
        self.assertFalse(v['passed'])
        self.assertFalse(v['cases'][0]['passed'])

    def test_static_validator_rejects_non_target_regression(self):
        def wrong(rule, text): return '调杳' if text == '调查' else B.expected_rewrite(text)
        with patch.object(B, 'compiled_rewrite', side_effect=wrong):
            v = B.static_validation(object(), self.records())
        self.assertFalse(v['passed'])
        self.assertFalse(v['cases'][2]['passed'])

    def test_static_validator_rejects_selected_subset(self):
        with patch.object(B, 'compiled_rewrite', side_effect=lambda rule, text: B.expected_rewrite(text)):
            v = B.static_validation(object(), self.records()[:2])
        self.assertFalse(v['passed'])

    def test_far_metadata_rejects_log_codepoint_extra_entries_and_dirty_shape(self):
        good = {'entries': [B.ENTRY_KEY], 'arc_type': 'standard', 'weight_type': 'tropical',
                'fst_type': 'vector', 'states': 30, 'token_type': 'byte', 'min_label': 0, 'max_label': 255}
        B.validate_far_structure(good)
        for key, value in [('entries', ['extra', B.ENTRY_KEY]), ('arc_type', 'log'), ('weight_type', 'log'),
                           ('token_type', 'utf8'), ('max_label', 35843), ('states', True)]:
            bad = deepcopy(good); bad[key] = value
            with self.assertRaises(ValueError): B.validate_far_structure(bad)

    def test_output_reuse_overlap_and_symlinks_refused(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); source = root / 'source'; source.mkdir()
            with self.assertRaises(FileExistsError): B._fresh(source, [])
            with self.assertRaises(ValueError): B._fresh(source / 'new', [source])
            link = root / 'link'; link.symlink_to(source, target_is_directory=True)
            with self.assertRaises(ValueError): B._fresh(link / 'new', [])

    def test_relative_artifact_cannot_escape_bundle(self):
        with tempfile.TemporaryDirectory() as d:
            for name in ['../outside', '/tmp/outside']:
                with self.assertRaises(ValueError): B._relative_file(Path(d), name)

    def test_identity_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'file'; p.write_bytes(b'original')
            frozen = B.identity(p); p.write_bytes(b'changed')
            with self.assertRaises(ValueError): B.check_identity(frozen)


if __name__ == '__main__': unittest.main()
