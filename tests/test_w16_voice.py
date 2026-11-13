from __future__ import annotations

import json
import contextlib
import io
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from edge_smart_home.agent import AgentPlanValidator
from edge_smart_home.app.orchestrator import AssistantOrchestrator
from edge_smart_home.app.pipeline import TextControlPipeline
from edge_smart_home.audio.feedback import AssistantFeedback
from edge_smart_home.audio.interfaces import AudioSource, StreamingRecognition, StreamingSpeechRecognizer
from edge_smart_home.audio.pipeline import VoiceControlPipeline, VoiceControlResult
from edge_smart_home.audio.sherpa_onnx_adapters import VoiceDependencyError
from edge_smart_home.audio.validation import AudioInputError
from edge_smart_home.common import CommandExecutionResult, DeviceCommand, DeviceState, EventSource, SceneExecutionResult
from edge_smart_home.device import MockDeviceBackend, SafetyPolicy
from edge_smart_home.router import CommandDeduplicator, FastPathRouter, SceneRouter
from scripts.run_voice_demo import _result_payload, _initialization_error_result, main as voice_demo_main


ROOT = Path(__file__).resolve().parents[1]


class RecordingMock(MockDeviceBackend):
    def __init__(self, config):
        self.calls = []
        super().__init__(config)

    def execute(self, command):
        self.calls.append(command)
        return super().execute(command)


class CaptureSpeaker:
    def __init__(self, fail=False):
        self.messages = []
        self.fail = fail

    def speak(self, text):
        self.messages.append(text)
        if self.fail:
            raise OSError('injected feedback unavailable')


def pipeline(*, speaker=None, diagnostics=None):
    load = lambda name: json.loads((ROOT / 'configs' / (name + '.json')).read_text())
    devices, intents, routing, scenes, safety = (load(k) for k in ('devices', 'intents', 'routing', 'scenes', 'safety'))
    backend = RecordingMock(devices)
    control = TextControlPipeline(FastPathRouter(devices, intents, routing.get('default_devices', {})),
                                  SceneRouter(scenes), backend, SafetyPolicy(safety.get('action_risks', {})),
                                  CommandDeduplicator(0))
    orchestrator = AssistantOrchestrator(control, intelligent_path=None, plan_validator=AgentPlanValidator(devices))
    return VoiceControlPipeline(Mock(spec=AudioSource), Mock(spec=StreamingSpeechRecognizer), orchestrator,
                                diagnostics=diagnostics,
                                feedback=AssistantFeedback(speaker) if speaker is not None else None), backend


class W16VoiceBoundaryTests(unittest.TestCase):
    def recognize(self, voice, text, **kwargs):
        return voice.process_recognition(StreamingRecognition(text, 2.0, 0.1), **kwargs)

    def test_legacy_background_empty_remains_silent_and_does_not_execute(self):
        speaker = CaptureSpeaker()
        voice, backend = pipeline(speaker=speaker)
        self.assertIsNone(self.recognize(voice, '  '))
        self.assertEqual(backend.calls, [])
        self.assertEqual(speaker.messages, [])

    def test_explicit_empty_has_layered_result_and_text_fallback(self):
        speaker = CaptureSpeaker()
        voice, backend = pipeline(speaker=speaker)
        result = self.recognize(voice, '', report_empty=True)
        self.assertEqual((result.status, result.error_code, result.error_stage),
                         ('empty_transcript', 'ASR_EMPTY_TRANSCRIPT', 'asr_output'))
        self.assertIsNone(result.state)
        self.assertIn('文本', result.text_fallback)
        self.assertEqual(result.audio_seconds, 2.0)
        self.assertEqual(backend.calls, [])
        self.assertTrue(any('文本' in message for message in speaker.messages))

    def test_audio_input_code_is_preserved_without_routing_reference_text(self):
        voice, backend = pipeline()
        result = voice.process_audio_error(AudioInputError('bad mono samples', 'AUDIO_SHAPE_INVALID'), stage='audio_input')
        self.assertEqual((result.status, result.error_code, result.error_stage),
                         ('asr_error', 'AUDIO_SHAPE_INVALID', 'audio_input'))
        self.assertEqual(result.text, '')
        self.assertIsNone(result.state)
        self.assertEqual(backend.calls, [])

    def test_dependency_and_decode_failures_are_distinct_with_fallback(self):
        voice, backend = pipeline()
        dependency = voice.process_audio_error(VoiceDependencyError('missing model'), stage='asr_dependency')
        decode = voice.process_audio_error(RuntimeError('native decode failed'))
        self.assertEqual(dependency.error_code, 'ASR_DEPENDENCY_UNAVAILABLE')
        self.assertEqual(decode.error_code, 'ASR_DECODE_FAILED')
        self.assertEqual(decode.error_stage, 'asr_decode')
        self.assertIn('文本', decode.text_fallback)
        self.assertEqual(backend.calls, [])

    def test_unknown_audio_failure_stage_is_not_guessed(self):
        voice, backend = pipeline()
        with self.assertRaises(ValueError):
            voice.process_audio_error(RuntimeError('unknown layer'), stage='physical_device')
        self.assertEqual(backend.calls, [])

    def test_feedback_failure_preserves_completed_state_and_executes_once(self):
        speaker = CaptureSpeaker(fail=True)
        voice, backend = pipeline(speaker=speaker)
        result = self.recognize(voice, '打开客厅灯')
        self.assertEqual(result.status, 'executed')
        self.assertIsNone(result.error_code)
        self.assertIsNone(result.error)
        self.assertTrue(result.state.attributes['power'])
        self.assertEqual(result.state, backend.get_state('living_room_light'))
        self.assertEqual(result.state.backend_kind, 'mock')
        self.assertEqual(len(backend.calls), 1)
        self.assertIn('OSError', result.feedback_error)
        self.assertIn('勿重复', result.text_fallback)

    def test_diagnostic_failure_preserves_execution_and_does_not_retry(self):
        recorder = Mock()
        recorder.session_id = 'w16-mock'
        recorder.record_result.side_effect = OSError('injected diagnostic disk failure')
        voice, backend = pipeline(diagnostics=recorder)
        result = self.recognize(voice, '打开客厅灯')
        self.assertEqual(result.status, 'executed')
        self.assertTrue(result.state.attributes['power'])
        self.assertEqual(len(backend.calls), 1)
        self.assertIn('OSError', result.diagnostic_error)
        self.assertIsNone(result.error_code)

    def test_completion_formatting_failure_cannot_erase_already_executed_state(self):
        voice, backend = pipeline(speaker=CaptureSpeaker())
        with patch('edge_smart_home.audio.pipeline.completion_message', side_effect=RuntimeError('completion formatting failed')):
            result = self.recognize(voice, '打开客厅灯')
        self.assertEqual(result.status, 'executed')
        self.assertTrue(result.state.attributes['power'])
        self.assertEqual(result.state, backend.get_state('living_room_light'))
        self.assertIsNone(result.error_code)
        self.assertIsNone(result.error)
        self.assertIn('completion formatting failed', result.feedback_error)
        self.assertIn('勿重复', result.text_fallback)
        self.assertEqual(len(backend.calls), 1)

    def test_diagnostic_detail_and_trace_build_failures_cannot_erase_execution(self):
        for target in ('edge_smart_home.audio.pipeline._diagnostic_detail',
                       'edge_smart_home.common.observability.Trace.snapshot'):
            with self.subTest(target=target):
                recorder = Mock()
                recorder.session_id = 'w16-mock'
                voice, backend = pipeline(diagnostics=recorder)
                with patch(target, side_effect=RuntimeError('diagnostic construction failed')):
                    result = self.recognize(voice, '打开客厅灯')
                self.assertEqual(result.status, 'executed')
                self.assertTrue(result.state.attributes['power'])
                self.assertIsNone(result.error_code)
                self.assertIsNone(result.error)
                self.assertIn('diagnostic construction failed', result.diagnostic_error)
                self.assertEqual(len(backend.calls), 1)
                recorder.record_result.assert_not_called()

    def test_both_observers_failing_do_not_turn_completion_into_processing_error(self):
        recorder = Mock()
        recorder.session_id = 'w16-mock'
        recorder.record_result.side_effect = OSError('disk error')
        voice, backend = pipeline(speaker=CaptureSpeaker(fail=True), diagnostics=recorder)
        result = self.recognize(voice, '打开客厅灯')
        self.assertEqual(result.status, 'executed')
        self.assertIsNotNone(result.feedback_error)
        self.assertIsNotNone(result.diagnostic_error)
        self.assertEqual(len(backend.calls), 1)

    def test_failed_request_keeps_original_code_when_feedback_fails(self):
        voice, backend = pipeline(speaker=CaptureSpeaker(fail=True))
        result = self.recognize(voice, '把客厅空调温度设为31度')
        self.assertEqual((result.status, result.error_code, result.error_stage),
                         ('request_rejected', 'REQUEST_ARGUMENT_INVALID', 'request'))
        self.assertIsNone(result.state)
        self.assertIsNotNone(result.feedback_error)
        self.assertEqual(backend.calls, [])

    def test_explicit_empty_with_observer_failures_is_still_an_empty_result(self):
        recorder = Mock()
        recorder.session_id = 'w16-mock'
        recorder.record_result.side_effect = OSError('disk error')
        voice, backend = pipeline(speaker=CaptureSpeaker(fail=True), diagnostics=recorder)
        result = self.recognize(voice, '', report_empty=True)
        self.assertEqual(result.error_code, 'ASR_EMPTY_TRANSCRIPT')
        self.assertIsNotNone(result.feedback_error)
        self.assertIsNotNone(result.diagnostic_error)
        self.assertEqual(backend.calls, [])

    def test_audio_error_feedback_failure_does_not_mask_dependency_error(self):
        voice, backend = pipeline(speaker=CaptureSpeaker(fail=True))
        result = voice.process_audio_error(VoiceDependencyError('model unavailable'), stage='asr_dependency')
        self.assertEqual(result.error_code, 'ASR_DEPENDENCY_UNAVAILABLE')
        self.assertIsNotNone(result.feedback_error)
        self.assertEqual(backend.calls, [])

    def test_one_character_processing_error_does_not_retry_after_feedback_failure(self):
        voice, backend = pipeline(speaker=CaptureSpeaker(fail=True))
        result = self.recognize(voice, '灯')
        self.assertEqual((result.status, result.error_code), ('processing_error', 'REQUEST_INPUT_INVALID'))
        self.assertIsNotNone(result.feedback_error)
        self.assertEqual(backend.calls, [])

    def test_diagnostics_include_actual_mock_readback_and_observer_error(self):
        recorder = Mock()
        recorder.session_id = 'w16-mock'
        voice, backend = pipeline(speaker=CaptureSpeaker(fail=True), diagnostics=recorder)
        result = self.recognize(voice, '打开客厅灯')
        detail = recorder.record_result.call_args.args[2]
        self.assertEqual(detail['backend_kind'], 'mock')
        self.assertEqual(detail['completion_scope'], 'mock_state_snapshot')
        self.assertEqual(detail['attributes'], dict(backend.get_state('living_room_light').attributes))
        self.assertEqual(detail['feedback_error'], result.feedback_error)

    def test_scene_partial_failure_has_device_layer_and_never_all_done_feedback(self):
        speaker = CaptureSpeaker()
        voice, backend = pipeline(speaker=speaker)
        backend.set_available('living_room_tv', False)
        result = self.recognize(voice, '开启观影模式')
        self.assertEqual((result.status, result.error_code, result.error_stage),
                         ('partial_failure', 'EXECUTION_PARTIAL_FAILURE', 'device'))
        self.assertFalse(result.state.succeeded)
        self.assertIn('先核对设备状态', result.text_fallback)
        self.assertTrue(any('部分' in message for message in speaker.messages))
        self.assertFalse(any(message.startswith('已完成') for message in speaker.messages))

    def test_cli_preserves_authoritative_status_even_if_state_is_present(self):
        state = DeviceState('living_room_light', False, {'power': False}, backend_kind='mock', completion_scope='mock_state_snapshot')
        payload = _result_payload(VoiceControlResult(text='test', state=state, status='device_error', error_code='DEVICE_OFFLINE'))
        self.assertEqual(payload['status'], 'device_error')
        self.assertEqual(payload['error_code'], 'DEVICE_OFFLINE')
        self.assertFalse(payload['available'])
        self.assertEqual(payload['backend_kind'], 'mock')

    def test_cli_keeps_scene_machine_codes_acceptance_and_observed_state(self):
        state = DeviceState('living_room_light', True, {'power': False}, backend_kind='home_assistant', completion_scope='ha_state_observation')
        command = DeviceCommand('living_room_light', 'turn_on', EventSource.VOICE)
        scene = SceneExecutionResult('mock-scene', (CommandExecutionResult(command, error='unconfirmed', error_code='DEVICE_STATE_UNCONFIRMED',
                                                                        request_accepted=True, observed_state=state),))
        payload = _result_payload(VoiceControlResult(text='test', state=scene, status='partial_failure',
                                                    error_code='EXECUTION_PARTIAL_FAILURE', feedback_error='OSError'))
        self.assertEqual(payload['status'], 'partial_failure')
        self.assertTrue(payload['results'][0]['request_accepted'])
        self.assertEqual(payload['results'][0]['observed_state']['completion_scope'], 'ha_state_observation')
        self.assertEqual(payload['results'][0]['error_code'], 'DEVICE_STATE_UNCONFIRMED')
        self.assertEqual(payload['feedback_error'], 'OSError')

    def test_unroutable_feedback_has_text_suggestion_without_implicit_execution(self):
        speaker = CaptureSpeaker()
        # A standalone feedback observation is not a device execution.
        AssistantFeedback(speaker).response(SimpleNamespace(status='not_routed'))
        self.assertIn('文本', speaker.messages[-1])
        voice, backend = pipeline()
        result = self.recognize(voice, '火星上的紫色木头')
        self.assertNotEqual(result.status, 'executed')
        self.assertIn('文本', result.text_fallback)
        self.assertEqual(backend.calls, [])

    def test_initialization_failure_closes_diagnostics_before_any_mic_or_orchestrator(self):
        recorder = Mock()
        recorder.session_dir = 'mock-diagnostic-path'
        for diagnostic_failure in (False, True):
            with self.subTest(diagnostic_failure=diagnostic_failure):
                recorder.reset_mock(side_effect=True)
                if diagnostic_failure:
                    recorder.record_result.side_effect = OSError('injected diagnostic failure')
                stderr, stdout = io.StringIO(), io.StringIO()
                argv = ['run_voice_demo.py', '--tokens', 'missing-tokens', '--encoder', 'missing-encoder',
                        '--decoder', 'missing-decoder', '--joiner', 'missing-joiner', '--diagnostics-dir', 'unused']
                with (patch('sys.argv', argv),
                      patch('scripts.run_voice_demo.AudioDiagnosticRecorder', return_value=recorder),
                      patch('scripts.run_voice_demo.SherpaOnnxStreamingAsr', side_effect=VoiceDependencyError('model missing')),
                      patch('scripts.run_voice_demo.SoundDeviceMicrophone') as microphone,
                      patch('scripts.run_voice_demo.AssistantOrchestrator.from_config_dir') as orchestrator,
                      patch('scripts.run_voice_demo._preload_model') as preload,
                      patch('scripts.run_voice_demo.create_feedback') as feedback,
                      contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(stdout)):
                    with self.assertRaises(SystemExit) as caught:
                        voice_demo_main()
                self.assertEqual(caught.exception.code, 2)
                payload = json.loads(stderr.getvalue())
                self.assertEqual(payload['error_code'], 'ASR_DEPENDENCY_UNAVAILABLE')
                self.assertEqual(payload['error_stage'], 'asr_dependency')
                self.assertIn('--backend mock text --no-model', payload['text_fallback'])
                self.assertFalse(payload['microphone_started'])
                self.assertEqual(payload['status'], 'asr_error')
                self.assertEqual(bool(payload['diagnostic_error']), diagnostic_failure)
                recorder.close.assert_called_once()
                microphone.assert_not_called()
                orchestrator.assert_not_called()
                preload.assert_not_called()
                feedback.assert_not_called()

    def test_initialization_types_are_classified_without_guessing_all_errors_as_dependencies(self):
        invalid = _initialization_error_result(AudioInputError('bad padding', 'AUDIO_PADDING_INVALID'))
        unknown = _initialization_error_result(RuntimeError('initialization failed'))
        self.assertEqual((invalid.error_stage, invalid.error_code), ('audio_input', 'AUDIO_PADDING_INVALID'))
        self.assertEqual((unknown.error_stage, unknown.error_code), ('asr_initialization', 'ASR_INITIALIZATION_FAILED'))


if __name__ == '__main__':
    unittest.main()
