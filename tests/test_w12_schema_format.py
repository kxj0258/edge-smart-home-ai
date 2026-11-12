from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import unittest

from edge_smart_home.agent.contract import AgentResponseContractError, parse_agent_response
from edge_smart_home.agent.schema_format import ollama_agent_response_format


ROOT = Path(__file__).resolve().parents[1]


class W12SchemaFormatTest(unittest.TestCase):
    def setUp(self):
        self.schema = json.loads((ROOT / "schemas/agent_response.schema.json").read_text())

    @staticmethod
    def branch_accepts_relation(branch, kind, count):
        kinds = branch["properties"]["kind"]
        if not isinstance(kind, str) or kind not in kinds["enum"]:
            return False
        if "const" in kinds and kind != kinds["const"]:
            return False
        commands = branch["properties"]["commands"]
        return commands.get("minItems", 0) <= count <= commands.get("maxItems", float("inf"))

    def test_all_kind_and_count_combinations_preserve_relation(self):
        projected = ollama_agent_response_format(self.schema)
        # This exhaustive finite check covers the five-kind/count relation,
        # plus invalid kind values and count9 beyond the shared maximum8.
        # It is not advertised as a generic JSON Schema validation engine.
        for kind in [*self.schema["properties"]["kind"]["enum"], "unknown", None, True, 0]:
            for count in range(10):
                with self.subTest(kind=kind, count=count):
                    old = (kind in self.schema["properties"]["kind"]["enum"] and count <= 8
                           and (count >= 1 if kind == "plan" else count == 0))
                    matched = sum(self.branch_accepts_relation(b, kind, count) for b in projected["oneOf"])
                    self.assertEqual(matched == 1, old)
                    self.assertLessEqual(matched, 1, "oneOf alternatives must be disjoint")

    def test_every_shared_constraint_is_preserved_in_both_full_objects(self):
        projected = ollama_agent_response_format(self.schema)
        base = deepcopy(self.schema)
        base.pop("allOf")
        for key in ("$schema", "$id", "title", "description"):
            base.pop(key, None)
        plan, nonplan = deepcopy(projected["oneOf"])
        plan["properties"]["kind"].pop("const")
        plan["properties"]["commands"].pop("minItems")
        nonplan["properties"]["kind"]["enum"] = deepcopy(base["properties"]["kind"]["enum"])
        nonplan["properties"]["commands"]["maxItems"] = base["properties"]["commands"]["maxItems"]
        self.assertEqual(plan, base)
        self.assertEqual(nonplan, base)
        self.assertNotIn("properties", projected)
        self.assertNotIn("allOf", projected)
        self.assertEqual(projected["$schema"], self.schema["$schema"])
        self.assertEqual(projected["$id"], self.schema["$id"])

    def test_projection_never_mutates_canonical_input_or_shares_branch_objects(self):
        before = deepcopy(self.schema)
        projected = ollama_agent_response_format(self.schema)
        self.assertEqual(before, self.schema)
        projected["oneOf"][0]["properties"]["message"]["maxLength"] = 1
        self.assertEqual(projected["oneOf"][1]["properties"]["message"]["maxLength"], 512)
        self.assertEqual(self.schema, before)

    def test_changed_unknown_conditional_is_rejected_instead_of_ignored(self):
        altered = deepcopy(self.schema)
        altered["allOf"][0]["then"]["properties"]["commands"]["minItems"] = 2
        with self.assertRaises(ValueError):
            ollama_agent_response_format(altered)
        altered = deepcopy(self.schema)
        altered["allOf"].append({"required": ["new_field"]})
        with self.assertRaises(ValueError):
            ollama_agent_response_format(altered)

    def test_changed_enum_or_missing_required_kind_is_rejected(self):
        for change in ("enum", "required"):
            altered = deepcopy(self.schema)
            if change == "enum":
                altered["properties"]["kind"]["enum"].append("scheduled")
            else:
                altered["required"].remove("kind")
            with self.subTest(change=change), self.assertRaises(ValueError):
                ollama_agent_response_format(altered)

    def test_existing_item_type_range_and_whitelist_constraints_are_kept(self):
        projected = ollama_agent_response_format(self.schema)
        original_items = self.schema["properties"]["commands"]["items"]
        for branch in projected["oneOf"]:
            self.assertEqual(branch["properties"]["commands"]["items"], original_items)
            self.assertFalse(branch["additionalProperties"])
            self.assertFalse(branch["properties"]["commands"]["items"]["additionalProperties"])
        invalid = {"kind": "plan", "message": "说明", "commands": []}
        with self.assertRaises(AgentResponseContractError):
            parse_agent_response(json.dumps(invalid))
        invalid["kind"] = "clarification"
        self.assertEqual(parse_agent_response(json.dumps(invalid)).kind.value, "clarification")

    def test_existing_stricter_minimum_is_intersected_not_relaxed(self):
        altered = deepcopy(self.schema)
        altered["properties"]["commands"]["minItems"] = 2
        projected = ollama_agent_response_format(altered)
        self.assertEqual(projected["oneOf"][0]["properties"]["commands"]["minItems"], 2)
        self.assertEqual(projected["oneOf"][1]["properties"]["commands"]["minItems"], 2)
        self.assertEqual(projected["oneOf"][1]["properties"]["commands"]["maxItems"], 0)


if __name__ == "__main__":
    unittest.main()
