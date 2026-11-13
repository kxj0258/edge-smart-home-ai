"""Repair preparation uses Fake resampling only, no SciPy or neural inference."""
from argparse import Namespace
import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch
import wave

import numpy as np

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/prepare_w16_repair_audio.py'
SPEC = importlib.util.spec_from_file_location('w16_repair_preparation', SCRIPT)
P = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(P)


class FakeResampler:
    """Shape-preserving fake, deliberately NOT an antialiasing algorithm."""
    def __init__(self):
        self.calls = []
        self.hook = None
        self.bad_output = None

    def __call__(self, value, **kwargs):
        self.calls.append((value.copy(), kwargs))
        if self.hook:
            self.hook()
        if self.bad_output is not None:
            return self.bad_output
        return np.repeat(value, kwargs['up'])[::kwargs['down']]


class RepairPreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.fake = FakeResampler()
        self.git = patch.object(P, 'git_info', return_value={'commit': 'a' * 40, 'status': ''})
        self.git.start(); self.addCleanup(self.git.stop)
        self.home_manifest = self.root / 'home-manifest.json'
        self.news_manifest = self.root / 'news-manifest.json'
        self.news_manifest.write_text('{"kind":"fake_source_news_manifest"}')
        clips = []
        for index, clip_id in enumerate(P.HOME_IDS):
            pcm = np.array([-4096, 0, 4096, 8192, 4096, 0, -4096, 0, 4096], dtype='<i2')
            if index == 9: pcm[:] = 0
            wav = self.wav(clip_id, pcm, 8000)
            floats = self.root / (clip_id + '.npy')
            np.save(floats, pcm.astype(np.float32) / np.float32(32768), allow_pickle=False)
            clips.append({'id': clip_id, 'case_id': clip_id, 'reference_text': '打开客厅灯' if index < 9 else '',
                          'source_kind': 'synthetic_audio' if index < 9 else 'synthetic_signal',
                          'wav': str(wav), 'sha256': P.identity(wav)['sha256'],
                          'audio_identity': P.identity(wav), 'float_npy': P.identity(floats)})
        self.home_manifest.write_text(json.dumps({'schema_version': 1, 'kind': 'w16_audio_manifest',
            'preparation_kind': 'native_tts_audio', 'first_generation_preserved': True, 'clips': clips}))
        self.home_protocol = self.root / 'home-protocol.json'
        self.home_protocol.write_text(json.dumps({'schema_version': 1, 'kind': 'w16_frozen_voice_home_protocol',
            'state': 'frozen', 'manifest': P.identity(self.home_manifest), 'clips': clips}))
        cases = []
        for clip_id in P.NEWS_IDS:
            wav = self.wav(clip_id, np.array([-8192, 0, 8192, 4096, 0, -4096, 0], dtype='<i2'), 16000)
            cases.append({'id': clip_id, 'expected': '公开新闻朗读', 'source_kind': 'human',
                          'source_url': 'https://invalid.test/public-fixture', 'license': 'CC-BY-4.0',
                          'attribution': 'Fake source provenance in an isolated unit fixture',
                          'wav': str(wav), 'sha256': P.identity(wav)['sha256'], 'audio_identity': P.identity(wav)})
        self.news_protocol = self.root / 'news-protocol.json'
        self.news_protocol.write_text(json.dumps({'schema_version': 1, 'kind': 'w14_frozen_asr_protocol',
            'state': 'frozen', 'manifest': P.identity(self.news_manifest), 'cases': cases}))
        self.args = Namespace(home_protocol=self.home_protocol, news_protocol=self.news_protocol,
                              environment_lock=self.root / 'must-not-read-for-fake.json', outdir=self.root / 'output')

    def wav(self, name, values, rate):
        p = self.root / (name + '.wav')
        with wave.open(str(p), 'wb') as target:
            target.setnchannels(1); target.setsampwidth(2); target.setframerate(rate)
            target.writeframes(values.tobytes())
        return p

    def prepare(self):
        return P.prepare(self.args, self.fake)

    def rewrite_protocol(self, which, mutate):
        path = self.home_protocol if which == 'home' else self.news_protocol
        data = P.read(path); mutate(data); path.write_text(json.dumps(data))

    def test_all_frozen_sources_once_fake_cannot_be_native_evidence(self):
        result = self.prepare()
        self.assertEqual(result['preparation_kind'], 'fake_unit_fixture')
        self.assertEqual((result['home'], result['news'], result['derivation_attempts']), (10, 5, 15))
        manifest = P.read(self.args.outdir / 'manifest.json')
        self.assertEqual(manifest['kind'], 'w16_repair_derived_audio_manifest')
        self.assertEqual(manifest['preparation_kind'], 'fake_unit_fixture')
        self.assertTrue(manifest['first_waveforms_preserved'])
        self.assertFalse(manifest['reference_texts_changed'])
        self.assertEqual(manifest['new_tts_generations'], 0)
        self.assertEqual(manifest['source_inputs_before'], manifest['source_inputs_after'])
        self.assertEqual([r['id'] for r in manifest['home']], list(P.HOME_IDS))
        self.assertEqual([r['id'] for r in manifest['news']], list(P.NEWS_IDS))
        self.assertEqual(len(self.fake.calls), 15)
        for row in manifest['home']:
            values = np.load(row['resampled_float_identity']['path'], allow_pickle=False)
            self.assertEqual((values.dtype, values.shape, row['resampled_sample_rate']), (np.dtype('float32'), (18,), 16000))
            self.assertEqual(row['frames_before'], 9)
            self.assertEqual(row['reference_text'], '打开客厅灯' if row['id'] != 'W16-A05' else '')
        for row in manifest['news']:
            self.assertEqual((row['frames_before'], row['frames_after'], row['downsampled_sample_rate']), (7, 4, 8000))
            self.assertEqual(row['source_kind'], 'human_public_news')
            self.assertEqual(row['license'], 'CC-BY-4.0')
        journal = P.read(self.args.outdir / 'preparation.json')
        self.assertTrue(journal['passed'])
        self.assertEqual(journal['asr_calls'], 0)
        self.assertEqual(len((self.args.outdir / 'journal.jsonl').read_text().splitlines()), 30)

    def test_source_is_pcm_float_not_original_tts_float_and_fixed_parameters(self):
        # A representable sub-LSB deviation in original TTS still quantizes to
        # the same frozen PCM; the resampler must receive decoded PCM instead.
        protocol = P.read(self.home_protocol); manifest = P.read(self.home_manifest)
        first = protocol['clips'][0]; p = Path(first['float_npy']['path'])
        raw = np.load(p, allow_pickle=False); raw[1] = 1.0 / 131072
        np.save(p, raw, allow_pickle=False)
        for data in (protocol, manifest): data['clips'][0]['float_npy'] = P.identity(p)
        self.home_manifest.write_text(json.dumps(manifest)); protocol['manifest'] = P.identity(self.home_manifest)
        self.home_protocol.write_text(json.dumps(protocol))
        self.prepare()
        self.assertEqual(float(self.fake.calls[0][0][1]), 0.0)
        self.assertNotEqual(float(raw[1]), 0.0)
        for i, (_, params) in enumerate(self.fake.calls):
            self.assertEqual(params, {'up': 2 if i < 10 else 1, 'down': 1 if i < 10 else 2,
                                     'window': ('kaiser', 5.0), 'padtype': 'constant', 'cval': 0.0})

    def test_existing_output_rejected_before_inputs_git_or_algorithm(self):
        self.args.outdir.mkdir(); marker = self.args.outdir / 'keep'; marker.write_bytes(b'unchanged')
        with patch.object(P, 'read', side_effect=AssertionError('read')), patch.object(P, 'git_info', side_effect=AssertionError('git')):
            with self.assertRaises(FileExistsError): self.prepare()
        self.assertEqual(self.fake.calls, [])
        self.assertEqual(marker.read_bytes(), b'unchanged')

    def test_dirty_formal_sources_fail_without_importing_scipy(self):
        with patch.object(P, 'git_info', return_value={'commit': 'a'*40, 'status': ' M code.py'}), patch.object(P, 'load_resampler') as load:
            with self.assertRaisesRegex(ValueError, 'clean committed'): P.prepare(self.args)
        load.assert_not_called()
        self.assertFalse(P.read(self.args.outdir / 'preparation.json')['passed'])
        self.assertFalse((self.args.outdir / 'manifest.json').exists())

    def test_missing_protocol_keeps_failure_journal(self):
        self.home_protocol.unlink()
        with self.assertRaises(FileNotFoundError): self.prepare()
        self.assertEqual(self.fake.calls, [])
        result = P.read(self.args.outdir / 'preparation.json')
        self.assertFalse(result['passed'])
        self.assertEqual(result['derivation_attempts'], 0)
        self.assertEqual(result['error']['type'], 'FileNotFoundError')

    def test_changed_wav_prevents_derivation_and_keeps_failure(self):
        clip = P.read(self.home_protocol)['clips'][0]
        Path(clip['audio_identity']['path']).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'identity differs'): self.prepare()
        self.assertEqual(self.fake.calls, [])
        self.assertFalse(P.read(self.args.outdir / 'preparation.json')['passed'])

    def test_incomplete_or_reordered_sets_are_rejected(self):
        for group in ('home', 'news'):
            with self.subTest(group=group):
                args = Namespace(**vars(self.args)); args.outdir = self.root / ('out-' + group)
                path = self.home_protocol if group == 'home' else self.news_protocol
                old = path.read_text(); data = json.loads(old)
                data['clips' if group == 'home' else 'cases'].reverse(); path.write_text(json.dumps(data))
                with self.assertRaisesRegex(ValueError, 'frozen order'): P.prepare(args, self.fake)
                path.write_text(old)
        self.assertEqual(self.fake.calls, [])

    def test_unreviewed_news_license_not_promoted_to_human_home(self):
        self.rewrite_protocol('news', lambda d: d['cases'][0].update(license='UNKNOWN'))
        with self.assertRaisesRegex(ValueError, 'license binding'): self.prepare()
        self.assertEqual(self.fake.calls, [])

    def test_fake_original_tts_material_is_rejected(self):
        data = P.read(self.home_manifest); data['preparation_kind'] = 'fake_tts_unit_fixture'
        self.home_manifest.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, 'first native-TTS'): self.prepare()
        self.assertEqual(self.fake.calls, [])

    def test_home_reference_cannot_differ_from_first_manifest(self):
        self.rewrite_protocol('home', lambda d: d['clips'][0].update(reference_text='改变标签'))
        with self.assertRaisesRegex(ValueError, 'manifest clip differs'): self.prepare()
        self.assertEqual(self.fake.calls, [])

    def test_original_float_wave_binding_checked_without_using_it_as_source(self):
        protocol = P.read(self.home_protocol); manifest = P.read(self.home_manifest)
        p = Path(protocol['clips'][0]['float_npy']['path']); np.save(p, np.zeros(9, dtype=np.float32), allow_pickle=False)
        for data in (protocol, manifest): data['clips'][0]['float_npy'] = P.identity(p)
        self.home_manifest.write_text(json.dumps(manifest)); protocol['manifest'] = P.identity(self.home_manifest)
        self.home_protocol.write_text(json.dumps(protocol))
        with self.assertRaisesRegex(ValueError, 'quantization binding'): self.prepare()
        self.assertEqual(self.fake.calls, [])

    def test_frame_count_wrong_dtype_nonfinite_and_overshoot_rejected_without_clip(self):
        for i, value in enumerate((np.zeros(17, dtype=np.float32), np.zeros(18, dtype=np.float64),
                                  np.full(18, np.nan, dtype=np.float32), np.full(18, 1.0001, dtype=np.float32))):
            with self.subTest(value=repr(value)):
                args = Namespace(**vars(self.args)); args.outdir = self.root / ('bad-' + str(i))
                fake = FakeResampler(); fake.bad_output = value
                with self.assertRaises(ValueError): P.prepare(args, fake)
                result = P.read(args.outdir / 'preparation.json')
                self.assertFalse(result['passed']); self.assertEqual(result['derivation_attempts'], 1)
                self.assertFalse((args.outdir / 'manifest.json').exists())

    def test_mid_run_source_mutation_preserves_partial_outputs_and_failure(self):
        path = self.home_manifest; original = path.read_bytes()
        self.fake.hook = lambda: path.write_bytes(original + b'\n')
        with self.assertRaisesRegex(ValueError, 'input or preparation source changed'): self.prepare()
        result = P.read(self.args.outdir / 'preparation.json')
        self.assertFalse(result['passed']); self.assertEqual(len(result['home']), 10)
        self.assertEqual(len(result['news']), 5)
        self.assertNotEqual(result['source_inputs_before'], result['observed_inputs_after_failure'])
        self.assertFalse((self.args.outdir / 'manifest.json').exists())

    def test_wrong_pcm_rate_stereo_or_width_is_rejected(self):
        p = self.wav('wrong-rate', np.zeros(3, dtype='<i2'), 16000)
        with self.assertRaisesRegex(ValueError, 'frozen true rate'): P.read_pcm16(p, 8000)
        for name, channels, width in [('stereo', 2, 2), ('width', 1, 1)]:
            with wave.open(str(self.root / (name+'.wav')), 'wb') as out:
                out.setnchannels(channels);out.setsampwidth(width);out.setframerate(8000);out.writeframes(b'\0' * (4*channels*width))
            with self.subTest(name=name), self.assertRaises(ValueError): P.read_pcm16(self.root / (name+'.wav'), 8000)

    def test_validate_float_never_silently_coerces_shapes_or_values(self):
        for value in ([], [0.], np.array([0], dtype=np.int16), np.zeros((2, 2), dtype=np.float32),
                      np.array([np.inf], dtype=np.float32), np.array([-1.01], dtype=np.float32)):
            with self.subTest(value=repr(value)), self.assertRaises(ValueError): P.validate_float(value)
        expected = np.array([-1, 0, 1], dtype=np.float32)
        self.assertIs(P.validate_float(expected), expected)

    def test_dependency_lock_bad_versions_or_missing_evidence_rejected(self):
        lock = self.root / 'lock.json'
        lock.write_text(json.dumps({'schema_version': 1, 'kind': 'w16_resampling_environment_lock',
                                    'numpy': '2.2.6', 'scipy': '1.15.2', 'asr_environment_unchanged': True}))
        with self.assertRaisesRegex(ValueError, 'frozen separate'): P.load_resampler(lock)
        data = P.read(lock); data.update(scipy='1.15.3', python=P.platform.python_version(),
                                        executable=str(Path(P.sys.executable).absolute()))
        lock.write_text(json.dumps(data))
        with patch.object(P.sys, 'prefix', str(self.root/'isolated')), patch.object(P.sys, 'base_prefix', str(self.root/'base')):
            with self.assertRaisesRegex(ValueError, 'Missing resampling dependency'): P.load_resampler(lock)

    def test_self_check_catches_fake_decimation_alias_not_speech_accuracy(self):
        with self.assertRaisesRegex(ValueError, 'antialias algorithm'): P.self_check(self.args, self.fake)
        result = P.read(self.args.outdir / 'self-check.json')
        self.assertFalse(result['passed']); self.assertEqual(result['speech_samples'], 0)
        self.assertEqual(result['asr_calls'], 0); self.assertEqual(result['tts_calls'], 0)
        self.assertEqual(result['preparation_kind'], 'fake_unit_fixture')
        self.assertGreater(result['measurements']['downsampled_6khz_alias_rms_ratio'], .9)

    def test_existing_self_check_output_rejected_before_algorithm(self):
        self.args.outdir.mkdir()
        with self.assertRaises(FileExistsError): P.self_check(self.args, self.fake)
        self.assertEqual(self.fake.calls, [])

    def test_main_failure_returns_two_and_keeps_structured_evidence(self):
        self.args.outdir.mkdir()
        with patch.object(P.sys, 'stderr'), patch.object(P.sys, 'stdout'):
            self.assertEqual(P.main(['--outdir', str(self.args.outdir)]), 2)
        self.assertEqual(self.fake.calls, [])


if __name__ == '__main__':
    unittest.main()
