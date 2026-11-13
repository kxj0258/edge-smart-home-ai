from pathlib import Path
from tempfile import TemporaryDirectory
import csv
import json
import unittest

from scripts import w16_pronunciation_analysis as P


class PronunciationAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'tokens.txt').write_text('sil 0\neos 1\n#0 2\nk 3\nong1 4\nt 5\niao2 6\nd 7\niao4 8\n')
        (self.root / 'lexicon.txt').write_text('空 k ong1 #0\n调 d iao4 #0\n空调 k ong1 #0 t iao2 #0\n空调 d iao4 #0\n坏 UNKNOWN\n')
        self.resources = P.load_resources(self.root)
        self.case = {'id': 'test', 'reference_text': '空调', 'focal_pronunciation': [
            {'text': '空调', 'char_start': 0, 'char_end': 2, 'base_pinyin': ['kong1', 'tiao2'],
             'expected_phones': ['k', 'ong1', '#0', 't', 'iao2', '#0']}]}

    def test_first_valid_entry_and_token_alphabet_follow_official_lexicon(self):
        self.assertEqual(self.resources['lexicon']['空调'], self.case['focal_pronunciation'][0]['expected_phones'])
        self.assertNotIn('坏', self.resources['lexicon'])
        self.assertEqual(self.resources['duplicate_entries'], ['空调'])

    def test_observed_native_word_group_changes_derived_reading_without_changing_label(self):
        plain = P.derive_frontend(self.case, ['空', '调'], self.resources)
        grouped = P.derive_frontend(self.case, ['空调'], self.resources)
        self.assertEqual(plain['focal_passed'], 0)
        self.assertEqual(grouped['focal_passed'], 1)
        self.assertTrue(grouped['surface_preserved'])
        self.assertEqual(grouped['lexicon_derived_token_ids'], [3, 4, 2, 5, 6, 2])
        self.assertIn('not a captured tensor', grouped['token_scope'])

    def test_focal_can_align_inside_a_longer_native_word_using_syllable_boundaries(self):
        case = {'reference_text': '空调', 'focal_pronunciation': [
            {'text': '调', 'char_start': 1, 'char_end': 2, 'expected_phones': ['t', 'iao2', '#0']}]}
        result = P.derive_frontend(case, ['空调'], self.resources)
        self.assertEqual(result['focal_passed'], 1)

    def test_missing_native_words_cannot_borrow_expected_text_or_phones(self):
        result = P.derive_frontend(self.case, None, self.resources)
        self.assertIsNone(result['native_words'])
        self.assertEqual(result['focal_observed'], 0)
        self.assertIsNone(result['focal_checks'][0]['actual_phones'])

    def test_native_surface_change_and_oov_keep_failure_visible(self):
        result = P.derive_frontend(self.case, ['空', '坏'], self.resources)
        self.assertFalse(result['surface_preserved'])
        self.assertEqual(result['oov_words'], ['坏'])
        self.assertEqual(result['focal_passed'], 0)
        self.assertIsNone(result['focal_checks'][0]['actual_phones'])

    def test_unaligned_word_is_not_split_by_guessed_reference_phones(self):
        self.resources['lexicon']['空调'] = ['k', 'ong1', '#0']
        result = P.derive_frontend(self.case, ['空调'], self.resources)
        self.assertEqual(result['focal_observed'], 0)
        self.assertTrue(result['alignment_errors'])

    def test_bad_frozen_focal_span_is_rejected(self):
        for start, end in [(1, 2), (0, 99), (False, 2), (-1, 2)]:
            with self.subTest(span=(start, end)):
                self.case['focal_pronunciation'][0].update(char_start=start, char_end=end)
                with self.assertRaises(ValueError):
                    P.derive_frontend(self.case, ['空调'], self.resources)

    def test_no_focal_is_not_invented_as_a_pronunciation_pass(self):
        self.case['focal_pronunciation'] = []
        result = P.derive_frontend(self.case, ['空', '调'], self.resources)
        self.assertIsNone(result['all_focal_matched'])
        self.assertEqual(result['focal_total'], 0)

    def make_rows(self, count=2):
        cases = [{**self.case, 'id': str(i)} for i in range(count)]
        rows = []
        for arm in ('A', 'B'):
            for case in cases:
                path = self.root / (arm + case['id'] + '.wav')
                path.write_bytes((arm + case['id']).encode())  # Fake packet bytes, not native audio.
                rows.append({'id': case['id'], 'case': case, 'arm': arm, 'pcm16_wav': P.identity(path)})
        return {'cases': cases}, rows

    def test_packet_preserves_all_slots_and_blank_human_answers_with_alternating_key(self):
        p, rows = self.make_rows()
        out = self.root / 'packet'
        result = P.make_listening_packet(p, rows, out)
        ratings = list(csv.DictReader((out / 'ratings.csv').open()))
        self.assertEqual(result['audio_copied'], 4)
        self.assertEqual(result['ratings_rows'], 4)
        self.assertFalse(result['human_listening_accepted'])
        self.assertTrue(all(r['heard_words'] == r['pronunciation_correct'] == r['reviewer'] == '' for r in ratings))
        mapping = json.loads((out / 'mapping-key.json').read_text())['mapping']
        self.assertEqual([r['arm'] for r in mapping], ['A', 'B', 'B', 'A'])
        self.assertTrue(all(r['source_wav']['sha256'] == r['copied_wav']['sha256'] for r in mapping))
        self.assertNotIn('<audio autoplay', (out / 'listen.html').read_text())
        self.assertNotIn(b'\r', (out / 'ratings.csv').read_bytes())

    def test_packet_keeps_missing_audio_instead_of_selecting_successes(self):
        p, rows = self.make_rows(1)
        rows[0]['pcm16_wav'] = None
        result = P.make_listening_packet(p, rows, self.root / 'packet')
        self.assertEqual(result['ratings_rows'], 2)
        self.assertEqual(result['audio_copied'], 1)
        ratings = list(csv.DictReader((self.root / 'packet/ratings.csv').open()))
        self.assertEqual(ratings[0]['status'], 'missing_audio')

    def test_packet_rejects_selected_records_changed_wav_or_existing_directory(self):
        p, rows = self.make_rows(1)
        with self.assertRaises(ValueError):
            P.make_listening_packet(p, rows[:1], self.root / 'missing-record')
        Path(rows[0]['pcm16_wav']['path']).write_bytes(b'changed')
        with self.assertRaises(ValueError):
            P.make_listening_packet(p, rows, self.root / 'changed-wave')
        with self.assertRaises(FileExistsError):
            P.make_listening_packet(p, rows, self.root)


if __name__ == '__main__':
    unittest.main()
