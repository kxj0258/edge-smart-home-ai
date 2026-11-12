"""Offline HA contract probes. Fake service acceptance is not real hardware.

No HA credentials, socket, installed HA runtime or state endpoint writes are
used. These tests assert backend-scoped target observations only.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from edge_smart_home.agent import AgentPlanValidator
from edge_smart_home.agent.rule_based import RuleBasedIntelligentPath
from edge_smart_home.app.orchestrator import AssistantOrchestrator
from edge_smart_home.app.cli import serialize_outcome, serialize_response
from edge_smart_home.app.pipeline import TextControlPipeline
from edge_smart_home.common import DeviceCommand, DeviceState, EventSource, PerceptionEvent
from edge_smart_home.common.schemas import CommandExecutionResult
from edge_smart_home.device import DeviceBackendError, HomeAssistantBackend
from edge_smart_home.device.home_assistant import UrllibHomeAssistantClient
from edge_smart_home.audio.feedback import completion_message
from edge_smart_home.audio.interfaces import AudioSource, StreamingRecognition
from edge_smart_home.audio.pipeline import VoiceControlPipeline


ROOT = Path(__file__).resolve().parents[1]
STAMP = '2026-11-13T01:00:00+00:00'


class FakeClient:
    def __init__(self, raw, *, post_response=None, post_error=None, get_error=None):
        self.raw = raw
        self.post_response = post_response
        self.post_error = post_error
        self.get_error = get_error
        self.calls = []

    def request(self, method, path, payload=None):
        self.calls.append((method, path, deepcopy(payload)))
        if method == 'POST':
            if self.post_error:
                raise self.post_error
            return deepcopy(self.post_response)
        if self.get_error:
            raise self.get_error
        return deepcopy(self.raw)


def raw_state(entity='light.living_room', state='off', **attributes):
    return {'entity_id': entity, 'state': state, 'attributes': attributes, 'last_updated': STAMP}


def configured_backend(raw, device='living_room_light', *, missing_fields=(), **client_args):
    entities = json.loads((ROOT / 'configs/home_assistant.json').read_text())['entities']
    config = deepcopy(entities[device])
    for name in missing_fields:
        config['state_fields'].pop(name, None)
    client = FakeClient(raw, **client_args)
    return HomeAssistantBackend({device: config}, client), client


class HomeAssistantReadbackBoundaryTests(unittest.TestCase):
    def assert_backend_error(self, backend, command, code, *, accepted=False):
        with self.assertRaises(DeviceBackendError) as caught:
            backend.execute(command)
        self.assertEqual(caught.exception.error_code, code)
        self.assertEqual(caught.exception.request_accepted, accepted)
        return caught.exception

    def command(self, action='turn_on', arguments=None, device='living_room_light'):
        return DeviceCommand(device, action, EventSource.TEXT, arguments or {})

    def test_post_accepted_empty_or_none_does_not_confirm_unchanged_get(self):
        for accepted in (None, [], {}):
            with self.subTest(response=accepted):
                backend, client = configured_backend(raw_state(), post_response=accepted)
                error = self.assert_backend_error(backend, self.command(), 'DEVICE_STATE_UNCONFIRMED', accepted=True)
                self.assertFalse(error.observed_state.attributes['power'])
                self.assertEqual([call[0] for call in client.calls], ['POST', 'GET'])

    def test_correct_target_is_ha_observation_never_physical_confirmation(self):
        backend, client = configured_backend(raw_state(state='on', brightness=255))
        state = backend.execute(self.command())
        self.assertEqual(state.backend_kind, 'home_assistant')
        self.assertEqual(state.completion_scope, 'ha_target_observed')
        self.assertEqual(state.updated_at, datetime.fromisoformat(STAMP))
        self.assertTrue(state.attributes['power'])
        self.assertEqual(len(client.calls), 2)

    def test_query_scope_is_observation_and_only_gets(self):
        backend, client = configured_backend(raw_state(state='on', brightness=255))
        state = backend.execute(self.command('query_state'))
        self.assertEqual(state.completion_scope, 'ha_state_observation')
        self.assertEqual(state.backend_kind, 'home_assistant')
        self.assertEqual([call[0] for call in client.calls], ['GET'])

    def test_unknown_and_unavailable_query_never_become_false_or_success(self):
        for unavailable in ('unknown', 'unavailable'):
            with self.subTest(state=unavailable):
                backend, client = configured_backend(raw_state(state=unavailable))
                observation = backend.get_state('living_room_light')
                self.assertFalse(observation.available)
                self.assertNotEqual(observation.attributes['power'], False)
                self.assertEqual(observation.attributes['ha_state'], unavailable)
                error = self.assert_backend_error(backend, self.command('query_state'), 'DEVICE_OFFLINE')
                self.assertFalse(error.observed_state.available)
                self.assertTrue(all(call[0] == 'GET' for call in client.calls))

    def test_accepted_mutation_with_unavailable_readback_is_offline(self):
        backend, _ = configured_backend(raw_state(state='unavailable', brightness=255))
        error = self.assert_backend_error(backend, self.command(), 'DEVICE_OFFLINE', accepted=True)
        self.assertFalse(error.observed_state.available)

    def test_missing_mapped_or_raw_target_field_stays_unconfirmed(self):
        cases = [(raw_state(state='on', brightness=128), ('brightness',)), (raw_state(state='on'), ())]
        for raw, absent_mapping in cases:
            with self.subTest(absent_mapping=absent_mapping):
                backend, _ = configured_backend(raw, missing_fields=absent_mapping)
                error = self.assert_backend_error(backend, self.command('set_brightness', {'brightness': 50}),
                                                  'DEVICE_STATE_UNCONFIRMED', accepted=True)
                self.assertIsNone(error.observed_state.attributes.get('brightness'))

    def test_wrong_numeric_target_not_accepted_as_success(self):
        backend, _ = configured_backend(raw_state(state='on', brightness=64))
        error = self.assert_backend_error(backend, self.command('set_brightness', {'brightness': 50}),
                                          'DEVICE_STATE_UNCONFIRMED', accepted=True)
        self.assertNotEqual(error.observed_state.attributes['brightness'], 50)

    def test_zero_brightness_normalizes_off_output_without_claiming_remembered_level(self):
        backend, _ = configured_backend(raw_state(state='off', brightness=200))
        state = backend.execute(self.command('set_brightness', {'brightness': 0}))
        self.assertEqual(state.attributes['brightness'], 0)
        self.assertFalse(state.attributes['power'])
        self.assertEqual(state.attributes['ha_state'], 'off')
        self.assertEqual(state.completion_scope, 'ha_target_observed')

    def test_wrong_entity_and_invalid_timestamp_are_response_errors(self):
        invalids = [raw_state(entity='light.some_other_light'),
                    {k: v for k, v in raw_state().items() if k != 'last_updated'},
                    {**raw_state(), 'last_updated': 'not a timestamp'},
                    {**raw_state(), 'last_updated': '2026-11-13T01:00:00'}]
        for raw in invalids:
            with self.subTest(raw=raw):
                backend, client = configured_backend(raw)
                with self.assertRaises(DeviceBackendError) as caught:
                    backend.get_state('living_room_light')
                self.assertEqual(caught.exception.error_code, 'DEVICE_RESPONSE_INVALID')
                self.assertFalse(caught.exception.request_accepted)
                self.assertEqual([call[0] for call in client.calls], ['GET'])

    def test_invalid_arguments_unknown_device_and_unmapped_action_have_zero_requests(self):
        commands = [self.command('set_brightness', {'brightness': True}),
                    self.command('set_brightness', {'brightness': 101}),
                    self.command('set_brightness', {'brightness': -1}),
                    self.command('set_brightness', {}),
                    self.command('turn_on', {'unexpected': 1}),
                    self.command('set_temperature', {'temperature': 31}),
                    self.command('set_hvac_mode', {'mode': 'fan_only'})]
        for command in commands:
            with self.subTest(action=command.action, args=command.arguments):
                backend, client = configured_backend(raw_state())
                self.assert_backend_error(backend, command, 'INVALID_ARGUMENTS')
                self.assertEqual(client.calls, [])
        backend, client = configured_backend(raw_state())
        self.assert_backend_error(backend, self.command(device='not_configured'), 'UNKNOWN_DEVICE')
        self.assert_backend_error(backend, self.command('set_channel', {'channel': 'CCTV-1'}), 'UNSUPPORTED_ACTION')
        self.assertEqual(client.calls, [])

    def test_post_failure_prevents_read_and_read_failure_preserves_acceptance(self):
        for code in ('DEVICE_TRANSPORT_ERROR', 'DEVICE_HTTP_ERROR'):
            for stage in ('post', 'get'):
                with self.subTest(stage=stage, code=code):
                    error = DeviceBackendError('injected HTTP/transport error', error_code=code)
                    kwargs = {'post_error' if stage == 'post' else 'get_error': error}
                    backend, client = configured_backend(raw_state(), **kwargs)
                    self.assert_backend_error(backend, self.command(), code,
                                              accepted=True if stage == 'get' else None)
                    self.assertEqual([call[0] for call in client.calls], ['POST'] if stage == 'post' else ['POST', 'GET'])

    def test_relative_and_restart_services_keep_no_target_unconfirmed(self):
        for device, action, raw in [
            ('living_room_tv', 'volume_up', raw_state('media_player.living_room_tv', 'on', volume_level=.25)),
            ('home_gateway', 'restart', raw_state('sensor.home_gateway', 'on', restart_count=1)),
        ]:
            with self.subTest(action=action):
                backend, client = configured_backend(raw, device)
                error = self.assert_backend_error(backend, self.command(action, device=device),
                                                  'DEVICE_STATE_UNCONFIRMED', accepted=True)
                self.assertIsNotNone(error.observed_state)
                self.assertEqual([call[0] for call in client.calls], ['POST', 'GET'])

    def test_cover_requires_percent_and_stable_state_not_merely_open(self):
        for raw in [raw_state('cover.living_room_curtain', 'opening', current_position=100),
                    raw_state('cover.living_room_curtain', 'open', current_position=50),
                    raw_state('cover.living_room_curtain', 'open')]:
            with self.subTest(raw=raw):
                backend, _ = configured_backend(raw, 'living_room_curtain')
                self.assert_backend_error(backend, self.command('open', device='living_room_curtain'),
                                          'DEVICE_STATE_UNCONFIRMED', accepted=True)
        backend, _ = configured_backend(raw_state('cover.living_room_curtain', 'open', current_position=50),
                                         'living_room_curtain')
        state = backend.execute(self.command('set_position', {'position': 50}, device='living_room_curtain'))
        self.assertEqual(state.attributes['position'], 'partial')
        self.assertEqual(state.attributes['ha_state'], 'open')
        self.assertEqual(state.completion_scope, 'ha_target_observed')

    def test_climate_setpoint_is_not_measured_room_temperature(self):
        raw = raw_state('climate.living_room', 'cool', temperature=26, current_temperature=19)
        backend, client = configured_backend(raw, 'living_room_air_conditioner')
        state = backend.execute(self.command('set_temperature', {'temperature': 26}, 'living_room_air_conditioner'))
        self.assertEqual(state.attributes['temperature'], 26)
        self.assertEqual(client.raw['attributes']['current_temperature'], 19)
        self.assertEqual(state.completion_scope, 'ha_target_observed')
        raw['attributes'].update(temperature=24, current_temperature=26)
        backend, _ = configured_backend(raw, 'living_room_air_conditioner')
        self.assert_backend_error(backend, self.command('set_temperature', {'temperature': 26}, 'living_room_air_conditioner'),
                                  'DEVICE_STATE_UNCONFIRMED', accepted=True)

    def test_climate_dry_and_fan_speed_mapping_remain_normalized_observations(self):
        backend, _ = configured_backend(raw_state('climate.living_room', 'dry', temperature=26), 'living_room_air_conditioner')
        state = backend.execute(self.command('set_hvac_mode', {'mode': 'dry'}, 'living_room_air_conditioner'))
        self.assertTrue(state.attributes['power'])
        self.assertEqual(state.attributes['hvac_mode'], 'dry')
        backend, _ = configured_backend(raw_state('fan.living_room', 'on', percentage=66.666667), 'living_room_fan')
        state = backend.execute(self.command('set_speed', {'speed': 2}, 'living_room_fan'))
        self.assertEqual(state.attributes['speed'], 2)

    def test_nonfinite_or_boolean_numeric_readback_is_invalid(self):
        for value in (float('nan'), float('inf'), -float('inf'), True, 10**400):
            with self.subTest(value=value):
                backend, _ = configured_backend(raw_state('climate.living_room', 'cool', temperature=value),
                                                 'living_room_air_conditioner')
                with self.assertRaises(DeviceBackendError) as caught:
                    backend.get_state('living_room_air_conditioner')
                self.assertEqual(caught.exception.error_code, 'DEVICE_RESPONSE_INVALID')


class UrllibResponseClassificationTests(unittest.TestCase):
    def client(self):
        return UrllibHomeAssistantClient('https://example.invalid', 'unused-fake-token')

    def test_transport_errors_are_typed_without_network(self):
        for error in (URLError('mock unavailable'), TimeoutError('mock timeout'), ConnectionResetError('mock reset')):
            with self.subTest(error=type(error).__name__):
                with patch('edge_smart_home.device.home_assistant.urlopen', side_effect=error):
                    with self.assertRaises(DeviceBackendError) as caught:
                        self.client().request('GET', '/api/states/light.fixture')
                self.assertEqual(caught.exception.error_code, 'DEVICE_TRANSPORT_ERROR')

    def test_http_and_invalid_json_or_utf8_have_distinct_codes(self):
        http = HTTPError('https://example.invalid', 503, 'mock HTTP', {}, io.BytesIO(b'mock failure'))
        with patch('edge_smart_home.device.home_assistant.urlopen', side_effect=http):
            with self.assertRaises(DeviceBackendError) as caught:
                self.client().request('POST', '/api/services/light/turn_on', {})
        self.assertEqual(caught.exception.error_code, 'DEVICE_HTTP_ERROR')
        for raw in (b'not json', b'\xff\xfe\x80'):
            with self.subTest(raw=raw):
                response = SimpleNamespace(read=lambda: raw)
                context = unittest.mock.MagicMock()
                context.__enter__.return_value = response
                with patch('edge_smart_home.device.home_assistant.urlopen', return_value=context):
                    with self.assertRaises(DeviceBackendError) as caught:
                        self.client().request('GET', '/api/states/light.fixture')
                self.assertEqual(caught.exception.error_code, 'DEVICE_RESPONSE_INVALID')


class HomeAssistantApplicationOutcomeTests(unittest.TestCase):
    def control(self):
        control = TextControlPipeline.from_config_dir(ROOT / 'configs')
        control.backend, _ = configured_backend(raw_state())
        return control

    def test_scene_records_unconfirmed_error_without_success(self):
        control = self.control()
        event = PerceptionEvent(EventSource.TEXT, '测试场景')
        command = DeviceCommand('living_room_light', 'turn_on', EventSource.TEXT)
        outcome = control.execute_resolved(event, 'test_scene', [command])
        self.assertFalse(outcome.succeeded)
        self.assertFalse(outcome.results[0].succeeded)
        self.assertIsNone(outcome.results[0].state)
        self.assertEqual(outcome.results[0].error_code, 'DEVICE_STATE_UNCONFIRMED')
        self.assertTrue(outcome.results[0].request_accepted)
        self.assertFalse(outcome.results[0].observed_state.attributes['power'])
        serialized = serialize_outcome(outcome)['results'][0]
        self.assertTrue(serialized['request_accepted'])
        self.assertEqual(serialized['observed_state']['completion_scope'], 'ha_state_observation')
        self.assertFalse(serialized['observed_state']['attributes']['power'])

    def test_orchestrator_does_not_call_accepted_service_executed(self):
        devices = json.loads((ROOT / 'configs/devices.json').read_text())
        app = AssistantOrchestrator(self.control(), RuleBasedIntelligentPath([]), AgentPlanValidator(devices))
        response = app.handle_text('打开客厅灯')
        self.assertNotEqual(response.status, 'executed')
        self.assertEqual(response.error_code, 'DEVICE_STATE_UNCONFIRMED')
        payload = serialize_response(response)
        self.assertTrue(payload['request_accepted'])
        self.assertNotIn('result', payload)
        self.assertEqual(payload['observed_state']['backend_kind'], 'home_assistant')
        self.assertEqual(payload['observed_state']['completion_scope'], 'ha_state_observation')
        self.assertFalse(payload['observed_state']['attributes']['power'])
        self.assertNotIn('已完成', completion_message(response))

    def test_voice_preserves_accepted_but_unconfirmed_ha_observation(self):
        devices = json.loads((ROOT / 'configs/devices.json').read_text())
        app = AssistantOrchestrator(self.control(), RuleBasedIntelligentPath([]), AgentPlanValidator(devices))
        diagnostics = Mock()
        diagnostics.session_id = 'mock-w15-ha-boundary'
        pipeline = VoiceControlPipeline(Mock(spec=AudioSource), Mock(), app, diagnostics=diagnostics)
        result = pipeline.process_recognition(StreamingRecognition('打开客厅灯', 1.0, .1))
        self.assertNotEqual(result.status, 'executed')
        self.assertEqual(result.error_code, 'DEVICE_STATE_UNCONFIRMED')
        self.assertTrue(result.request_accepted)
        self.assertIsNone(result.state)
        self.assertFalse(result.observed_state.attributes['power'])
        self.assertEqual(result.observed_state.completion_scope, 'ha_state_observation')
        detail = diagnostics.record_result.call_args.args[2]
        self.assertTrue(detail['request_accepted'])
        self.assertEqual(detail['error_code'], 'DEVICE_STATE_UNCONFIRMED')
        self.assertFalse(detail['observed_state']['attributes']['power'])

    def test_feedback_distinguishes_ha_target_from_query_and_physical_completion(self):
        devices = json.loads((ROOT / 'configs/devices.json').read_text())
        for text, expected in [('打开客厅灯', '回读到目标状态'), ('查询客厅灯状态', '读取设备状态')]:
            with self.subTest(text=text):
                control = self.control()
                control.backend, _ = configured_backend(raw_state(state='on', brightness=255))
                app = AssistantOrchestrator(control, RuleBasedIntelligentPath([]), AgentPlanValidator(devices))
                response = app.handle_text(text)
                self.assertEqual(response.status, 'executed')
                spoken = completion_message(response)
                self.assertIn(expected, spoken)
                self.assertIn('平台', spoken)
                self.assertNotIn('已完成', spoken)

    def test_unavailable_state_is_not_successful_execution_result(self):
        command = DeviceCommand('living_room_light', 'turn_on', EventSource.TEXT)
        observation = DeviceState('living_room_light', False, {'power': False}, updated_at=datetime.now(timezone.utc))
        self.assertFalse(CommandExecutionResult(command, observation).succeeded)


if __name__ == '__main__':
    unittest.main()
