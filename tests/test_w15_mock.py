from copy import deepcopy
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from edge_smart_home.common import DeviceCommand, EventSource
from edge_smart_home.device import DeviceBackendError, MockDeviceBackend
from edge_smart_home.device.validation import KNOWN_ACTIONS, validate_action_arguments


ROOT = Path(__file__).resolve().parents[1]
DEVICES = json.loads((ROOT / "configs/devices.json").read_text())
T0 = datetime(2026, 11, 13, tzinfo=timezone.utc)
T1 = T0 + timedelta(seconds=1)


class W15MockTests(unittest.TestCase):
    def setUp(self):
        with patch("edge_smart_home.device.mock.utc_now", return_value=T0):
            self.backend = MockDeviceBackend(DEVICES)

    def command(self, device_id, action, arguments=None):
        return DeviceCommand(device_id, action, EventSource.TEXT, arguments or {})

    def snapshot(self):
        return [(s.device_id, s.available, dict(s.attributes), s.updated_at)
                for s in self.backend.list_states()]

    def assert_rejected_unchanged(self, code, command):
        before = self.snapshot()
        with self.assertRaises(DeviceBackendError) as caught:
            self.backend.execute(command)
        self.assertEqual(caught.exception.error_code, code)
        self.assertEqual(self.snapshot(), before)
        self.assertIsInstance(caught.exception, RuntimeError)
        return caught.exception

    def test_existing_catalogue_has_known_capabilities_and_mock_snapshot_scope(self):
        self.assertEqual(len(DEVICES), 25)
        states = {s.device_id: s for s in self.backend.list_states()}
        self.assertEqual(set(states), set(DEVICES))
        for device_id, config in DEVICES.items():
            with self.subTest(device_id=device_id):
                self.assertTrue(set(config["capabilities"]) <= KNOWN_ACTIONS)
                state = states[device_id]
                self.assertEqual(dict(state.attributes), config["initial_state"])
                self.assertTrue(state.available)
                self.assertEqual(state.backend_kind, "mock")
                self.assertEqual(state.completion_scope, "mock_state_snapshot")

    def test_unknown_device_is_typed_for_execute_read_and_availability(self):
        before = self.snapshot()
        for action in [lambda: self.backend.get_state("missing"),
                       lambda: self.backend.set_available("missing", False),
                       lambda: self.backend.execute(self.command("missing", "turn_on"))]:
            with self.assertRaises(DeviceBackendError) as caught:
                action()
            self.assertEqual(caught.exception.error_code, "UNKNOWN_DEVICE")
            self.assertEqual(self.snapshot(), before)

    def test_device_capability_not_just_known_action_is_enforced(self):
        self.assert_rejected_unchanged("UNSUPPORTED_ACTION", self.command("living_room_sensor", "turn_on"))

    def test_unknown_advertised_action_is_not_silently_accepted(self):
        custom = deepcopy(DEVICES)
        custom["living_room_light"]["capabilities"].append("future_action")
        backend = MockDeviceBackend(custom)
        with self.assertRaises(DeviceBackendError) as caught:
            backend.execute(self.command("living_room_light", "future_action"))
        self.assertEqual(caught.exception.error_code, "UNSUPPORTED_ACTION")
        self.assertFalse(backend.get_state("living_room_light").attributes["power"])

    def test_integer_setters_reject_bool_float_missing_range_and_extra_arguments(self):
        cases = [("living_room_light", "set_brightness", "brightness", 0, 100),
                 ("living_room_tv", "set_volume", "volume", 0, 100),
                 ("living_room_curtain", "set_position", "position", 0, 100),
                 ("living_room_air_conditioner", "set_temperature", "temperature", 16, 30),
                 ("living_room_fan", "set_speed", "speed", 1, 3)]
        for device_id, action, key, minimum, maximum in cases:
            invalid = [{}, {key: True}, {key: False}, {key: float(minimum)},
                       {key: str(minimum)}, {key: minimum - 1}, {key: maximum + 1},
                       {key: minimum, "extra": 1}]
            for arguments in invalid:
                with self.subTest(action=action, arguments=arguments):
                    self.assert_rejected_unchanged("INVALID_ARGUMENTS", self.command(device_id, action, arguments))

    def test_numeric_bounds_are_written_and_read_back(self):
        for device_id, action, key, values in [
            ("living_room_light", "set_brightness", "brightness", (0, 100)),
            ("living_room_tv", "set_volume", "volume", (0, 100)),
            ("living_room_air_conditioner", "set_temperature", "temperature", (16, 30)),
            ("living_room_fan", "set_speed", "speed", (1, 3)),
        ]:
            for value in values:
                with self.subTest(action=action, value=value):
                    state = self.backend.execute(self.command(device_id, action, {key: value}))
                    self.assertEqual(state.attributes[key], value)
                    self.assertEqual(self.backend.get_state(device_id), state)

    def test_open_app_missing_or_invalid_value_is_not_guessed_or_stringified(self):
        for arguments in [{}, {"app": None}, {"app": False}, {"app": {}}, {"app": ""}, {"app": "   "}]:
            with self.subTest(arguments=arguments):
                self.assert_rejected_unchanged("INVALID_ARGUMENTS", self.command("living_room_set_top_box", "open_app", arguments))

    def test_channel_and_mode_argument_sets_are_strict(self):
        for device_id, action, arguments in [
            ("living_room_set_top_box", "set_channel", {"channel": " "}),
            ("living_room_set_top_box", "set_channel", {"channel": "CCTV-5", "extra": 0}),
            ("living_room_air_conditioner", "set_hvac_mode", {"mode": "off"}),
            ("living_room_air_conditioner", "set_hvac_mode", {"mode": "COOL"}),
            ("bedroom_air_purifier", "set_mode", {"mode": "party"}),
        ]:
            with self.subTest(action=action, arguments=arguments):
                self.assert_rejected_unchanged("INVALID_ARGUMENTS", self.command(device_id, action, arguments))

    def test_no_argument_action_cannot_hide_a_state_change_in_extra_fields(self):
        for device_id, action, arguments in [
            ("living_room_light", "turn_on", {"brightness": 200}),
            ("living_room_light", "query_state", {"power": True}),
            ("home_gateway", "restart", {"force": True}),
        ]:
            self.assert_rejected_unchanged("INVALID_ARGUMENTS", self.command(device_id, action, arguments))

    def test_offline_read_is_last_known_snapshot_and_clock_is_preserved(self):
        online = self.backend.execute(self.command("living_room_light", "set_brightness", {"brightness": 40}))
        offline = self.backend.set_available("living_room_light", False)
        self.assertFalse(offline.available)
        self.assertEqual(dict(offline.attributes), dict(online.attributes))
        self.assertEqual(offline.updated_at, online.updated_at)
        self.assertEqual(self.backend.get_state("living_room_light"), offline)

    def test_offline_commands_and_query_fail_without_mutating_last_known_state(self):
        for device_id, action, args in [
            ("living_room_light", "turn_on", {}),
            ("living_room_air_conditioner", "set_temperature", {"temperature": 23}),
            ("living_room_curtain", "open", {}),
            ("living_room_sensor", "query_state", {}),
        ]:
            self.backend.set_available(device_id, False)
            self.assert_rejected_unchanged("DEVICE_OFFLINE", self.command(device_id, action, args))

    def test_availability_is_strict_bool_and_not_a_raw_state_setter(self):
        before = self.snapshot()
        for value in (1, 0, "false", None):
            with self.assertRaises(DeviceBackendError) as caught:
                self.backend.set_available("living_room_light", value)
            self.assertEqual(caught.exception.error_code, "INVALID_ARGUMENTS")
            self.assertEqual(self.snapshot(), before)

    def test_offline_recovery_requires_a_new_successful_control(self):
        initial = self.backend.get_state("living_room_light")
        self.backend.set_available("living_room_light", False)
        self.assert_rejected_unchanged("DEVICE_OFFLINE", self.command("living_room_light", "turn_on"))
        restored = self.backend.set_available("living_room_light", True)
        self.assertEqual(restored.attributes, initial.attributes)
        self.assertEqual(restored.updated_at, initial.updated_at)
        with patch("edge_smart_home.device.mock.utc_now", return_value=T1):
            changed = self.backend.execute(self.command("living_room_light", "turn_on"))
        self.assertTrue(changed.attributes["power"])
        self.assertEqual(changed.updated_at, T1)
        self.assertEqual(self.backend.get_state("living_room_light"), changed)

    def test_camera_online_attribute_is_synced_with_availability(self):
        with patch("edge_smart_home.device.mock.utc_now", return_value=T1):
            offline = self.backend.set_available("front_door_camera", False)
        self.assertFalse(offline.available)
        self.assertFalse(offline.attributes["online"])
        self.assertEqual(offline.updated_at, T1)
        self.assert_rejected_unchanged("DEVICE_OFFLINE", self.command("front_door_camera", "start_recording"))
        restored = self.backend.set_available("front_door_camera", True)
        self.assertTrue(restored.available)
        self.assertTrue(restored.attributes["online"])

    def test_initial_camera_offline_is_not_overridden_to_true(self):
        devices = deepcopy(DEVICES)
        devices["front_door_camera"]["initial_state"]["online"] = False
        backend = MockDeviceBackend(devices)
        self.assertFalse(backend.get_state("front_door_camera").available)
        with self.assertRaises(DeviceBackendError) as caught:
            backend.execute(self.command("front_door_camera", "query_state"))
        self.assertEqual(caught.exception.error_code, "DEVICE_OFFLINE")

    def test_lights_power_and_simulated_current_brightness_remain_consistent(self):
        for device_id in ("living_room_light", "bedroom_light", "kitchen_light"):
            with self.subTest(device_id=device_id):
                on = self.backend.execute(self.command(device_id, "turn_on"))
                self.assertEqual((on.attributes["power"], on.attributes["brightness"]), (True, 100))
                dimmed = self.backend.execute(self.command(device_id, "set_brightness", {"brightness": 40}))
                self.assertEqual((dimmed.attributes["power"], dimmed.attributes["brightness"]), (True, 40))
                repeated = self.backend.execute(self.command(device_id, "turn_on"))
                self.assertEqual(repeated.attributes["brightness"], 40)
                off = self.backend.execute(self.command(device_id, "turn_off"))
                self.assertEqual((off.attributes["power"], off.attributes["brightness"]), (False, 0))
                zero = self.backend.execute(self.command(device_id, "set_brightness", {"brightness": 0}))
                self.assertEqual(zero.attributes, off.attributes)
                self.assertEqual(self.backend.get_state(device_id), zero)

    def test_curtain_position_and_percentage_share_one_state(self):
        for device_id in ("living_room_curtain", "bedroom_curtain"):
            for action, args, expected in [("set_position", {"position": 50}, ("partial", 50)),
                                           ("open", {}, ("open", 100)), ("close", {}, ("closed", 0)),
                                           ("set_position", {"position": 100}, ("open", 100))]:
                state = self.backend.execute(self.command(device_id, action, args))
                self.assertEqual((state.attributes["position"], state.attributes["position_percent"]), expected)
                self.assertEqual(self.backend.get_state(device_id), state)

    def test_pause_of_off_player_is_a_noop_and_does_not_claim_paused(self):
        for device_id in ("living_room_set_top_box", "living_room_speaker"):
            initial = self.backend.get_state(device_id)
            with patch("edge_smart_home.device.mock.utc_now") as clock:
                state = self.backend.execute(self.command(device_id, "pause"))
            self.assertEqual(state, initial)
            clock.assert_not_called()
            self.assertEqual((state.attributes["power"], state.attributes["playback"]), (False, "stopped"))

    def test_play_pause_turn_off_and_readback_are_consistent(self):
        for device_id in ("living_room_set_top_box", "living_room_speaker"):
            self.backend.execute(self.command(device_id, "play"))
            paused = self.backend.execute(self.command(device_id, "pause"))
            self.assertEqual((paused.attributes["power"], paused.attributes["playback"]), (True, "paused"))
            stopped = self.backend.execute(self.command(device_id, "turn_off"))
            self.assertEqual((stopped.attributes["power"], stopped.attributes["playback"]), (False, "stopped"))
            self.assertEqual(self.backend.get_state(device_id), stopped)

    def test_camera_privacy_conflict_does_not_mutate_state_or_clock(self):
        self.assert_rejected_unchanged("DEVICE_STATE_CONFLICT", self.command("living_room_camera", "start_recording"))
        self.assert_rejected_unchanged("DEVICE_STATE_CONFLICT", self.command("living_room_camera", "show_feed"))

    def test_enabling_privacy_stops_existing_mock_recording(self):
        self.backend.execute(self.command("front_door_camera", "start_recording"))
        state = self.backend.execute(self.command("front_door_camera", "enable_privacy"))
        self.assertTrue(state.attributes["privacy_mode"])
        self.assertFalse(state.attributes["recording"])
        self.assertEqual(self.backend.get_state("front_door_camera"), state)

    def test_query_and_repeated_control_keep_attribute_clock_on_no_change(self):
        initial = self.backend.get_state("living_room_light")
        with patch("edge_smart_home.device.mock.utc_now") as clock:
            query = self.backend.execute(self.command("living_room_light", "query_state"))
            off = self.backend.execute(self.command("living_room_light", "turn_off"))
        self.assertEqual(query, initial)
        self.assertEqual(off, initial)
        clock.assert_not_called()

    def test_returned_nested_state_and_input_config_cannot_mutate_backend(self):
        devices = deepcopy(DEVICES)
        devices["living_room_sensor"]["initial_state"]["nested"] = {"samples": [1, 2]}
        backend = MockDeviceBackend(devices)
        devices["living_room_sensor"]["initial_state"]["nested"]["samples"].append(99)
        state = backend.get_state("living_room_sensor")
        state.attributes["nested"]["samples"].append(88)
        listed = next(s for s in backend.list_states() if s.device_id == "living_room_sensor")
        self.assertEqual(listed.attributes["nested"]["samples"], [1, 2])
        with self.assertRaises(TypeError):
            state.attributes["temperature"] = 10

    def test_bad_shared_argument_container_has_a_device_error(self):
        with self.assertRaises(DeviceBackendError) as caught:
            validate_action_arguments("query_state", [])
        self.assertEqual(caught.exception.error_code, "INVALID_ARGUMENTS")


if __name__ == "__main__":
    unittest.main()
