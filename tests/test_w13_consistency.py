from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import unittest

from edge_smart_home.agent.interfaces import IntelligentPathResult, IntelligentResultKind, PlannedDeviceCommand
from edge_smart_home.agent.request_consistency import RequestPlanConsistency
from edge_smart_home.common import EventSource, PerceptionEvent


ROOT = Path(__file__).resolve().parents[1]


class RequestConsistencyTest(unittest.TestCase):
    def setUp(self):
        self.devices = json.loads((ROOT / "configs/devices.json").read_text())
        self.policy = RequestPlanConsistency(self.devices)

    @staticmethod
    def event(text):
        return PerceptionEvent(source=EventSource.TEXT, label=text)

    @staticmethod
    def plan(device, action, **arguments):
        return IntelligentPathResult(kind=IntelligentResultKind.PLAN, message="准备计划",
                                     commands=(PlannedDeviceCommand(device, action, arguments),))

    def check(self, text, result):
        return self.policy.check(self.event(text), result)

    def test_query_claim_is_replaced_only_with_a_prepared_read_plan(self):
        raw = IntelligentPathResult(kind=IntelligentResultKind.RESPONSE, message="客厅环境状态已查询")
        decision = self.check("查询客厅环境状态", raw)
        self.assertEqual(decision.code, "QUERY_PLAN_REQUIRED")
        self.assertTrue(decision.applied)
        self.assertEqual(decision.result.kind, IntelligentResultKind.PLAN)
        self.assertEqual(decision.result.commands, (PlannedDeviceCommand("living_room_sensor", "query_state", {}),))
        self.assertIn("尚未执行", decision.result.message)
        self.assertNotIn("已查询", decision.result.message)
        self.assertEqual(raw.message, "客厅环境状态已查询")

    def test_missing_brightness_is_clarified_before_even_a_nonplan_reply(self):
        for raw in (self.plan("bedroom_light", "set_brightness", brightness=0),
                    IntelligentPathResult(kind=IntelligentResultKind.RESPONSE, message="说明")):
            decision = self.check("把卧室灯亮度设置一下", raw)
            self.assertEqual(decision.code, "REQUEST_ARGUMENT_MISSING")
            self.assertEqual(decision.result.kind, IntelligentResultKind.CLARIFICATION)
            self.assertFalse(decision.result.commands)

    def test_out_of_range_temperature_cannot_be_clipped_to_valid_output(self):
        decision = self.check("把客厅空调温度设置成31度", self.plan("living_room_air_conditioner", "set_temperature", temperature=30))
        self.assertEqual(decision.code, "REQUEST_ARGUMENT_INVALID")
        self.assertEqual(decision.result.kind, IntelligentResultKind.REJECTED)
        self.assertFalse(decision.result.commands)

    def test_out_of_range_fan_speed_cannot_be_rewritten_to_one(self):
        decision = self.check("把卫生间排气扇速度设置为4档", self.plan("bathroom_fan", "set_speed", speed=1))
        self.assertEqual(decision.code, "REQUEST_ARGUMENT_INVALID")
        self.assertFalse(decision.result.commands)

    def test_preflight_detects_bad_input_independently_of_model_kind(self):
        for text, code in (("把卧室灯亮度设置一下", "REQUEST_ARGUMENT_MISSING"),
                           ("把客厅空调温度设为31度", "REQUEST_ARGUMENT_INVALID"),
                           ("把卫生间排气扇风速设为4档", "REQUEST_ARGUMENT_INVALID")):
            with self.subTest(text=text):
                result = self.policy.preflight(self.event(text))
                self.assertIsNotNone(result)
                self.assertEqual(result.code, code)
                self.assertFalse(result.result.commands)

    def test_valid_explicit_value_matches_exactly_without_rewriting(self):
        pairs = [("把电视音量调到35", self.plan("living_room_tv", "set_volume", volume=35)),
                 ("把卧室灯亮度设置为50", self.plan("bedroom_light", "set_brightness", brightness=50)),
                 ("把卧室窗帘位置设置为60", self.plan("bedroom_curtain", "set_position", position=60)),
                 ("把客厅空调温度设置为26度", self.plan("living_room_air_conditioner", "set_temperature", temperature=26)),
                 ("把卫生间排气扇速度设置为2档", self.plan("bathroom_fan", "set_speed", speed=2))]
        for text, raw in pairs:
            with self.subTest(text=text):
                self.assertIsNone(self.policy.preflight(self.event(text)))
                decision = self.check(text, raw)
                self.assertIs(decision.result, raw)
                self.assertIsNone(decision.code)

    def test_valid_but_different_value_or_target_or_extra_command_is_rejected(self):
        raw_cases = [self.plan("bedroom_light", "set_brightness", brightness=0),
                     self.plan("living_room_light", "set_brightness", brightness=50),
                     self.plan("bedroom_light", "turn_on"),
                     self.plan("bedroom_light", "set_brightness", brightness=50, volume=10),
                     IntelligentPathResult(kind=IntelligentResultKind.PLAN, message="计划", commands=(
                         PlannedDeviceCommand("bedroom_light", "set_brightness", {"brightness":50}),
                         PlannedDeviceCommand("living_room_tv", "turn_on", {})))]
        for raw in raw_cases:
            with self.subTest(raw=raw):
                decision = self.check("把卧室灯亮度设为50", raw)
                self.assertEqual(decision.code, "REQUEST_PLAN_MISMATCH")
                self.assertEqual(decision.result.kind, IntelligentResultKind.REJECTED)

    def test_chinese_integers_are_whole_tokens_and_use_existing_bounds(self):
        good = self.plan("living_room_tv", "set_volume", volume=35)
        self.assertIs(self.check("把电视音量设为三十五", good).result, good)
        for text in ("把客厅空调温度设为三十一度", "把电视音量设为一千", "把电视音量设为三五", "把电视音量设为十十"):
            with self.subTest(text=text):
                self.assertEqual(self.policy.preflight(self.event(text)).code, "REQUEST_ARGUMENT_INVALID")

    def test_signed_negative_decimal_exponent_fraction_and_suffix_are_not_partially_matched(self):
        for token in ("-1", "负一", "50.0", "三十点五", "1e2", "1/2", "50abc", "50.1.2"):
            with self.subTest(token=token):
                self.assertEqual(self.policy.preflight(self.event("把卧室灯亮度设为"+token)).code, "REQUEST_ARGUMENT_INVALID")

    def test_units_do_not_cross_parameter_families(self):
        for text in ("把卧室灯亮度设为50度", "把客厅空调温度设为26档", "把卫生间排气扇速度设为2%"):
            with self.subTest(text=text):
                self.assertEqual(self.policy.preflight(self.event(text)).code, "REQUEST_ARGUMENT_INVALID")

    def test_number_separators_are_not_silently_joined(self):
        for token in ("0,5", "0，5", "3 5", "3。5"):
            with self.subTest(token=token):
                result = self.policy.preflight(self.event("把卧室灯亮度设为"+token))
                self.assertEqual(result.code, "REQUEST_ARGUMENT_INVALID")
        raw = self.plan("bedroom_light", "set_brightness", brightness=35)
        self.assertIs(self.check("请， 把卧室灯亮度设为 35", raw).result, raw)

    def test_explicit_alias_dominates_nested_generic_alias(self):
        raw = self.plan("bedroom_light", "set_brightness", brightness=50)
        self.assertIs(self.check("请把卧室的灯亮度设为50", raw).result, raw)
        query = self.check("查看卧室空调状态", IntelligentPathResult(kind=IntelligentResultKind.RESPONSE, message="尚未查询"))
        self.assertEqual(query.result.commands[0].device_id, "bedroom_air_conditioner")

    def test_multi_target_query_or_setting_is_not_guessed(self):
        raw = self.plan("living_room_light", "turn_on")
        self.assertEqual(self.check("查询客厅环境和卧室环境状态", raw).code, "REQUEST_TARGET_AMBIGUOUS")
        result = self.policy.preflight(self.event("把客厅灯和卧室灯亮度设置为50"))
        self.assertEqual(result.code, "REQUEST_TARGET_AMBIGUOUS")

    def test_query_is_read_only_and_unchanged_when_already_exact(self):
        raw = self.plan("living_room_sensor", "query_state")
        decision = self.check("查询客厅环境状态", raw)
        self.assertIs(decision.result, raw)
        self.assertFalse(decision.applied)
        self.assertIsNone(self.policy.preflight(self.event("查询客厅空调设置的温度")))

    def test_negation_does_not_create_control_or_query(self):
        raw = self.plan("bedroom_light", "set_brightness", brightness=50)
        for text in ("不要把卧室灯亮度设为50", "别查询客厅环境状态"):
            with self.subTest(text=text):
                decision = self.check(text, raw)
                self.assertEqual(decision.result.kind, IntelligentResultKind.REJECTED)
                self.assertFalse(decision.result.commands)

    def test_model_rejection_of_valid_control_is_not_turned_into_control(self):
        raw = IntelligentPathResult(kind=IntelligentResultKind.REJECTED, message="拒绝")
        self.assertIs(self.check("把卧室灯亮度设为50", raw).result, raw)

    def test_query_requires_configured_capability_and_does_not_mutate_catalog(self):
        before = deepcopy(self.devices)
        self.devices["living_room_sensor"]["capabilities"] = []
        decision = self.check("查询客厅环境状态", IntelligentPathResult(kind=IntelligentResultKind.RESPONSE, message="说明"))
        self.assertEqual(decision.code, "REQUEST_PLAN_MISMATCH")
        self.devices["living_room_sensor"]["capabilities"] = before["living_room_sensor"]["capabilities"]
        self.assertEqual(self.devices, before)

    def test_unrecognized_relative_or_dialogue_request_is_left_to_existing_path(self):
        raw = IntelligentPathResult(kind=IntelligentResultKind.RESPONSE, message="说明")
        for text in ("你好", "把音量调高一点", "我想看下一集电视", "让空气净化器进入睡眠模式"):
            with self.subTest(text=text):
                self.assertIsNone(self.policy.preflight(self.event(text)))
                self.assertIs(self.check(text, raw).result, raw)

    def test_explanation_capability_and_media_questions_do_not_become_state_plans(self):
        raw = IntelligentPathResult(kind=IntelligentResultKind.RESPONSE, message="说明")
        for text in ("为什么要查询客厅环境状态", "你能查询客厅环境状态吗",
                     "请解释把卧室灯亮度设置为50是什么意思", "查看客厅摄像头画面"):
            with self.subTest(text=text):
                self.assertIsNone(self.policy.preflight(self.event(text)))
                self.assertIs(self.check(text, raw).result, raw)

    def test_abbreviated_absolute_setters_cannot_drop_sign_or_decimal(self):
        for text in ("把客厅空调调到-26度", "把客厅空调调到26.0度", "把客厅空调调到三十一度",
                     "把卧室窗帘调到-50", "把电视设为35.0", "把卫生间排气扇设为4档"):
            with self.subTest(text=text):
                result = self.policy.preflight(self.event(text))
                self.assertIsNotNone(result)
                self.assertEqual(result.code, "REQUEST_ARGUMENT_INVALID")

    def test_abbreviated_values_use_unique_numeric_capability_and_full_fidelity(self):
        pairs = [("把客厅空调调到26度", self.plan("living_room_air_conditioner", "set_temperature", temperature=26)),
                 ("把卧室空调设置26", self.plan("bedroom_air_conditioner", "set_temperature", temperature=26)),
                 ("把卧室灯设为50", self.plan("bedroom_light", "set_brightness", brightness=50)),
                 ("把卫生间排气扇设为二档", self.plan("bathroom_fan", "set_speed", speed=2))]
        for text, raw in pairs:
            with self.subTest(text=text):
                self.assertIsNone(self.policy.preflight(self.event(text)))
                self.assertIs(self.check(text, raw).result, raw)
        self.assertEqual(self.check("把客厅空调调到26度", self.plan("living_room_air_conditioner", "set_temperature", temperature=25)).code, "REQUEST_PLAN_MISMATCH")

    def test_abbreviated_setter_never_infers_default_or_multi_numeric_action(self):
        for text in ("调到31度", "设置26", "把客厅灯和卧室灯设置50"):
            with self.subTest(text=text):
                self.assertEqual(self.policy.preflight(self.event(text)).code, "REQUEST_TARGET_AMBIGUOUS")
        self.devices["bedroom_light"]["capabilities"].append("set_volume")
        self.assertEqual(self.policy.preflight(self.event("把卧室灯设为50")).code, "REQUEST_TARGET_AMBIGUOUS")


if __name__ == "__main__":
    unittest.main()
