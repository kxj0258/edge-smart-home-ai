from __future__ import annotations

import json
from pathlib import Path
import unittest

from edge_smart_home.agent import (
    AgentResponseContractError,
    IntelligentResultKind,
    default_agent_response_schema,
    parse_agent_response,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class AgentResponseContractTest(unittest.TestCase):
    def test_versioned_schema_matches_allowed_wire_shape(self) -> None:
        schema = json.loads(
            (PROJECT_ROOT / "schemas" / "agent_response.schema.json").read_text(
                encoding="utf-8"
            )
        )
        runtime_schema = default_agent_response_schema()
        self.assertEqual(schema["$id"], runtime_schema["$id"])
        self.assertEqual(
            set(schema["properties"]["kind"]["enum"]),
            set(runtime_schema["properties"]["kind"]["enum"]),
        )
        self.assertFalse(schema["additionalProperties"])

    def test_valid_plan_is_converted_to_existing_internal_result(self) -> None:
        result = parse_agent_response(
            json.dumps(
                {
                    "kind": "plan",
                    "message": "准备客厅观影环境。",
                    "commands": [
                        {
                            "device_id": "living_room_tv",
                            "action": "turn_on",
                            "arguments": {},
                        }
                    ]
                },
                ensure_ascii=False,
            )
        )
        self.assertEqual(result.kind, IntelligentResultKind.PLAN)
        self.assertEqual(result.commands[0].device_id, "living_room_tv")

    def test_response_and_clarification_must_have_empty_commands(self) -> None:
        for kind in ("response", "clarification"):
            result = parse_agent_response(
                json.dumps({"kind": kind, "message": "说明", "commands": []})
            )
            self.assertEqual(result.kind.value, kind)

    def test_rejects_extra_fields_at_every_wire_level(self) -> None:
        payload = {
            "kind": "plan",
            "message": "开灯",
            "commands": [
                {
                    "device_id": "living_room_light",
                    "action": "turn_on",
                    "arguments": {},
                    "executed": True,
                }
            ],
            "debug": "ignore me",
        }
        with self.assertRaises(AgentResponseContractError):
            parse_agent_response(json.dumps(payload))

    def test_rejects_empty_plan_unknown_kind_and_bad_arguments(self) -> None:
        cases = [
            {"kind": "plan", "message": "空计划", "commands": []},
            {"kind": "other", "message": "未知", "commands": []},
            {
                "kind": "plan",
                "message": "调音量",
                "commands": [
                    {
                        "device_id": "living_room_tv",
                        "action": "set_volume",
                        "arguments": {"volume": 101},
                    }
                ],
            },
            {
                "kind": "plan",
                "message": "多余参数",
                "commands": [
                    {
                        "device_id": "living_room_tv",
                        "action": "turn_on",
                        "arguments": {"unknown": True},
                    }
                ],
            },
        ]
        for payload in cases:
            with self.subTest(payload=payload):
                with self.assertRaises(AgentResponseContractError):
                    parse_agent_response(json.dumps(payload))

    def test_rejects_more_than_configured_maximum(self) -> None:
        command = {
            "device_id": "living_room_light",
            "action": "turn_on",
            "arguments": {},
        }
        payload = {"kind": "plan", "message": "批量开灯", "commands": [command] * 2}
        with self.assertRaises(AgentResponseContractError):
            parse_agent_response(json.dumps(payload), max_commands=1)


if __name__ == "__main__":
    unittest.main()
