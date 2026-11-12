from __future__ import annotations

from collections import deque
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from edge_smart_home.app.orchestrator import AssistantOrchestrator
from edge_smart_home.app.pipeline import TextControlPipeline
from edge_smart_home.agent import AgentPlanValidator
from edge_smart_home.agent.rule_based import RuleBasedIntelligentPath
from edge_smart_home.audio.interfaces import AudioChunk, AudioSource, StreamingRecognition
from edge_smart_home.audio.pipeline import VoiceControlPipeline
from edge_smart_home.audio.sherpa_onnx_adapters import SherpaOnnxStreamingAsr, SherpaOnnxVad
from edge_smart_home.audio.validation import AudioInputError, validate_audio_samples, validate_sample_rate


ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / 'configs/audio.json').read_text())


class FakeStream:
    def __init__(self):
        self.calls = []
        self.finished = False

    def accept_waveform(self, rate, samples):
        self.calls.append((rate, np.array(samples, copy=True)))

    def input_finished(self):
        self.finished = True


class FakeRecognizer:
    def __init__(self):
        self.streams = []
        self.endpoint = False
        self.resets = []
        self.text = ''

    def create_stream(self):
        stream = FakeStream()
        self.streams.append(stream)
        return stream

    def is_ready(self, stream):
        return False

    def decode_stream(self, stream):
        raise AssertionError('mock never has a ready frame')

    def is_endpoint(self, stream):
        return self.endpoint

    def get_result_all(self, stream):
        return SimpleNamespace(text=self.text)

    def reset(self, stream):
        self.resets.append(stream)


class FakeVad:
    def __init__(self):
        self.accepted = []
        self.segments = deque()
        self.active = []
        self.flush_count = 0

    def accept_waveform(self, samples):
        self.accepted.append(np.array(samples, copy=True))

    def empty(self):
        return not self.segments

    @property
    def front(self):
        return SimpleNamespace(samples=self.segments[0])

    def pop(self):
        self.segments.popleft()

    def flush(self):
        self.flush_count += 1
        if self.active:
            self.segments.append(self.active)
            self.active = []


class AudioErrorAssertions:
    def assert_audio_error(self, code, function, *args):
        with self.assertRaises(AudioInputError) as caught:
            function(*args)
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(caught.exception.stage, 'audio_input')
        self.assertIsInstance(caught.exception, ValueError)


class AudioValidationTests(AudioErrorAssertions, unittest.TestCase):
    def test_positive_integer_rate_and_native_representation_bound(self):
        for rate in (8000, 16000, 44100, 48000, np.int64(16000)):
            self.assertEqual(validate_sample_rate(rate), int(rate))
        for rate in (0, -1, True, False, 16000.0, float('nan'), '16000', None, 2**31):
            with self.subTest(rate=rate):
                self.assert_audio_error('AUDIO_SAMPLE_RATE_INVALID', validate_sample_rate, rate)

    def test_float_input_is_normalized_without_clipping_or_downmix(self):
        source = np.array([-1.0, 0.0, 0.5, 0.25, 1.0, 0.0], dtype=np.float64)[::2]
        result = validate_audio_samples(source, 48000)
        self.assertEqual(result.dtype, np.float32)
        self.assertEqual(result.ndim, 1)
        self.assertTrue(result.flags.c_contiguous)
        np.testing.assert_array_equal(result, [-1.0, 0.5, 1.0])

    def test_stereo_scalar_and_ragged_inputs_are_rejected(self):
        self.assert_audio_error('AUDIO_SHAPE_INVALID', validate_audio_samples, np.zeros((16, 2)), 16000)
        self.assert_audio_error('AUDIO_SHAPE_INVALID', validate_audio_samples, 0.0, 16000)
        self.assert_audio_error('AUDIO_SAMPLE_TYPE_INVALID', validate_audio_samples, [[0.0], [0.0, 0.1]], 16000)

    def test_integer_pcm_requires_explicit_normalization(self):
        for value in (np.array([0, 32767], dtype=np.int16), [0, 1], [True, False], ['0.0']):
            self.assert_audio_error('AUDIO_SAMPLE_TYPE_INVALID', validate_audio_samples, value, 16000)
        pcm = np.array([-32768, 0, 32767], dtype='<i2')
        result = validate_audio_samples(pcm.astype(np.float32) / 32768.0, 16000)
        np.testing.assert_array_equal(result, [-1.0, 0.0, 32767 / 32768.0])

    def test_empty_nonfinite_and_out_of_range_are_not_silently_repaired(self):
        self.assert_audio_error('AUDIO_EMPTY', validate_audio_samples, [], 16000)
        for value in (float('nan'), float('inf'), -float('inf')):
            self.assert_audio_error('AUDIO_NONFINITE', validate_audio_samples, [value], 16000)
        # Verify the boundary before float32 conversion can round this to 1.0.
        for value in (1.000000001, -1.000000001, 32767.0):
            self.assert_audio_error('AUDIO_RANGE_INVALID', validate_audio_samples, [value], 16000)

    def test_audio_chunk_rejects_bad_rate_before_native_adapter(self):
        for rate in (False, 0, '16000', 16000.5):
            self.assert_audio_error('AUDIO_SAMPLE_RATE_INVALID', AudioChunk, [0.0], rate)


class AsrBoundaryTests(AudioErrorAssertions, unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        paths = [Path(self.temp.name) / name for name in ('tokens.txt', 'encoder.onnx', 'decoder.onnx', 'joiner.onnx')]
        for path in paths:
            path.write_bytes(b'fixture, not a model')
        self.paths = paths
        self.engine = FakeRecognizer()
        self.factory = Mock(return_value=self.engine)
        runtime = SimpleNamespace(OnlineRecognizer=SimpleNamespace(from_transducer=self.factory))
        self.runtime_patch = patch('edge_smart_home.audio.sherpa_onnx_adapters._sherpa_onnx', return_value=runtime)
        self.runtime_patch.start()
        self.addCleanup(self.runtime_patch.stop)
        self.asr = SherpaOnnxStreamingAsr(*paths, 16000, CONFIG['asr'])

    def test_invalid_waveform_is_rejected_before_native_accept(self):
        for value in (np.zeros((160, 2)), np.array([12], dtype=np.int16), [float('nan')], [1.1], []):
            with self.assertRaises(AudioInputError):
                self.asr.accept(AudioChunk(value, 16000))
        self.assertEqual(self.engine.streams[0].calls, [])

    def test_accept_preserves_real_input_rate_for_native_resampling(self):
        self.asr.accept(AudioChunk(np.zeros(4800, dtype=np.float32), 48000))
        rate, samples = self.engine.streams[0].calls[0]
        self.assertEqual(rate, 48000)
        self.assertEqual(len(samples), 4800)
        self.assertAlmostEqual(self.asr._audio_seconds, 0.1)

    def test_rate_change_rejected_even_after_endpoint_reset(self):
        for first, second in ((48000, 16000), (16000, 48000)):
            with self.subTest(first=first):
                asr = SherpaOnnxStreamingAsr(*self.paths, 16000, CONFIG['asr'])
                self.engine.endpoint = True
                asr.accept(AudioChunk([0.0], first))
                self.assert_audio_error('AUDIO_SAMPLE_RATE_CHANGED', asr.accept, AudioChunk([0.0], second))
                self.assertEqual(len(self.engine.streams[-1].calls), 1)

    def test_reset_current_does_not_change_rate_of_existing_native_stream(self):
        self.asr.accept(AudioChunk([0.0], 48000))
        self.asr.reset_current()
        self.assert_audio_error('AUDIO_SAMPLE_RATE_CHANGED', self.asr.accept, AudioChunk([0.0], 16000))

    def test_finish_padding_uses_input_rate_and_finishes_once(self):
        self.asr.accept(AudioChunk(np.zeros(4800, dtype=np.float32), 48000))
        self.engine.text = '测试'
        result = self.asr.finish()
        stream = self.engine.streams[0]
        self.assertEqual(result[0].text, '测试')
        self.assertAlmostEqual(result[0].audio_seconds, 0.1)
        self.assertTrue(stream.finished)
        self.assertEqual(stream.calls[-1][0], 48000)
        self.assertEqual(len(stream.calls[-1][1]), 24000)
        self.assertEqual(self.asr.finish(), ())
        with self.assertRaises(RuntimeError):
            self.asr.accept(AudioChunk([0.0], 48000))

    def test_finish_without_input_has_no_padding_or_hallucinated_result(self):
        self.engine.text = 'must not be returned without source audio'
        self.assertEqual(self.asr.finish(), ())
        self.assertEqual(self.engine.streams[0].calls, [])

    def test_independent_waveforms_use_new_streams_and_can_change_rate(self):
        self.asr.transcribe_waveform(np.zeros(1600), 16000)
        self.asr.transcribe_waveform(np.zeros(4800), 48000)
        self.assertEqual(len(self.engine.streams), 3)
        self.assertEqual(self.engine.streams[1].calls[0][0], 16000)
        self.assertEqual(self.engine.streams[2].calls[0][0], 48000)
        self.assertTrue(self.engine.streams[1].finished)
        self.assertTrue(self.engine.streams[2].finished)
        self.assertEqual(self.engine.streams[0].calls, [])

    def test_offline_invalid_waveform_or_chunk_duration_creates_no_stream(self):
        self.assert_audio_error('AUDIO_SAMPLE_TYPE_INVALID', self.asr.transcribe_waveform, np.zeros(160, dtype=np.int16), 16000)
        for duration in (0, -1, True, 100.5):
            self.assert_audio_error('AUDIO_CHUNK_DURATION_INVALID', self.asr.transcribe_waveform, [0.0], 16000, duration)
        self.assertEqual(len(self.engine.streams), 1)


class VadBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.model = Path(self.temp.name) / 'silero_vad.onnx'
        self.model.write_bytes(b'fixture, not a model')
        self.engine = FakeVad()
        config_factory = lambda: SimpleNamespace(silero_vad=SimpleNamespace())
        self.factory = Mock(return_value=self.engine)
        runtime = SimpleNamespace(VadModelConfig=config_factory, VoiceActivityDetector=self.factory)
        self.patcher = patch('edge_smart_home.audio.sherpa_onnx_adapters._sherpa_onnx', return_value=runtime)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.vad = SherpaOnnxVad(self.model, 16000, CONFIG['vad'])

    def test_vad_requires_current_exports_16k_feature_rate(self):
        for rate in (8000, 48000):
            with self.assertRaises(AudioInputError) as caught:
                SherpaOnnxVad(self.model, rate, CONFIG['vad'])
            self.assertEqual(caught.exception.code, 'AUDIO_SAMPLE_RATE_MISMATCH')
        self.assertEqual(self.factory.call_count, 1)

    def test_vad_rejects_rate_mismatch_and_bad_pcm_before_native(self):
        for chunk in (AudioChunk([0.0], 48000), AudioChunk(np.array([32767], dtype=np.int16), 16000)):
            with self.assertRaises(AudioInputError):
                self.vad.accept(chunk)
        self.assertEqual(self.engine.accepted, [])

    def test_vad_drains_all_ready_segments(self):
        self.engine.segments.extend([[0.1, 0.2], [0.3]])
        result = self.vad.accept(AudioChunk([0.0], 16000))
        self.assertEqual(len(result), 2)
        self.assertTrue(all(segment.sample_rate == 16000 for segment in result))
        self.assertTrue(self.engine.empty())

    def test_vad_finish_flushes_active_segment_once_and_disallows_new_input(self):
        self.engine.active = [0.1, 0.2]
        result = self.vad.finish()
        self.assertEqual(result[0].samples, [0.1, 0.2])
        self.assertEqual(self.vad.finish(), ())
        self.assertEqual(self.engine.flush_count, 1)
        with self.assertRaises(RuntimeError):
            self.vad.accept(AudioChunk([0.0], 16000))


class VoiceTextBoundaryTests(unittest.TestCase):
    def pipeline(self, diagnostics=None):
        # Explicitly isolated provider; these are routing Mock probes, not ASR.
        devices = json.loads((ROOT / 'configs/devices.json').read_text())
        orchestrator = AssistantOrchestrator(
            TextControlPipeline.from_config_dir(ROOT / 'configs'),
            RuleBasedIntelligentPath([]), AgentPlanValidator(devices, 8),
        )
        return VoiceControlPipeline(Mock(spec=AudioSource), Mock(), orchestrator, diagnostics)

    def test_missing_and_out_of_range_transcripts_keep_w13_machine_codes(self):
        pipeline = self.pipeline()
        for text, expected in [('把客厅灯亮度设置一下', 'REQUEST_ARGUMENT_MISSING'), ('把客厅空调温度设为31度', 'REQUEST_ARGUMENT_INVALID')]:
            with self.subTest(text=text):
                result = pipeline.process_recognition(StreamingRecognition(text, 1.0, 0.1))
                self.assertEqual(result.error_code, expected)
                self.assertNotEqual(result.status, 'executed')
                self.assertIsNone(result.state)
                self.assertEqual(result.llm_calls, 0)

    def test_executed_transcript_has_no_error_code(self):
        result = self.pipeline().process_recognition(StreamingRecognition('打开客厅灯', 1.0, 0.1))
        self.assertEqual(result.status, 'executed')
        self.assertIsNone(result.error_code)
        self.assertTrue(result.state.attributes['power'])

    def test_one_character_transcript_is_input_error_and_not_executed(self):
        result = self.pipeline().process_recognition(StreamingRecognition('灯', 1.0, 0.1))
        self.assertEqual(result.status, 'processing_error')
        self.assertEqual(result.error_code, 'REQUEST_INPUT_INVALID')
        self.assertIsNone(result.state)

    def test_diagnostics_retain_text_error_code(self):
        recorder = Mock()
        recorder.session_id = 'mock-w14'
        result = self.pipeline(recorder).process_recognition(StreamingRecognition('把客厅灯亮度设置一下', 1.0, 0.1))
        self.assertEqual(recorder.record_result.call_args.args[2]['error_code'], result.error_code)


if __name__ == '__main__':
    unittest.main()
