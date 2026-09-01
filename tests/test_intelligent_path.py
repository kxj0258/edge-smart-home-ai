from __future__ import annotations

import json
from pathlib import Path
import unittest

from edge_smart_home.agent import (
    AgentPlanValidationError,
    AgentPlanValidator,
    IntelligentPathResult,
    IntelligentResultKind,
    PlannedDeviceCommand,
)
from edge_smart_home.app import AssistantOrchestrator
from edge_smart_home.app.cli import serialize_response
from edge_smart_home.app.pipeline import TextControlPipeline


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class RecordingIntelligentPath:
    def __init__(self) -> None:
        self.labels: list[str] = []

    def route(self, event):
        self.labels.append(event.label)
        return IntelligentPathResult(
            IntelligentResultKind.UNAVAILABLE, reason="stub unavailable"
        )


class FixedPlanPath:
    def __init__(self, command):
        self.command = command

    def route(self, event):
        return IntelligentPathResult(
            IntelligentResultKind.PLAN, commands=(self.command,)
        )


class IntelligentPathTest(unittest.TestCase):
    def setUp(self) -> None:
        self.intelligent = RecordingIntelligentPath()
        self.orchestrator = AssistantOrchestrator(
            TextControlPipeline.from_config_dir(PROJECT_ROOT / "configs"),
            self.intelligent,
        )

    def test_simple_command_does_not_call_intelligent_path(self) -> None:
        response = self.orchestrator.handle_text("打开电视")
        self.assertEqual(response.status, "executed")
        self.assertEqual(self.intelligent.labels, [])

    def test_unsupported_text_is_explicitly_deferred(self) -> None:
        response = self.orchestrator.handle_text("客厅有点暗，帮我处理一下")
        self.assertEqual(response.status, "needs_intelligence")
        self.assertEqual(self.intelligent.labels, ["客厅有点暗，帮我处理一下"])
        self.assertEqual(
            serialize_response(response)["intelligent_path"]["kind"], "unavailable"
        )

    def test_structured_plan_contract_rejects_empty_plan(self) -> None:
        with self.assertRaises(ValueError):
            IntelligentPathResult(IntelligentResultKind.PLAN)
        plan = IntelligentPathResult(
            IntelligentResultKind.PLAN,
            commands=(PlannedDeviceCommand("living_room_light", "turn_on"),),
        )
        self.assertEqual(plan.commands[0].device_id, "living_room_light")

    def test_configured_rule_plan_executes_through_backend(self) -> None:
        orchestrator = AssistantOrchestrator.from_config_dir(PROJECT_ROOT / "configs")
        response = orchestrator.handle_text("客厅有点暗，帮我处理一下")
        self.assertEqual(response.status, "executed")
        self.assertEqual(response.outcome.scene_id, "agent_plan")
        self.assertTrue(response.outcome.results[0].state.attributes["power"])
        self.assertEqual(response.intelligent_result.kind, IntelligentResultKind.PLAN)

    def test_response_and_clarification_do_not_access_backend(self) -> None:
        orchestrator = AssistantOrchestrator.from_config_dir(PROJECT_ROOT / "configs")
        greeting = orchestrator.handle_text("你好")
        clarification = orchestrator.handle_text("这里有点暗")
        self.assertEqual(greeting.status, "responded")
        self.assertEqual(clarification.status, "clarification_required")
        self.assertIsNone(greeting.outcome)
        self.assertIsNone(clarification.outcome)

    def test_validator_rejects_unknown_action_before_execution(self) -> None:
        devices = json.loads(
            (PROJECT_ROOT / "configs" / "devices.json").read_text(encoding="utf-8")
        )
        validator = AgentPlanValidator(devices)
        control = TextControlPipeline.from_config_dir(PROJECT_ROOT / "configs")
        before = control.backend.get_state("living_room_light")
        orchestrator = AssistantOrchestrator(
            control,
            FixedPlanPath(PlannedDeviceCommand("living_room_light", "restart")),
            validator,
        )
        response = orchestrator.handle_text("执行未授权操作")
        after = control.backend.get_state("living_room_light")
        self.assertEqual(response.status, "plan_rejected")
        self.assertEqual(before.attributes, after.attributes)

    def test_validator_rejects_out_of_range_parameters(self) -> None:
        devices = json.loads(
            (PROJECT_ROOT / "configs" / "devices.json").read_text(encoding="utf-8")
        )
        with self.assertRaises(AgentPlanValidationError):
            AgentPlanValidator(devices).validate(
                [
                    PlannedDeviceCommand(
                        "living_room_tv", "set_volume", {"volume": 101}
                    )
                ]
            )

    def test_agent_plan_cannot_bypass_high_risk_confirmation(self) -> None:
        devices = json.loads(
            (PROJECT_ROOT / "configs" / "devices.json").read_text(encoding="utf-8")
        )
        control = TextControlPipeline.from_config_dir(PROJECT_ROOT / "configs")
        orchestrator = AssistantOrchestrator(
            control,
            FixedPlanPath(PlannedDeviceCommand("home_gateway", "restart")),
            AgentPlanValidator(devices),
        )
        blocked = orchestrator.handle_text("帮我修复家庭网络")
        self.assertEqual(blocked.status, "confirmation_required")
        self.assertEqual(
            control.backend.get_state("home_gateway").attributes["restart_count"], 0
        )
        executed = orchestrator.handle_text("帮我修复家庭网络", confirmed=True)
        self.assertEqual(executed.status, "executed")
        self.assertEqual(
            control.backend.get_state("home_gateway").attributes["restart_count"], 1
        )


if __name__ == "__main__":
    unittest.main()
