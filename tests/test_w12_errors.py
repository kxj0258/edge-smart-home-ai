from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import json
import unittest
from unittest.mock import patch

from edge_smart_home.agent.contract import (
    AgentResponseContractError, default_agent_response_schema, parse_agent_response,
)
from edge_smart_home.agent.evaluation_errors import (
    diagnostic_fixtures, diagnose_fixture_suite, evaluate_model_response,
)
from edge_smart_home.common import EventSource, PerceptionEvent


ROOT = Path(__file__).resolve().parents[1]


class W12ContractDiagnosticTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.devices = json.loads((ROOT / "configs/devices.json").read_text())

    def evaluate(self, text, query=None, **kwargs):
        event = PerceptionEvent(source=EventSource.TEXT, label=query) if query else None
        return evaluate_model_response(text, devices=self.devices, event=event, **kwargs)

    def test_product_rejects_duplicate_wire_fields_at_every_nested_level(self):
        values = [
            '{"kind":"rejected","kind":"response","message":"说明","commands":[]}',
            '{"kind":"plan","message":"开灯","commands":[{"device_id":"unknown","device_id":"living_room_light","action":"turn_on","arguments":{}}]}',
            '{"kind":"plan","message":"亮度","commands":[{"device_id":"living_room_light","action":"set_brightness","arguments":{"brightness":101,"brightness":50}}]}',
        ]
        for text in values:
            with self.subTest(text=text):
                with self.assertRaises(AgentResponseContractError) as raised:
                    parse_agent_response(text)
                self.assertIsInstance(raised.exception.__cause__, ValueError)
                self.assertFalse(self.evaluate(text)["product_parse_accepted"])

    def test_repeated_words_and_keys_in_different_objects_are_allowed(self):
        payload = {"kind": "plan", "message": "kind kind brightness brightness", "commands": [
            {"device_id": "living_room_light", "action": "turn_on", "arguments": {}},
            {"device_id": "bedroom_light", "action": "turn_on", "arguments": {}},
        ]}
        self.assertEqual(len(parse_agent_response(json.dumps(payload)).commands), 2)

    def test_all_injected_fixtures_match_their_separate_expected_classes(self):
        results = diagnose_fixture_suite(self.devices)
        self.assertEqual(len(results), 26)
        self.assertTrue(all(r["matched"] for r in results))
        self.assertTrue(all(r["evidence_kind"] == "intentional_injected_fixture_not_model_generation" for r in results))
        self.assertEqual({r["expected_category"] for r in results}, {
            "ok", "model_json_syntax", "model_json_root_type", "model_json_nonfinite",
            "model_json_duplicate_key", "wire_contract", "plan_validation",
        })

    def test_contract_and_semantic_validator_errors_are_distinct(self):
        by_id = {c["id"]: c for c in diagnostic_fixtures()}
        boolean = self.evaluate(by_id["numeric_bool"]["input_text"])
        missing = self.evaluate(by_id["missing_action_argument"]["input_text"])
        mode = self.evaluate(by_id["invalid_mode_enum"]["input_text"])
        self.assertEqual(boolean["primary_category"], "wire_contract")
        self.assertFalse(boolean["product_parse_accepted"])
        self.assertEqual(missing["primary_category"], "plan_validation")
        self.assertTrue(missing["product_parse_accepted"])
        self.assertFalse(missing["raw_plan_validation_accepted"])
        self.assertEqual(mode["raw_plan_validation_error"]["exception_type"], "AgentPlanValidationError")

    def test_invalid_raw_plan_is_not_hidden_by_guard_rejection(self):
        case = next(c for c in diagnostic_fixtures() if c["id"] == "invalid_raw_not_hidden_by_guard")
        value = self.evaluate(case["input_text"], case["query"])
        self.assertFalse(value["passed"])
        self.assertTrue(value["product_parse_accepted"])
        self.assertFalse(value["raw_plan_validation_accepted"])
        self.assertEqual(value["parsed_result"]["kind"], "plan")
        self.assertEqual(value["guard_result"]["kind"], "rejected")
        self.assertTrue(value["request_guard_changed"])
        self.assertIsNone(value["plan_validation_accepted"])
        self.assertEqual(value["errors"][0]["stage"], "raw_plan_validation")

    def test_guard_change_is_observed_separately_from_model_wire_validity(self):
        case = next(c for c in diagnostic_fixtures() if c["id"] == "guard_rejects_request")
        value = self.evaluate(case["input_text"], case["query"])
        self.assertTrue(value["passed"])
        self.assertTrue(value["raw_plan_validation_accepted"])
        self.assertEqual(value["parsed_result"]["kind"], "plan")
        self.assertEqual(value["guard_result"]["kind"], "rejected")
        self.assertTrue(value["request_guard_changed"])

    def test_nonplan_does_not_claim_plan_validation_was_performed(self):
        case = next(c for c in diagnostic_fixtures() if c["id"] == "valid_response")
        value = self.evaluate(case["input_text"])
        self.assertTrue(value["passed"])
        self.assertIsNone(value["raw_plan_validation_accepted"])
        self.assertIsNone(value["plan_validation_accepted"])
        self.assertFalse(value["request_guard_applied"])
        self.assertEqual(value["parsed_result"], value["guard_result"])

    def test_schema_and_runtime_schema_match_every_constraint_modulo_enum_order(self):
        stored = json.loads((ROOT / "schemas/agent_response.schema.json").read_text())
        runtime = default_agent_response_schema()
        for value in (stored, runtime):
            value["properties"]["kind"]["enum"].sort()
        self.assertEqual(stored, runtime)
        self.assertNotIn("time", stored["properties"])
        self.assertNotIn("room", stored["properties"]["commands"]["items"]["properties"])

    def test_current_parser_has_documented_stricter_string_and_integer_rules(self):
        # Draft2020-12 minLength does not reject spaces, and an integer-valued
        # JSON number50.0 can satisfy its integer type. Product policy is stricter.
        for identifier in ("whitespace_message", "numeric_float"):
            case = next(c for c in diagnostic_fixtures() if c["id"] == identifier)
            self.assertEqual(self.evaluate(case["input_text"])["primary_category"], "wire_contract")

    def test_no_error_category_depends_on_validator_exception_message(self):
        text = next(c["input_text"] for c in diagnostic_fixtures() if c["id"] == "valid_plan")
        from edge_smart_home.agent.validation import AgentPlanValidationError
        with patch("edge_smart_home.agent.evaluation_errors.AgentPlanValidator.validate",
                   side_effect=AgentPlanValidationError("a wholly different localized reason")):
            value = self.evaluate(text)
        self.assertEqual(value["primary_category"], "plan_validation")
        self.assertEqual(value["raw_plan_validation_error"]["message"], "a wholly different localized reason")

    def test_diagnostics_preserve_lexical_and_product_acceptance_separately(self):
        text = next(c["input_text"] for c in diagnostic_fixtures() if c["id"] == "duplicate_top_level")
        accepted = parse_agent_response('{"kind":"response","message":"说明","commands":[]}')
        with patch("edge_smart_home.agent.evaluation_errors.parse_agent_response", return_value=accepted):
            value = self.evaluate(text)
        self.assertFalse(value["lexical_accepted"])
        self.assertTrue(value["product_parse_accepted"])
        self.assertFalse(value["passed"])
        self.assertEqual(value["primary_category"], "model_json_duplicate_key")
        self.assertIsNone(value["guard_result"])

    def test_diagnostics_are_json_serializable_and_leave_catalog_unchanged(self):
        original = deepcopy(self.devices)
        rows = diagnose_fixture_suite(self.devices)
        json.dumps(rows, allow_nan=False)
        self.assertEqual(self.devices, original)

    def test_invalid_configuration_is_a_caller_error_not_a_model_error(self):
        for value in (0, 9, True, 1.5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.evaluate("{}", max_commands=value)

    def test_non_text_field_is_not_confused_with_json_root_type(self):
        value = self.evaluate(None)
        self.assertEqual(value["primary_category"], "model_json_input_type")
        self.assertFalse(value["product_parse_accepted"])

    def test_invalid_encoded_json_is_distinct_from_a_non_text_field(self):
        value = self.evaluate(b"\xff")
        self.assertEqual(value["primary_category"], "model_json_encoding")
        self.assertFalse(value["product_parse_accepted"])


if __name__ == "__main__":
    unittest.main()
