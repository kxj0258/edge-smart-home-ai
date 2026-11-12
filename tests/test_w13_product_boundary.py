from __future__ import annotations

import json
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from edge_smart_home.agent import (
    IntelligentPathResult, IntelligentResultKind, PlannedDeviceCommand,
    AgentPlanValidator, LocalLlmIntelligentPath, LlmHttpError, LlmTransportError,
)
from edge_smart_home.agent.error_codes import IntentErrorCode as Code
from edge_smart_home.app.pipeline import TextControlPipeline, RequestPlanConsistencyError
from edge_smart_home.app.orchestrator import AssistantOrchestrator
from edge_smart_home.app.cli import serialize_response
from edge_smart_home.common import EventSource, PerceptionEvent


ROOT = Path(__file__).resolve().parents[1]
DEVICES = json.loads((ROOT / "configs/devices.json").read_text())


class RecordingTransport:
    def __init__(self, response=None, error=None):
        self.calls = []
        self.response = response
        self.error = error

    def post_json(self, url, payload, **timeouts):
        self.calls.append(payload)
        if self.error:
            raise self.error
        return self.response


class NoModel:
    def route(self, event):
        raise AssertionError("preflight must not call a model")


def event(text):
    return PerceptionEvent(source=EventSource.TEXT, label=text)


def control():
    with patch.dict("os.environ", {"EDGE_SMART_HOME_BACKEND": "mock"}):
        return TextControlPipeline.from_config_dir(ROOT / "configs")


def envelope(value):
    return {"model": "qwen3:4b", "done": True, "done_reason": "stop",
            "response": json.dumps(value, ensure_ascii=False)}


class ProductIntentBoundaryTest(unittest.TestCase):
    def test_known_bad_numeric_requests_never_call_transport_or_read_states(self):
        cases = [("把卧室灯亮度设置一下", "clarification", Code.REQUEST_ARGUMENT_MISSING),
                 ("把客厅空调温度设置成31度", "rejected", Code.REQUEST_ARGUMENT_INVALID),
                 ("把卫生间排气扇速度设置为4档", "rejected", Code.REQUEST_ARGUMENT_INVALID)]
        transport = RecordingTransport()
        def no_states():
            raise AssertionError("preflight does not require device state")
        path = LocalLlmIntelligentPath(DEVICES, transport, state_provider=no_states)
        for text, kind, code in cases:
            with self.subTest(text=text):
                result = path.route(event(text))
                self.assertEqual((result.kind.value, result.error_code), (kind, code.value))
                self.assertFalse(result.commands)
                self.assertEqual(path.last_call_count, 0)
                self.assertIsNone(path.last_llm_latency_ms)
        self.assertEqual(transport.calls, [])

    def test_false_query_ack_becomes_only_valid_query_plan_without_execution(self):
        transport = RecordingTransport(envelope({"kind": "response", "message": "客厅环境状态已查询", "commands": []}))
        path = LocalLlmIntelligentPath(DEVICES, transport)
        result = path.route(event("查询客厅环境状态"))
        self.assertEqual(result.kind, IntelligentResultKind.PLAN)
        self.assertEqual([(c.device_id, c.action, dict(c.arguments)) for c in result.commands],
                         [("living_room_sensor", "query_state", {})])
        self.assertEqual(path.last_call_count, 1)
        self.assertEqual(len(transport.calls), 1)
        self.assertIsNone(result.error_code)
        self.assertIn("尚未执行", result.message)

    def test_valid_but_changed_model_parameter_is_rejected(self):
        value = {"kind": "plan", "message": "准备调整", "commands": [
            {"device_id": "living_room_air_conditioner", "action": "set_temperature", "arguments": {"temperature": 25}}]}
        result = LocalLlmIntelligentPath(DEVICES, RecordingTransport(envelope(value))).route(event("把客厅空调温度设置为26度"))
        self.assertEqual(result.kind, IntelligentResultKind.REJECTED)
        self.assertEqual(result.error_code, Code.REQUEST_PLAN_MISMATCH)
        self.assertFalse(result.commands)

    def test_orchestrator_bad_inputs_report_precise_codes_and_zero_calls(self):
        c = control()
        app = AssistantOrchestrator(c, NoModel())
        with patch.object(c.backend, "execute", side_effect=AssertionError("must not execute")):
            for text, code in [("把客厅空调温度设置成31度", Code.REQUEST_ARGUMENT_INVALID),
                               ("把卧室灯亮度设置一下", Code.REQUEST_ARGUMENT_MISSING),
                               ("明天关闭客厅灯", Code.SCHEDULING_DEFERRED)]:
                with self.subTest(text=text):
                    response = app.handle_text(text, confirmed=True)
                    self.assertEqual(response.error_code, code)
                    self.assertEqual(response.llm_calls, 0)
                    self.assertIsNone(response.provider)
                    self.assertEqual(serialize_response(response)["error_code"], code.value)

    def test_direct_pipeline_cannot_bypass_request_value_check(self):
        c = control()
        with patch.object(c.backend, "execute", side_effect=AssertionError("must not execute")):
            for text in ("把客厅空调温度设置成31度", "把卫生间排气扇速度设置为4档",
                         "把客厅空调调到-26度", "把客厅空调调到26.0度"):
                with self.subTest(text=text), self.assertRaises(RequestPlanConsistencyError) as raised:
                    c.handle_text(text, confirmed=True)
                self.assertEqual(raised.exception.error_code, Code.REQUEST_ARGUMENT_INVALID)

    def test_direct_agent_plan_cannot_bypass_request_plan_fidelity(self):
        c = control()
        commands = (PlannedDeviceCommand("living_room_air_conditioner", "set_temperature", {"temperature": 25}),)
        with patch.object(c.backend, "execute", side_effect=AssertionError("must not execute")):
            with self.assertRaises(RequestPlanConsistencyError) as raised:
                c.execute_agent_plan(event("把客厅空调温度设置为26度"), commands, confirmed=True)
            self.assertEqual(raised.exception.error_code, Code.REQUEST_PLAN_MISMATCH)

    def test_input_contract_rejects_long_blank_or_nontext_before_model(self):
        for text in ("", "  ", None, "开" * 513):
            with self.subTest(text=text):
                t = RecordingTransport()
                # Invalid labels cannot construct a PerceptionEvent. Exercise
                # the route boundary with a minimal foreign event and the
                # public text boundary separately, before either can send.
                result = LocalLlmIntelligentPath(DEVICES, t).route(SimpleNamespace(label=text))
                self.assertEqual(result.error_code, Code.REQUEST_INPUT_INVALID)
                self.assertEqual(t.calls, [])
                response = AssistantOrchestrator(control(), NoModel()).handle_text(text)
                self.assertEqual(response.error_code, Code.REQUEST_INPUT_INVALID)

    def test_known_generation_failures_keep_codes(self):
        cases = [({"response": ""}, Code.MODEL_RESPONSE_EMPTY),
                 ({"done": False, "response": "{}"}, Code.MODEL_RESPONSE_INCOMPLETE),
                 ({"done": True, "done_reason": "length", "response": "{}"}, Code.MODEL_RESPONSE_TRUNCATED),
                 ({"model": "different", "response": "{}"}, Code.MODEL_IDENTITY_MISMATCH),
                 ({"response": "{"}, Code.JSON_SYNTAX)]
        for response, code in cases:
            with self.subTest(code=code):
                result = LocalLlmIntelligentPath(DEVICES, RecordingTransport(response)).route(event("你好"))
                self.assertEqual(result.error_code, code)
                self.assertFalse(result.commands)

    def test_transport_and_state_codes_survive_unavailable_fallback(self):
        for error, code in [(LlmHttpError(400), Code.MODEL_HTTP_ERROR),
                            (LlmTransportError("timeout", error_code=Code.MODEL_TIMEOUT), Code.MODEL_TIMEOUT)]:
            result = LocalLlmIntelligentPath(DEVICES, RecordingTransport(error=error)).route(event("你好"))
            self.assertEqual(result.error_code, code)
        def broken():
            raise OSError("context inaccessible")
        t = RecordingTransport()
        result = LocalLlmIntelligentPath(DEVICES, t, state_provider=broken).route(event("你好"))
        self.assertEqual(result.error_code, Code.STATE_CONTEXT_UNAVAILABLE)
        self.assertEqual(t.calls, [])

    def test_unknown_device_error_code_reaches_existing_serializer(self):
        class BadPlan:
            def route(self, received):
                return IntelligentPathResult(kind=IntelligentResultKind.PLAN, commands=(PlannedDeviceCommand("absent", "turn_on"),))
        response = AssistantOrchestrator(control(), BadPlan(), AgentPlanValidator(DEVICES)).handle_text("请处理未知目标")
        self.assertEqual(response.status, "plan_rejected")
        self.assertEqual(response.error_code, Code.UNKNOWN_DEVICE)
        self.assertEqual(serialize_response(response)["error_code"], "UNKNOWN_DEVICE")

    def test_confirm_cancel_and_expiry_are_explicit_not_message_inferred(self):
        c = control()
        app = AssistantOrchestrator(c)
        with patch.object(c.backend, "execute", side_effect=AssertionError("must not execute")):
            self.assertEqual(app.handle_text("重启路由器").error_code, Code.CONFIRMATION_REQUIRED)
            self.assertEqual(app.handle_text("取消").error_code, Code.CONFIRMATION_CANCELLED)
            app.handle_text("重启路由器")
            later = app._pending_confirmation.created_at + app.confirmation_timeout_seconds + 1
            with patch("edge_smart_home.app.orchestrator.monotonic", return_value=later):
                self.assertEqual(app.handle_text("确认").error_code, Code.CONFIRMATION_EXPIRED)


if __name__ == "__main__":
    unittest.main()
