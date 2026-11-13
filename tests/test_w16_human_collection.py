"""Artificial WAV/consent fixtures exercise structure; they are not human evidence."""
from __future__ import annotations

import array
import csv
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import wave

from scripts import w16_human_collection as HUMAN

ROOT = Path(__file__).resolve().parents[1]


class HumanCollectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.package = self.root / 'package'
        self.fixture = ROOT / 'configs/w16-pronunciation-cases-v1.json'
        self.fixture_before = self.fixture.read_bytes()
        HUMAN.prepare(self.fixture, self.package)
        self.forms = self.root / 'forms'
        self.forms.mkdir()
        self.participants = HUMAN.read_csv(self.package / 'participants-empty.csv', HUMAN.PARTICIPANT_FIELDS, 2)
        self.recordings = HUMAN.read_csv(self.package / 'recordings-empty.csv', HUMAN.RECORDING_FIELDS, 36)

    def write_forms(self):
        p, r = self.forms / 'participants.csv', self.forms / 'recordings.csv'
        HUMAN.write_csv(p, HUMAN.PARTICIPANT_FIELDS, self.participants)
        HUMAN.write_csv(r, HUMAN.RECORDING_FIELDS, self.recordings)
        return p, r

    def run_audit(self, name='audit'):
        p, r = self.write_forms()
        return HUMAN.audit(self.package, p, r, self.root / name)

    def wav(self, path, value, rate=16000, channels=1, width=2, frames=3200):
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), 'wb') as out:
            out.setparams((channels, width, rate, 0, 'NONE', 'not compressed'))
            data = array.array('h', [value] * frames * channels).tobytes() if width == 2 else b'\x80' * frames * channels
            out.writeframes(data)

    def complete_fake_declarations(self):
        # These are deliberately artificial test declarations; never archived as real consent.
        for i, p in enumerate(self.participants):
            p.update(source_kind='human_self_recording', adult_self_attested='true',
                     recorder_is_participant='true', local_evaluation_consent='true',
                     consent_recorded_at='2026-11-12T00:00:00+00:00',
                     consent_record_id=f'UNIT-TEST-NOT-REAL-{i}', recording_context='unit_test_only')
        for i, row in enumerate(self.recordings):
            wav = self.root / 'incoming' / f'{i:02d}.wav'
            self.wav(wav, i + 1)
            row.update(wav_path=str(wav), captured_at='2026-11-13T00:00:00Z',
                       take_index='1', source_kind='human_self_recording',
                       spoken_text_verbatim='人工应据实填写；本行仅测试')

    def test_prepare_preserves_all_frozen_texts_and_split_before_recording(self):
        plan = HUMAN.load_plan(self.package)
        self.assertEqual(len(plan['slots']), 36)
        self.assertEqual(len({s['case_id'] for s in plan['slots']}), 18)
        self.assertEqual([r['split'] for r in self.participants], ['development', 'speaker_validation'])
        self.assertTrue(plan['policy']['same_texts_in_both_splits'])
        self.assertFalse(plan['policy']['unseen_text_heldout'])
        self.assertFalse(plan['human_household_recordings_accepted'])
        self.assertEqual(self.fixture.read_bytes(), self.fixture_before)
        self.assertTrue(all(p['adult_self_attested'] == '' and p['local_evaluation_consent'] == '' for p in self.participants))
        self.assertTrue(all(p['public_release_authorized'] == 'false' for p in self.participants))

    def test_empty_forms_remain_pending_with_all_slots_and_no_audio(self):
        result = self.run_audit()
        self.assertEqual(result['status'], 'pending')
        self.assertEqual(len(result['slots']), 36)
        self.assertEqual(result['supplied_audio_files'], 0)
        self.assertFalse(result['human_household_recordings_accepted'])
        self.assertEqual(result['recording_asr_tts_playback_or_device_calls'], 0)
        self.assertTrue(result['sources_unchanged'])

    def test_complete_structure_never_claims_genuine_human_or_acoustic_acceptance(self):
        self.complete_fake_declarations()
        before = {r['wav_path']: HUMAN.identity(Path(r['wav_path'])) for r in self.recordings}
        result = self.run_audit()
        self.assertEqual(result['status'], 'structure_complete_declarations_unverified')
        self.assertEqual(result['copied_original_files'], 36)
        self.assertFalse(result['source_declarations_verified_as_true'])
        self.assertFalse(result['human_household_recordings_accepted'])
        self.assertFalse(result['human_listening_accepted'])
        self.assertFalse(result['personal_understanding_accepted'])
        self.assertFalse(result['asr_accuracy_measured'])
        for row in result['slots']:
            self.assertEqual(row['source_audio']['sha256'], row['copied_original']['sha256'])
        self.assertEqual(before, {r['wav_path']: HUMAN.identity(Path(r['wav_path'])) for r in self.recordings})

    def test_audio_without_consent_is_rejected_and_not_imported(self):
        self.complete_fake_declarations()
        for p in self.participants:
            p['local_evaluation_consent'] = ''
        result = self.run_audit()
        self.assertEqual(result['status'], 'rejected')
        self.assertEqual(result['copied_original_files'], 0)
        self.assertEqual(result['supplied_audio_files'], 36)
        self.assertTrue(all(s['source_audio']['sha256'] for s in result['slots']))

    def test_adult_and_self_recording_declarations_cannot_be_assumed(self):
        self.complete_fake_declarations()
        self.participants[0]['adult_self_attested'] = 'false'
        self.participants[1]['recorder_is_participant'] = 'false'
        result = self.run_audit()
        self.assertEqual(result['status'], 'rejected')
        self.assertEqual(result['copied_original_files'], 0)

    def test_tts_mock_or_public_license_claim_rejected_not_inferred(self):
        self.complete_fake_declarations()
        self.participants[0]['source_kind'] = 'tts'
        self.participants[1]['public_release_authorized'] = 'CC0'
        self.recordings[0]['source_kind'] = 'mock'
        result = self.run_audit()
        self.assertEqual(result['status'], 'rejected')
        self.assertEqual(result['copied_original_files'], 0)
        self.assertFalse(result['public_release_authorized'])

    def test_relabel_split_reorder_or_missing_rows_rejected(self):
        for mutation in ['reference', 'split', 'duplicate', 'missing']:
            with self.subTest(mutation=mutation):
                rows = [dict(r) for r in self.recordings]
                if mutation == 'reference':
                    rows[0]['reference_text'] = '改标签'
                elif mutation == 'split':
                    rows[0]['split'] = 'speaker_validation'
                elif mutation == 'duplicate':
                    rows[1] = dict(rows[0])
                else:
                    rows.pop()
                p, r = self.write_forms()
                HUMAN.write_csv(r, HUMAN.RECORDING_FIELDS, rows)
                with self.assertRaises(ValueError):
                    HUMAN.audit(self.package, p, r, self.root / mutation)

    def test_duplicate_audio_cannot_leak_across_speakers_or_slots(self):
        self.complete_fake_declarations()
        self.recordings[18]['wav_path'] = self.recordings[0]['wav_path']
        result = self.run_audit()
        self.assertEqual(result['status'], 'rejected')
        self.assertIn('DUPLICATE_AUDIO_WITH:P01-01', result['slots'][18]['issues'])
        self.assertEqual(result['copied_original_files'], 36)

    def test_invalid_format_is_retained_with_hash_and_error_not_resampled(self):
        self.complete_fake_declarations()
        path = Path(self.recordings[0]['wav_path'])
        self.wav(path, 1, rate=8000)
        result = self.run_audit()
        self.assertEqual(result['status'], 'rejected')
        slot = result['slots'][0]
        self.assertIn('WAV_FORMAT_MUST_BE_PCM16_MONO_16000', slot['issues'])
        self.assertEqual(slot['source_audio']['sha256'], slot['copied_original']['sha256'])
        self.assertEqual(slot['source_audio']['sample_rate'], 8000)

    def test_empty_truncated_long_and_bad_wav_preserve_diagnostics(self):
        self.complete_fake_declarations()
        self.wav(Path(self.recordings[0]['wav_path']), 1, frames=0)
        path = Path(self.recordings[1]['wav_path'])
        path.write_bytes(path.read_bytes()[:-20])
        self.wav(Path(self.recordings[2]['wav_path']), 1, frames=320001)
        Path(self.recordings[3]['wav_path']).write_bytes(b'not a wave')
        result = self.run_audit()
        self.assertEqual(result['status'], 'rejected')
        self.assertTrue(all(result['slots'][i]['issues'] for i in range(4)))
        self.assertEqual(result['copied_original_files'], 36)

    def test_clipping_silence_and_spoken_mistakes_are_not_success_selected(self):
        self.complete_fake_declarations()
        self.wav(Path(self.recordings[0]['wav_path']), -32768)
        self.wav(Path(self.recordings[1]['wav_path']), 0)
        self.recordings[2]['spoken_text_verbatim'] = '读错了的原话'
        result = self.run_audit()
        self.assertEqual(result['copied_original_files'], 36)
        self.assertEqual(result['slots'][0]['source_audio']['clipped_sample_count'], 3200)
        self.assertTrue(result['slots'][1]['source_audio']['silent'])
        self.assertEqual(result['slots'][2]['provider_spoken_text_verbatim'], '读错了的原话')
        self.assertEqual(result['slots'][2]['reference_text'], self.recordings[2]['reference_text'])
        self.assertFalse(result['human_household_recordings_accepted'])

    def test_consent_must_predate_capture_and_timestamps_need_timezone(self):
        self.complete_fake_declarations()
        self.participants[0]['consent_recorded_at'] = '2026-11-14T00:00:00Z'
        self.recordings[18]['captured_at'] = '2026-11-13T00:00:00'
        result = self.run_audit()
        self.assertEqual(result['status'], 'rejected')
        self.assertIn('CONSENT_POSTDATES_CAPTURE', result['slots'][0]['issues'])
        self.assertIn('INVALID_CAPTURE_OR_CONSENT_TIMESTAMP', result['slots'][18]['issues'])
        self.assertEqual(result['copied_original_files'], 17)

    def test_selected_retry_is_not_a_first_take(self):
        self.complete_fake_declarations()
        self.recordings[0]['take_index'] = '2'
        result = self.run_audit()
        self.assertEqual(result['status'], 'rejected')
        self.assertIn('FIRST_TAKE_REQUIRED_RETRIES_NEED_NEW_PROTOCOL', result['slots'][0]['issues'])
        self.assertEqual(result['copied_original_files'], 36)

    def test_existing_nested_or_symlink_outputs_refused_without_source_changes(self):
        p, r = self.write_forms()
        for target in [self.package, self.package / 'nested']:
            with self.subTest(target=target), self.assertRaises((ValueError, FileExistsError)):
                HUMAN.audit(self.package, p, r, target)
        link = self.root / 'link'
        link.symlink_to(self.forms, target_is_directory=True)
        with self.assertRaises(ValueError):
            HUMAN.audit(self.package, p, r, link / 'out')
        self.assertEqual(HUMAN.load_plan(self.package)['policy'], HUMAN.POLICY)

    def test_plan_or_frozen_fixture_mutation_refused(self):
        path = self.package / 'plan.json'
        plan = HUMAN.read(path)
        plan['slots'][0]['reference_text'] = '改标签'
        HUMAN.write_json(path, plan)
        with self.assertRaises(ValueError):
            HUMAN.load_plan(self.package)
        altered = self.root / 'altered.json'
        altered.write_text(self.fixture.read_text() + ' ')
        with self.assertRaises(ValueError):
            HUMAN.prepare(altered, self.root / 'altered-out')

    def test_extra_package_files_and_index_symlink_are_refused(self):
        extra = self.package / 'unexpected.wav'
        extra.write_bytes(b'extra')
        with self.assertRaisesRegex(ValueError, 'unexpected file'):
            HUMAN.load_plan(self.package)
        extra.unlink()
        index = self.package / 'package-index.json'
        outside = self.root / 'index-copy.json'
        outside.write_bytes(index.read_bytes())
        index.unlink()
        index.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            HUMAN.load_plan(self.package)

    def test_unknown_sensitive_columns_are_rejected(self):
        p, r = self.write_forms()
        HUMAN.write_csv(p, HUMAN.PARTICIPANT_FIELDS + ['legal_name'],
                        [{**row, 'legal_name': ''} for row in self.participants])
        with self.assertRaises(ValueError):
            HUMAN.audit(self.package, p, r, self.root / 'audit')

    def test_csv_mutation_during_copy_cannot_be_reported_unchanged(self):
        p, r = self.write_forms()
        original_copy = HUMAN.shutil.copyfile
        def changing_copy(source, destination):
            if source == p:
                p.write_text(p.read_text() + '\n')
            return original_copy(source, destination)
        with patch.object(HUMAN.shutil, 'copyfile', side_effect=changing_copy):
            with self.assertRaisesRegex(ValueError, 'CSV changed'):
                HUMAN.audit(self.package, p, r, self.root / 'audit')
        self.assertFalse((self.root / 'audit/report.json').exists())

    def test_cli_pending_exit_is_not_pass_and_existing_output_is_error(self):
        p, r = self.write_forms()
        command = [sys.executable, str(ROOT / 'scripts/w16_human_collection.py'), 'audit',
                   '--collection', str(self.package), '--participants', str(p),
                   '--recordings', str(r), '--outdir', str(self.root / 'audit')]
        result = subprocess.run(command, text=True, capture_output=True)
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'], 'pending')
        again = subprocess.run(command, text=True, capture_output=True)
        self.assertEqual(again.returncode, 2)
        self.assertIn('already exists', again.stderr)


if __name__ == '__main__':
    unittest.main()
