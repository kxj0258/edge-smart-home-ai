from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import unittest

from edge_smart_home.agent import (
    AgentPlanValidationError,
    AgentPlanValidator,
    AgentResponseContractError,
    PlannedDeviceCommand,
    parse_agent_response,
)


ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "configs/evaluation/w12-home-intents-v1.json"


class W12FrozenCasesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
        cls.cases = cls.corpus["cases"]
        cls.devices = json.loads((ROOT / "configs/devices.json").read_text())

    def test_primary_categories_are_balanced_and_cases_are_unique(self):
        self.assertEqual(self.corpus["schema_version"], 1)
        self.assertEqual(self.corpus["kind"], "w12_home_intent_cases")
        counts = Counter(c["category"] for c in self.cases)
        self.assertEqual(counts, {"normal": 9, "ambiguous": 9, "illegal": 9, "deferred": 2})
        self.assertGreaterEqual(sum(counts[k] for k in ("normal", "ambiguous", "illegal")), 20)
        self.assertEqual(self.corpus["counts"], dict(counts, total=len(self.cases)))
        self.assertEqual(len({c["id"] for c in self.cases}), len(self.cases))
        self.assertEqual(len({c["text"] for c in self.cases}), len(self.cases))
        self.assertTrue(all(c["id"].startswith("W12-") and c["text"].strip() for c in self.cases))

    def test_historical_provenance_matches_literal_fixtures_and_raw_files(self):
        sources = self.corpus["source_files"]
        self.assertEqual(len(sources), 4)
        for source in sources:
            path = ROOT / source["path"]
            if "case_count" in source:
                data = path.read_bytes()
                self.assertEqual(hashlib.sha256(data).hexdigest(), source["sha256"])
                self.assertEqual(len(json.loads(data)), source["case_count"])
            elif path.is_file():
                # Historical raw runs are ignored local evidence, not a unit
                # test dependency in a Git-only checkout.
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), source["sha256"])
            else:
                self.assertEqual(len(source["sha256"]), 64)
        historical = [c for c in self.cases if c["provenance"]["kind"] == "historical_exact"]
        self.assertEqual(len(historical), 19)
        for case in historical:
            provenance = case["provenance"]
            fixture = json.loads((ROOT / provenance["historical_path"]).read_text())
            old = next(c for c in fixture if c["id"] == provenance["historical_case_id"])
            self.assertEqual(case["text"], old["text"])
            self.assertEqual(provenance["historical_text"], old["text"])
            self.assertIn(provenance["historical_run_path"], {s["path"] for s in sources})
        self.assertEqual(sum(c["provenance"]["kind"] == "new_authored" for c in self.cases), 10)
        self.assertTrue(all(c["provenance"]["rationale"].strip() for c in self.cases))

    def test_catalog_and_schema_are_pinned_without_adding_wire_fields(self):
        for key in ("device_catalog", "response_schema"):
            source = self.corpus[key]
            self.assertEqual(hashlib.sha256((ROOT / source["path"]).read_bytes()).hexdigest(), source["sha256"])
        schema = json.loads((ROOT / self.corpus["response_schema"]["path"]).read_text())
        self.assertEqual(set(schema["properties"]), {"kind", "message", "commands"})
        self.assertFalse(schema["additionalProperties"])
        for forbidden in ("room", "time", "schedule"):
            self.assertNotIn(forbidden, schema["properties"])

    def test_all_expected_plans_pass_the_existing_wire_and_device_contract(self):
        validator = AgentPlanValidator(self.devices, 8)
        for case in self.cases:
            expected = case["expected"]
            with self.subTest(case=case["id"]):
                self.assertTrue(expected["must_not_execute"])
                self.assertEqual(len(expected["allowed_kinds"]), 1)
                payload = {"kind": expected["allowed_kinds"][0], "message": "固定期望的离线合同验证", "commands": expected["commands"]}
                result = parse_agent_response(json.dumps(payload, ensure_ascii=False))
                if case["category"] == "normal":
                    self.assertEqual(result.kind.value, "plan")
                    validator.validate(result.commands)
                    self.assertEqual(len(result.commands), 1)
                else:
                    self.assertEqual(result.commands, ())

    def test_normal_plans_cover_parameters_rooms_and_distinct_capabilities(self):
        normals = [c for c in self.cases if c["category"] == "normal"]
        parameters = {k for c in normals for command in c["expected"]["commands"] for k in command["arguments"]}
        self.assertEqual(parameters, {"volume", "temperature", "brightness", "position", "speed", "mode"})
        self.assertEqual({room for c in normals for room in c["coverage"]["rooms"]}, {"living_room", "bedroom", "bathroom"})
        self.assertEqual(len({command["action"] for c in normals for command in c["expected"]["commands"]}), 9)
        for case in normals:
            command = case["expected"]["commands"][0]
            self.assertEqual(case["coverage"]["device_ids"], [command["device_id"]])
            self.assertEqual(case["coverage"]["actions"], [command["action"]])
            self.assertEqual(set(case["coverage"]["parameters"]), set(command["arguments"]))

    def test_ambiguity_does_not_invent_missing_room_for_configured_aliases(self):
        ambiguities = [c for c in self.cases if c["category"] == "ambiguous"]
        self.assertTrue(all(c["expected"] == {"allowed_kinds": ["clarification"], "commands": [], "must_not_execute": True} for c in ambiguities))
        self.assertIn("灯", self.devices["living_room_light"]["aliases"])
        by_id = {c["id"]: c for c in ambiguities}
        self.assertEqual(by_id["W12-A07"]["coverage"]["concern"], "unresolved_action")
        self.assertEqual(by_id["W12-A09"]["coverage"]["rooms"], ["bedroom", "kitchen"])
        self.assertIn("还没决定", by_id["W12-A09"]["text"])
        self.assertEqual(by_id["W12-A08"]["coverage"]["concern"], "missing_required_value")

    def test_illegal_parameter_and_allowlist_proposals_are_rejected_without_execution(self):
        illegal = [c for c in self.cases if c["category"] == "illegal"]
        self.assertTrue(all(c["expected"] == {"allowed_kinds": ["rejected"], "commands": [], "must_not_execute": True} for c in illegal))
        validator = AgentPlanValidator(self.devices, 8)
        proposals = [c["coverage"]["invalid_request_command"] for c in illegal if "invalid_request_command" in c["coverage"]]
        self.assertEqual(len(proposals), 6)
        for command in proposals:
            with self.subTest(command=command):
                proposal = PlannedDeviceCommand(command["device_id"], command["action"], command["arguments"])
                with self.assertRaises(AgentPlanValidationError):
                    validator.validate([proposal])
                if command["arguments"]:
                    with self.assertRaises(AgentResponseContractError):
                        parse_agent_response(json.dumps({"kind": "plan", "message": "非法参数仅作离线反例", "commands": [command]}))

    def test_relative_and_absolute_timers_are_explicitly_deferred_without_plan(self):
        timers = [c for c in self.cases if c["category"] == "deferred"]
        self.assertEqual({c["coverage"]["concern"] for c in timers}, {"relative_scheduling_deferred", "absolute_scheduling_deferred"})
        self.assertTrue(all(c["expected"] == {"allowed_kinds": ["unavailable"], "commands": [], "must_not_execute": True} for c in timers))
        self.assertEqual(self.corpus["timer_policy"], {"status": "deferred", "scheduler_added": False, "wire_time_field_added": False, "expected_kind": "unavailable", "immediate_plan_allowed": False})
        self.assertEqual([c["text"] for c in timers], ["十分钟后关闭客厅灯", "明天早上七点打开卧室空调"])


if __name__ == "__main__":
    unittest.main()
