"""Workflow integrity with synthetic temporary files; no models or human scores."""
from contextlib import redirect_stderr, redirect_stdout
import csv
import importlib.util
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location(
    'w16_listening_review', Path(__file__).parents[1] / 'scripts/w16_listening_review.py')
review = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(review)


def dump(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')


def write_csv(path, fields, rows):
    with path.open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)


class ListeningWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.run = self.root / 'frozen-run'
        (self.run / 'listening/audio').mkdir(parents=True)
        self.bundle = self.root / 'review-bundle'
        self.submission = self.root / 'human-observations.csv'
        self.cases = []
        for n in range(18):
            focal = [{'text': '调到', 'char_start': 0, 'char_end': 2,
                      'base_pinyin': ['tiao2', 'dao4'], 'expected_phones': ['t', 'iao2', '#0', 'd', 'ao4', '#0']}] if n < 15 else []
            self.cases.append({'id': f'case-{n:03d}', 'reference_text': '调到秘密标注' + str(n),
                               'focal_pronunciation': focal})
        fixture = self.root / 'fixed-cases.json'
        dump(fixture, {'test_only': True, 'cases': self.cases})
        models = self.root / 'model-resources'
        models.mkdir()
        model_files = []
        for n in range(9):
            resource = models / f'fake-resource-{n}.txt'
            resource.write_text('test-only-not-a-model-' + str(n))
            model_files.append(review.identity(resource))
        protocol = {'source_git': {'commit': review.RUNTIME_COMMIT, 'status': ''},
                    'case_fixture': review.identity(fixture), 'cases': self.cases,
                    'model_files': model_files}
        dump(self.run / 'protocol.json', protocol)
        self.mapping = []
        source_fields = ['review_id', 'case_id', 'slot', 'reference_text', 'expected_base_pinyin',
                         *review.SOURCE_HUMAN_FIELDS, 'status']
        source_ratings = []
        for n, case in enumerate(self.cases):
            for position, slot in enumerate(['X', 'Y']):
                arm = ['A', 'B'][position] if n % 2 == 0 else ['B', 'A'][position]
                rid = f'review-{n + 1:03d}-{slot}'
                original = self.run / 'workers' / arm / 'pcm16' / (case['id'] + '.wav')
                original.parent.mkdir(parents=True, exist_ok=True)
                original.write_bytes(b'RIFF-fake-unit-waveform-' + rid.encode())
                copy = self.run / 'listening/audio' / (rid + '.wav')
                shutil.copyfile(original, copy)
                self.mapping.append({'review_id': rid, 'case_id': case['id'], 'slot': slot,
                                     'arm': arm, 'source_wav': review.identity(original),
                                     'copied_wav': review.identity(copy)})
                row = dict.fromkeys(source_fields, '')
                row.update(review_id=rid, case_id=case['id'], slot=slot,
                           reference_text=case['reference_text'], status='pending',
                           expected_base_pinyin='tiao2 dao4' if n < 15 else '')
                source_ratings.append(row)
        dump(self.run / 'listening/mapping-key.json', {'mapping': self.mapping})
        write_csv(self.run / 'listening/ratings.csv', source_fields, source_ratings)
        dump(self.run / 'summary.json', {'test_only': True})
        (self.run / 'report.md').write_text('Synthetic unit fixture; no human judgement.\n')
        self.reindex()
        self.patch = patch.multiple(review,
            SOURCE_RAW_INDEX_SHA256=review.sha256(self.run / 'raw-index.json'),
            SOURCE_PROTOCOL_SHA256=review.sha256(self.run / 'protocol.json'),
            SOURCE_SUMMARY_SHA256=review.sha256(self.run / 'summary.json'),
            SOURCE_REPORT_SHA256=review.sha256(self.run / 'report.md'),
            FIXTURE_SHA256=review.sha256(fixture))
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def reindex(self):
        artifacts = []
        for path in sorted(self.run.rglob('*')):
            if path.is_file() and path.name not in ['raw-index.json', 'summary.json', 'report.md']:
                artifacts.append({'path': str(path.relative_to(self.run)),
                                  'bytes': path.stat().st_size, 'sha256': review.sha256(path)})
        dump(self.run / 'raw-index.json', {'artifacts': artifacts})

    def prepare(self):
        result = review.prepare(self.run, self.bundle)
        shutil.copyfile(self.bundle / 'reviewer/ratings-empty.csv', self.submission)
        return result

    def rows(self):
        return review._csv_rows(self.submission)[1]

    def save(self, rows):
        write_csv(self.submission, review.FIELDS, rows)

    def completed_row(self, row):
        return {**row, 'reviewer_id': 'listener_01', 'reviewed_at': '2026-11-13T12:00:00+08:00',
                'audibility': 'clear', 'heard_words': '实际听到别的内容', 'heard_pronunciation': 'bu4 que4 ding4',
                'pronunciation_confidence': 'uncertain', 'reference_access': 'previously_known',
                'attestation': 'human_listening', 'status': 'reviewed'}

    def edit_bundle_manifest(self, relative):
        path = self.bundle / 'manifest.json'
        value = json.loads(path.read_text())
        for entry in value['artifacts']:
            if entry['path'] == relative:
                target = self.bundle / relative
                entry.update(bytes=target.stat().st_size, sha256=review.sha256(target))
        dump(path, value)

    def test_prepare_preserves_all_audio_and_masks_reference(self):
        before = {str(p): review.sha256(p) for p in self.run.rglob('*') if p.is_file()}
        result = self.prepare()
        self.assertEqual(result['audio_slots'], 36)
        self.assertEqual(result['status'], 'pending')
        self.assertFalse(result['human_listening_accepted'])
        audio = list((self.bundle / 'reviewer/audio').glob('*.wav'))
        self.assertEqual(len(audio), 36)
        for m in self.mapping:
            self.assertEqual(review.sha256(self.bundle / 'reviewer/audio' / (m['review_id'] + '.wav')),
                             m['source_wav']['sha256'])
        html = (self.bundle / 'reviewer/listen.html').read_text()
        self.assertNotIn('秘密标注', html)
        self.assertNotIn('tiao2', html)
        self.assertNotIn('autoplay', html)
        self.assertEqual(html.count('<audio '), 36)
        _, empty = review._csv_rows(self.bundle / 'reviewer/ratings-empty.csv')
        self.assertTrue(all(not r['heard_words'] and not r['reviewer_id'] for r in empty))
        _, focals = review._csv_rows(self.bundle / 'coordinator/focal-adjudication-empty.csv')
        _, slots = review._csv_rows(self.bundle / 'coordinator/slot-adjudication-empty.csv')
        self.assertEqual((len(slots), len(focals)), (36, 30))
        self.assertTrue(all(not r['hearing_verdict'] and not r['first_pass_ratings_sha256'] for r in focals + slots))
        self.assertEqual(before, {str(p): review.sha256(p) for p in self.run.rglob('*') if p.is_file()})

    def test_existing_output_rejected_before_source_read(self):
        self.bundle.mkdir()
        with self.assertRaises(FileExistsError):
            review.prepare(self.root / 'absent-source', self.bundle)

    def test_nested_source_output_rejected(self):
        with self.assertRaises(ValueError):
            review.prepare(self.run, self.run / 'new-review')

    def test_output_symlink_parent_rejected(self):
        alias = self.root / 'alias'
        alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            review.prepare(self.run, alias / 'new-review')

    def test_changed_original_audio_rejected_without_new_output(self):
        Path(self.mapping[0]['source_wav']['path']).write_bytes(b'changed')
        with self.assertRaises(ValueError):
            review.prepare(self.run, self.bundle)
        self.assertFalse(self.bundle.exists())

    def test_copy_corruption_is_rejected_during_prepare(self):
        original_copy = review.shutil.copyfile
        def bad_copy(source, target):
            result = original_copy(source, target)
            if Path(target).suffix == '.wav':
                Path(target).write_bytes(b'corrupted copy')
            return result
        with patch.object(review.shutil, 'copyfile', side_effect=bad_copy):
            with self.assertRaisesRegex(ValueError, 'differs from the frozen waveform'):
                review.prepare(self.run, self.bundle)

    def test_original_summary_must_stay_unchanged(self):
        (self.run / 'summary.json').write_text('{}')
        with self.assertRaises(ValueError):
            review.prepare(self.run, self.bundle)

    def test_blank_source_ratings_required_even_if_reindexed(self):
        path = self.run / 'listening/ratings.csv'
        fields, rows = review._csv_rows(path)
        rows[0]['heard_words'] = 'not blank'
        write_csv(path, fields, rows)
        self.reindex()
        with patch.object(review, 'SOURCE_RAW_INDEX_SHA256', review.sha256(self.run / 'raw-index.json')):
            with self.assertRaisesRegex(ValueError, 'remain blank'):
                review.prepare(self.run, self.bundle)

    def test_all_original_model_resources_verified(self):
        model = self.root / 'model-resources/fake-resource-2.txt'
        model.write_text('different')
        with self.assertRaises(ValueError):
            review.prepare(self.run, self.bundle)

    def test_blank_and_partial_reviews_remain_pending(self):
        self.prepare()
        result = review.validate(self.bundle, self.submission)
        self.assertEqual((result['status'], result['reviewed_slots']), ('pending', 0))
        rows = self.rows()
        rows[0] = self.completed_row(rows[0])
        self.save(rows)
        result = review.validate(self.bundle, self.submission)
        self.assertEqual((result['status'], result['reviewed_slots'], result['pending_slots']), ('pending', 1, 35))

    def test_full_human_structure_does_not_claim_acoustic_correctness(self):
        self.prepare()
        self.save([self.completed_row(r) for r in self.rows()])
        result = review.validate(self.bundle, self.submission)
        self.assertTrue(result['structure_complete'])
        self.assertEqual(result['reviewed_slots'], 36)
        self.assertEqual(result['reference_access_counts'], {'previously_known': 36})
        for key in ['human_listening_accepted', 'actual_acoustic_pronunciation_accepted',
                    'human_household_material_accepted', 'personal_understanding_accepted']:
            self.assertFalse(result[key])

    def test_unintelligible_observations_are_allowed_without_forced_transcript(self):
        self.prepare()
        rows = [self.completed_row(r) for r in self.rows()]
        for row in rows:
            row.update(audibility='unintelligible', heard_words='', heard_pronunciation='unintelligible',
                       pronunciation_confidence='unintelligible')
        self.save(rows)
        self.assertTrue(review.validate(self.bundle, self.submission)['structure_complete'])

    def test_each_reviewer_requires_their_own_all_slot_submission(self):
        self.prepare()
        rows = [self.completed_row(r) for r in self.rows()]
        rows[0]['reviewer_id'] = 'different_listener'
        self.save(rows)
        with self.assertRaisesRegex(ValueError, 'separate full 36-slot'):
            review.validate(self.bundle, self.submission)

    def test_duplicate_missing_extra_and_changed_identity_rejected(self):
        self.prepare()
        original = self.rows()
        for change in ['duplicate', 'missing', 'unknown', 'hash', 'slot']:
            with self.subTest(change=change):
                rows = [dict(r) for r in original]
                if change == 'duplicate': rows[-1] = dict(rows[0])
                elif change == 'missing': rows.pop()
                elif change == 'unknown': rows[-1]['review_id'] = 'review-999-X'
                elif change == 'hash': rows[0]['audio_sha256'] = '0' * 64
                else: rows[0]['slot'] = 'Y'
                self.save(rows)
                with self.assertRaises(ValueError): review.validate(self.bundle, self.submission)

    def test_invalid_human_metadata_rejected(self):
        self.prepare()
        original = [self.completed_row(r) for r in self.rows()]
        for field, bad in [('attestation', 'asr'), ('audibility', 'excellent'),
                           ('reviewer_id', ''), ('reviewer_id', 'real name with spaces'),
                           ('reviewed_at', '2026-11-13'), ('reviewed_at', '2026-11-13T12:00:00'),
                           ('heard_words', ''), ('heard_pronunciation', ''), ('status', ''),
                           ('reference_access', 'blind'), ('comments', 'a' * 4001)]:
            with self.subTest(field=field, bad=bad[:60]):
                rows = [dict(r) for r in original]
                rows[0][field] = bad
                self.save(rows)
                with self.assertRaises(ValueError): review.validate(self.bundle, self.submission)

    def test_duplicate_header_and_row_width_rejected(self):
        self.prepare()
        original = self.submission.read_text()
        for invalid in [original.replace('reviewer_id', 'review_id', 1),
                        original.replace('\n', ',extra\n', 2)]:
            with self.subTest(invalid=invalid[:50]):
                self.submission.write_text(invalid)
                with self.assertRaises(ValueError): review.validate(self.bundle, self.submission)

    def test_template_and_audio_tampering_rejected_even_with_rewritten_manifest(self):
        self.prepare()
        relative = 'reviewer/ratings-empty.csv'
        target = self.bundle / relative
        target.write_text(target.read_text().replace('pending', 'reviewed', 1))
        self.edit_bundle_manifest(relative)
        with self.assertRaisesRegex(ValueError, 'template changed'):
            review.validate(self.bundle, self.submission)
        target.write_text(review._template(self.mapping))
        self.edit_bundle_manifest(relative)
        relative = 'reviewer/audio/review-001-X.wav'
        (self.bundle / relative).write_bytes(b'changed-audio')
        self.edit_bundle_manifest(relative)
        with self.assertRaisesRegex(ValueError, 'original waveform'):
            review.validate(self.bundle, self.submission)

    def test_second_pass_template_cannot_be_edited_in_place(self):
        self.prepare()
        relative = 'coordinator/focal-adjudication-empty.csv'
        target = self.bundle / relative
        target.write_text(target.read_text().replace('tiao2', 'diao4', 1))
        self.edit_bundle_manifest(relative)
        with self.assertRaisesRegex(ValueError, 'second-pass template changed'):
            review.validate(self.bundle, self.submission)

    def test_submission_must_be_outside_bundle_and_source_and_not_symlink(self):
        self.prepare()
        with self.assertRaises(ValueError):
            review.validate(self.bundle, self.bundle / 'reviewer/ratings-empty.csv')
        alias = self.root / 'submission-link.csv'
        alias.symlink_to(self.submission)
        with self.assertRaises(ValueError): review.validate(self.bundle, alias)

    def test_unindexed_directory_symlinks_are_rejected_in_source_and_bundle(self):
        alias = self.run / 'extra-directory-link'
        alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'Symlink entry'):
            review.prepare(self.run, self.bundle)
        alias.unlink()
        self.prepare()
        (self.bundle / 'extra-directory-link').symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'Symlink entry'):
            review.validate(self.bundle, self.submission)

    def test_source_is_rechecked_during_validation(self):
        self.prepare()
        Path(self.mapping[-1]['source_wav']['path']).write_bytes(b'changed later')
        with self.assertRaises(ValueError): review.validate(self.bundle, self.submission)

    def test_rating_contents_and_reported_identity_use_the_same_snapshot(self):
        self.prepare()
        original = review._csv_rows
        def mutate_after_read(path):
            result = original(path)
            if Path(path) == self.submission:
                self.submission.write_text(self.submission.read_text() + '\n')
            return result
        with patch.object(review, '_csv_rows', side_effect=mutate_after_read):
            with self.assertRaisesRegex(ValueError, 'changed during validation'):
                review.validate(self.bundle, self.submission)

    def test_claimed_acceptance_in_manifest_rejected(self):
        self.prepare()
        path = self.bundle / 'manifest.json'
        value = json.loads(path.read_text())
        value['human_listening_accepted'] = True
        dump(path, value)
        with self.assertRaises(ValueError): review.validate(self.bundle, self.submission)

    def test_cli_pending_complete_and_rejection_exit_codes(self):
        self.prepare()
        args = ['validate', '--bundle', str(self.bundle), '--ratings', str(self.submission)]
        with redirect_stdout(io.StringIO()): self.assertEqual(review.main(args), 3)
        self.save([self.completed_row(r) for r in self.rows()])
        with redirect_stdout(io.StringIO()): self.assertEqual(review.main(args), 0)
        self.submission.write_text('bad csv')
        with redirect_stderr(io.StringIO()): self.assertEqual(review.main(args), 2)


if __name__ == '__main__':
    unittest.main()
