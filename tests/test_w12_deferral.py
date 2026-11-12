from pathlib import Path
import json
import unittest
from unittest.mock import patch

from edge_smart_home.agent.interfaces import (
    IntelligentPathResult, IntelligentResultKind, PlannedDeviceCommand,
)
from edge_smart_home.agent.local_llm import LocalLlmRequestGuard
from edge_smart_home.common import EventSource, PerceptionEvent
from edge_smart_home.app.orchestrator import AssistantOrchestrator
from edge_smart_home.app.pipeline import TextControlPipeline
from edge_smart_home.router import UnsupportedCommandError


ROOT = Path(__file__).resolve().parents[1]


class TimerDeferralTest(unittest.TestCase):
    def setUp(self):
        self.guard = LocalLlmRequestGuard(json.loads((ROOT / "configs/devices.json").read_text()))
        self.plan = IntelligentPathResult(
            kind=IntelligentResultKind.PLAN,
            commands=(PlannedDeviceCommand("living_room_light", "turn_off", {}),),
            message="准备关灯",
        )

    def apply(self, text, result=None):
        return self.guard.apply(PerceptionEvent(source=EventSource.TEXT, label=text), result or self.plan)

    def test_relative_and_absolute_cases_cannot_become_immediate_plans(self):
        for text in ("十分钟后关闭客厅灯", "明天早上七点打开卧室空调"):
            with self.subTest(text=text):
                result = self.apply(text)
                self.assertEqual(result.kind, IntelligentResultKind.UNAVAILABLE)
                self.assertFalse(result.commands)
                self.assertIn("暂不支持", result.message)
                self.assertIn("deferred", result.reason)

    def test_recognized_delay_and_recurrence_variants_are_deferred(self):
        for text in ("10分钟后关闭客厅灯", "半小时之后关闭客厅灯", "30秒后关闭客厅灯",
                     "每天晚上关掉客厅灯", "每隔两小时关闭客厅灯", "今晚关闭客厅灯",
                     "定时关闭客厅灯", "稍后打开卧室空调"):
            with self.subTest(text=text):
                self.assertEqual(self.apply(text).kind, IntelligentResultKind.UNAVAILABLE)
                self.assertFalse(self.apply(text).commands)

    def test_immediate_controls_remain_the_original_result(self):
        self.assertIs(self.apply("现在关闭客厅灯"), self.plan)
        self.assertIs(self.apply("请把客厅灯关闭"), self.plan)

    def test_unknown_or_unsafe_request_rejection_still_takes_precedence(self):
        result = self.apply("明天打开未知设备 secret_door")
        self.assertEqual(result.kind, IntelligentResultKind.REJECTED)
        self.assertFalse(result.commands)

    def test_nonplan_output_does_not_claim_scheduled_success(self):
        result = self.apply("明天打开客厅灯", IntelligentPathResult(
            kind=IntelligentResultKind.RESPONSE, message="已经定时打开了", commands=(),
        ))
        self.assertEqual(result.kind, IntelligentResultKind.UNAVAILABLE)
        self.assertFalse(result.commands)
        self.assertNotIn("已经", result.message)

    def control(self):
        with patch.dict("os.environ", {"EDGE_SMART_HOME_BACKEND": "mock"}):
            return TextControlPipeline.from_config_dir(ROOT / "configs")

    def test_pipeline_rejects_timing_before_direct_command_or_scene(self):
        control = self.control()
        with patch.object(control.backend, "execute", side_effect=AssertionError("must not execute")):
            for text in ("十分钟后关闭客厅灯", "明天早上七点打开卧室空调", "明天回家模式"):
                with self.subTest(text=text), self.assertRaises(UnsupportedCommandError):
                    control.handle_text(text, confirmed=True)

    def test_orchestrator_returns_unavailable_without_model_or_execution(self):
        control = self.control()

        class NoModel:
            def route(self, event):
                raise AssertionError("deferred request must not call a model")

        app = AssistantOrchestrator(control, NoModel())
        with patch.object(control.backend, "execute", side_effect=AssertionError("must not execute")):
            for source in (EventSource.TEXT, EventSource.VOICE):
                for text in ("十分钟后关闭客厅灯", "明天早上七点打开卧室空调", "每天回家模式"):
                    with self.subTest(source=source, text=text):
                        response = app.handle_event(PerceptionEvent(source=source, label=text), confirmed=True)
                        self.assertEqual(response.status, "unavailable")
                        self.assertEqual(response.llm_calls, 0)
                        self.assertIsNone(response.provider)
                        self.assertFalse(response.intelligent_result.commands)

    def test_pipeline_immediate_resolution_stays_available(self):
        _, commands = self.control().resolve_event(PerceptionEvent(source=EventSource.TEXT, label="关闭客厅灯"))
        self.assertEqual([(c.device_id, c.action) for c in commands], [("living_room_light", "turn_off")])

    def test_confirmed_deferred_request_preserves_pending_clear_behavior(self):
        control = self.control()
        app = AssistantOrchestrator(control)
        with patch.object(control.backend, "execute", side_effect=AssertionError("must not execute")):
            first = app.handle_text("重启路由器")
            self.assertEqual(first.status, "confirmation_required")
            self.assertIsNotNone(app._pending_confirmation)
            response = app.handle_text("明天关闭客厅灯", confirmed=True)
            self.assertEqual(response.status, "unavailable")
            self.assertIsNone(app._pending_confirmation)
            self.assertEqual(response.llm_calls, 0)


if __name__ == "__main__":
    unittest.main()
