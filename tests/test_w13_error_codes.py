from enum import Enum
import json
import unittest

from edge_smart_home.agent.error_codes import (
    IntentErrorCode, assistant_error_code, diagnostic_error_code,
    input_error_code, normalize_error_code, result_error_code,
)
from edge_smart_home.agent.contract import AgentResponseContractError, parse_agent_response
from edge_smart_home.agent.interfaces import IntelligentPathResult, IntelligentResultKind, PlannedDeviceCommand
from edge_smart_home.agent.validation import AgentPlanValidationError, AgentPlanValidator


class IntentErrorCodeTest(unittest.TestCase):
    def test_request_input_boundary_rejects_before_silent_prefix_truncation(self):
        for value in (None, 1, True, b"text", [], {}, "", " \n\t", "查" * 513):
            with self.subTest(value_type=type(value).__name__):
                self.assertEqual(input_error_code(value), "REQUEST_INPUT_INVALID")
        self.assertIsNone(input_error_code("查" * 512))
        self.assertIsNone(input_error_code(" 查询客厅状态 "))

    def test_request_input_type_is_distinct_from_model_json_input_type(self):
        self.assertEqual(diagnostic_error_code("request_input_invalid"), "REQUEST_INPUT_INVALID")
        self.assertEqual(diagnostic_error_code("model_json_input_type"), "JSON_INPUT_TYPE")

    def test_explicit_enum_and_string_have_identical_stable_json_representation(self):
        code = IntentErrorCode.REQUEST_ARGUMENT_INVALID
        self.assertEqual(normalize_error_code(code), "REQUEST_ARGUMENT_INVALID")
        self.assertEqual(normalize_error_code(code.value), "REQUEST_ARGUMENT_INVALID")
        self.assertEqual(json.loads(json.dumps({"error_code": normalize_error_code(code)})),
                         {"error_code": "REQUEST_ARGUMENT_INVALID"})
        self.assertIsNone(normalize_error_code(None))

    def test_invalid_explicit_code_cannot_be_silently_serialized_as_success(self):
        for value in ("UNKNOWN_NEW_CODE", "", "model timeout", 400, True, {}, []):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_error_code(value)

    def test_enum_values_are_unique_machine_codes_not_human_messages(self):
        values = [c.value for c in IntentErrorCode]
        self.assertEqual(len(values), len(set(values)))
        self.assertTrue(all(v and v == v.upper() and " " not in v for v in values))

    def test_successful_plan_and_response_have_no_error_but_no_execution_claim(self):
        self.assertIsNone(result_error_code("plan"))
        self.assertIsNone(result_error_code("response"))
        self.assertIsNone(assistant_error_code("executed"))
        self.assertIsNone(assistant_error_code("responded"))

    def test_string_enum_kind_from_existing_interface_is_accepted_without_import_cycle(self):
        class Kind(str, Enum):
            PLAN = "plan"
            CLARIFICATION = "clarification"
        self.assertIsNone(result_error_code(Kind.PLAN))
        self.assertEqual(result_error_code(Kind.CLARIFICATION), "CLARIFICATION_REQUIRED")

    def test_specific_missing_parameter_survives_generic_clarification_kind(self):
        self.assertEqual(result_error_code("clarification", IntentErrorCode.REQUEST_ARGUMENT_MISSING),
                         "REQUEST_ARGUMENT_MISSING")
        self.assertEqual(assistant_error_code("clarification_required", result_code="REQUEST_ARGUMENT_MISSING"),
                         "REQUEST_ARGUMENT_MISSING")

    def test_explicit_app_failure_has_priority_over_stale_or_broad_result_code(self):
        self.assertEqual(assistant_error_code("device_error", explicit="EXECUTION_FAILED",
                                              result_code="REQUEST_PLAN_MISMATCH"), "EXECUTION_FAILED")

    def test_transport_failure_is_not_erased_by_unavailable_application_status(self):
        self.assertEqual(assistant_error_code("needs_intelligence", result_code="MODEL_HTTP_ERROR"),
                         "MODEL_HTTP_ERROR")
        self.assertEqual(assistant_error_code("unavailable", result_code="MODEL_TIMEOUT"), "MODEL_TIMEOUT")
        self.assertEqual(assistant_error_code("unavailable"), "CAPABILITY_UNAVAILABLE")

    def test_deferred_policy_code_is_distinct_from_generic_missing_capability(self):
        self.assertEqual(result_error_code("unavailable", "SCHEDULING_DEFERRED"), "SCHEDULING_DEFERRED")
        self.assertEqual(result_error_code("unavailable"), "CAPABILITY_UNAVAILABLE")

    def test_query_reconstruction_diagnostic_does_not_turn_valid_plan_into_error(self):
        self.assertEqual(diagnostic_error_code("query_plan_required"), "QUERY_PLAN_REQUIRED")
        self.assertIsNone(result_error_code("plan"))

    def test_lexical_and_wire_failures_remain_distinct_and_exactly_classified(self):
        expected = {"model_json_syntax": "JSON_SYNTAX", "model_json_encoding": "JSON_ENCODING",
                    "model_json_duplicate_key": "JSON_DUPLICATE_KEY", "model_json_nonfinite": "JSON_NONFINITE",
                    "model_json_root_type": "JSON_ROOT_TYPE", "wire_contract": "WIRE_CONTRACT_INVALID"}
        for category, code in expected.items():
            with self.subTest(category=category):
                self.assertEqual(diagnostic_error_code(category), code)

    def test_legacy_plan_error_only_gets_broad_code_until_typed_specificity_exists(self):
        self.assertEqual(diagnostic_error_code("plan_validation"), "PLAN_VALIDATION_FAILED")
        self.assertEqual(diagnostic_error_code("plan_validation", "UNKNOWN_DEVICE"), "UNKNOWN_DEVICE")
        self.assertEqual(diagnostic_error_code("plan_validation", "UNSUPPORTED_ACTION"), "UNSUPPORTED_ACTION")
        self.assertEqual(diagnostic_error_code("plan_validation", "INVALID_ARGUMENTS"), "INVALID_ARGUMENTS")

    def test_truncation_timeout_and_model_identity_are_not_semantic_mismatch(self):
        expected = {"generation_truncated": "MODEL_RESPONSE_TRUNCATED", "transport_timeout": "MODEL_TIMEOUT",
                    "model_identity": "MODEL_IDENTITY_MISMATCH", "semantic_mismatch": "REQUEST_PLAN_MISMATCH"}
        for category, code in expected.items():
            with self.subTest(category=category):
                self.assertEqual(diagnostic_error_code(category), code)

    def test_execution_confirmation_and_duplicate_statuses_preserve_stage(self):
        expected = {"confirmation_required": "CONFIRMATION_REQUIRED", "partial_failure": "EXECUTION_PARTIAL_FAILURE",
                    "duplicate_suppressed": "DUPLICATE_SUPPRESSED", "plan_rejected": "PLAN_VALIDATION_FAILED",
                    "not_routed": "ROUTE_UNSUPPORTED"}
        for status, code in expected.items():
            with self.subTest(status=status):
                self.assertEqual(assistant_error_code(status), code)

    def test_unknown_status_kind_or_category_is_visibly_unclassified(self):
        self.assertEqual(assistant_error_code("new_upstream_status"), "UNCLASSIFIED_ERROR")
        self.assertEqual(result_error_code("new_kind"), "UNCLASSIFIED_ERROR")
        self.assertEqual(diagnostic_error_code("AgentPlanValidationError: unknown device"), "UNCLASSIFIED_ERROR")
        self.assertEqual(diagnostic_error_code("error says timeout"), "UNCLASSIFIED_ERROR")
        self.assertIsNone(diagnostic_error_code("ok"))

    def test_existing_internal_result_constructor_remains_compatible(self):
        # Existing positional order kind/commands/message/reason is unchanged.
        response = IntelligentPathResult(IntelligentResultKind.RESPONSE, (), "说明", "原原因")
        self.assertIsNone(response.error_code)
        self.assertEqual(response.reason, "原原因")
        clarification = IntelligentPathResult(IntelligentResultKind.CLARIFICATION, message="请补参数")
        self.assertEqual(clarification.error_code, "CLARIFICATION_REQUIRED")
        missing = IntelligentPathResult(IntelligentResultKind.CLARIFICATION, message="请补参数",
                                        error_code=IntentErrorCode.REQUEST_ARGUMENT_MISSING)
        self.assertEqual(missing.error_code, "REQUEST_ARGUMENT_MISSING")

    def test_internal_result_rejects_unknown_explicit_code_without_weakening_kind_invariants(self):
        with self.assertRaises(ValueError):
            IntelligentPathResult(IntelligentResultKind.RESPONSE, message="说明", error_code="MISSPELLED")
        with self.assertRaises(ValueError):
            IntelligentPathResult(IntelligentResultKind.PLAN)
        with self.assertRaises(ValueError):
            IntelligentPathResult(IntelligentResultKind.RESPONSE, message="说明", commands=(
                PlannedDeviceCommand("light", "turn_on"),))

    def test_typed_exception_code_preserves_legacy_valueerror_message_and_cause(self):
        error = AgentPlanValidationError("原错误", error_code="INVALID_ARGUMENTS")
        self.assertIsInstance(error, ValueError)
        self.assertEqual(error.args, ("原错误",))
        self.assertEqual(str(error), "原错误")
        self.assertEqual(error.error_code, "INVALID_ARGUMENTS")
        self.assertEqual(AgentResponseContractError("错误").error_code, "WIRE_CONTRACT_INVALID")
        self.assertEqual(AgentPlanValidationError("错误").error_code, "PLAN_VALIDATION_FAILED")

    def test_product_parser_exposes_observed_lexical_stage_codes_without_message_guess(self):
        examples = [("{", "JSON_SYNTAX"), ("[]", "JSON_ROOT_TYPE"),
                    ('{"kind":"response","kind":"response","message":"说明","commands":[]}', "JSON_DUPLICATE_KEY"),
                    ('{"kind":"response","message":"说明","commands":[],"x":NaN}', "JSON_NONFINITE"),
                    (b'\xff', "JSON_ENCODING"), (None, "JSON_INPUT_TYPE")]
        for raw, expected in examples:
            with self.subTest(expected=expected), self.assertRaises(AgentResponseContractError) as caught:
                parse_agent_response(raw)
            self.assertEqual(caught.exception.error_code, expected)
            if expected != "JSON_ROOT_TYPE":
                self.assertIsNotNone(caught.exception.__cause__)

    def test_product_wire_rejection_is_distinct_from_json_syntax(self):
        with self.assertRaises(AgentResponseContractError) as caught:
            parse_agent_response('{"kind":"plan","message":"准备","commands":[]}')
        self.assertEqual(caught.exception.error_code, "WIRE_CONTRACT_INVALID")
        self.assertEqual(str(caught.exception), "plan must contain at least one command")

    def test_product_validator_attaches_codes_at_actual_device_action_argument_branches(self):
        validator = AgentPlanValidator({"light": {"capabilities": ["turn_on", "set_brightness"]}})
        examples = [(PlannedDeviceCommand("missing", "turn_on"), "UNKNOWN_DEVICE"),
                    (PlannedDeviceCommand("light", "erase_all"), "UNSUPPORTED_ACTION"),
                    (PlannedDeviceCommand("light", "set_brightness", {"brightness": 101}), "INVALID_ARGUMENTS"),
                    (PlannedDeviceCommand("light", "set_brightness", {}), "INVALID_ARGUMENTS"),
                    (PlannedDeviceCommand("light", "turn_on", {"brightness": 50}), "INVALID_ARGUMENTS")]
        for command, expected in examples:
            with self.subTest(expected=expected), self.assertRaises(AgentPlanValidationError) as caught:
                validator.validate((command,))
            self.assertEqual(caught.exception.error_code, expected)
        validator.validate((PlannedDeviceCommand("light", "set_brightness", {"brightness": 50}),))


if __name__ == "__main__":
    unittest.main()
