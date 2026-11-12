from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import evaluate_intent_pipeline as evaluation
from edge_smart_home.agent.local_llm import LlmHttpError


def metadata():
    details = {"format": "gguf", "family": "qwen3", "parameter_size": "4.0B", "quantization_level": "Q4_K_M"}
    info = {"general.architecture": "qwen3", "qwen3.block_count": 36, "qwen3.embedding_length": 2560,
            "qwen3.attention.head_count": 32, "qwen3.attention.head_count_kv": 8,
            "qwen3.attention.key_length": 128, "qwen3.attention.value_length": 128, "qwen3.context_length": 262144}
    return {"version": {"version": evaluation.SERVER_VERSION},
            "tags": {"models": [{"name": "qwen3:4b", "digest": evaluation.MODEL_DIGEST, "details": details}]},
            "show": {"details": details, "model_info": info, "thinking": {"values": [True], "default": True},
                     "template": "fixed", "license": "Apache-2.0"}, "ps": {"models": []}}


class FakeClient:
    def __init__(self):
        self.value = metadata(); self.calls = []

    def get_json(self, path):
        self.calls.append(path)
        return deepcopy(self.value[path.split("/")[-1]])

    def show(self):
        self.calls.append("/api/show")
        return deepcopy(self.value["show"])


class FakeTransport:
    def __init__(self, p, overrides=None):
        self.p = p; self.calls = []; self.overrides = overrides or {}

    def post_json(self, endpoint, payload, **timeouts):
        index = len(self.calls)
        self.calls.append({"endpoint": endpoint, "payload": deepcopy(payload), "timeouts": timeouts})
        case = self.p["corpus"]["cases"][index % 29]
        answer = {"kind": case["expected"]["allowed_kinds"][0], "message": "准备计划。" if case["expected"]["commands"] else "说明。",
                  "commands": deepcopy(case["expected"]["commands"])}
        response = {"model": "qwen3:4b", "done": True, "done_reason": "stop", "response": json.dumps(answer, ensure_ascii=False),
                    "eval_count": 12, "eval_duration": 100000000, "prompt_eval_count": 3752,
                    "prompt_eval_cached_count": 3700, "prompt_eval_duration": 10000000}
        override = self.overrides.get(index)
        if isinstance(override, Exception):
            raise override
        if isinstance(override, dict):
            response.update(override)
        return response


class W13EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(); self.addCleanup(self.temp.cleanup); self.base = Path(self.temp.name)
        self.gitpatch = patch.object(evaluation, "git_info", return_value={"commit": "1" * 40, "status": ""})
        self.gitpatch.start(); self.addCleanup(self.gitpatch.stop)

    def prepared(self):
        directory = self.base / "prepare"; directory.mkdir()
        client = FakeClient()
        result = evaluation.prepare(SimpleNamespace(config_dir=evaluation.ROOT / "configs", corpus=evaluation.CORPUS, outdir=directory), client=client)
        path = directory / "protocol.json"
        return path, evaluation.read_json(path), result, client

    def execute(self, overrides=None):
        path, p, _, _ = self.prepared(); out = self.base / "run"; out.mkdir()
        transport = FakeTransport(p, overrides)
        result = evaluation.run(SimpleNamespace(protocol=path, outdir=out), client=FakeClient(), transport=transport)
        return result, out, p, transport

    def reindex(self, out):
        index = evaluation.read_json(out / "raw-index.json")
        for name in index["sha256"]:
            index["sha256"][name] = evaluation.sha256(out / name)
        (out / "raw-index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n")

    @staticmethod
    def answer(kind, commands=(), message="说明。"):
        return {"response": json.dumps({"kind": kind, "message": message, "commands": list(commands)}, ensure_ascii=False)}

    def test_prepare_freezes_three_full_rounds_without_generation_or_oracle_leak(self):
        _, p, result, client = self.prepared()
        self.assertEqual(result["generation_calls"], 0); self.assertEqual(len(client.calls), 4)
        self.assertEqual(len(p["requests"]), 87); self.assertEqual(p["corpus_file_sha256"], evaluation.CORPUS_SHA256)
        for n, request in enumerate(p["requests"]):
            self.assertEqual((request["call_index"], request["round"], request["round_case_index"]), (n, n // 29, n % 29))
            self.assertEqual(request["payload"]["options"], {"temperature": 0.0, "num_predict": 256, "seed": 42})
            self.assertFalse(request["payload"]["think"]); self.assertFalse(request["payload"]["stream"])
            self.assertNotIn("allowed_kinds", request["payload"]["prompt"])
            self.assertNotIn("frozen oracle", request["payload"]["prompt"])
            self.assertEqual(request["payload"], p["requests"][n % 29]["payload"])

    def test_full_fake87_calls_and_offline_rebuild_have_no_execution(self):
        with patch.object(evaluation.w12.MockDeviceBackend, "execute", side_effect=AssertionError("execution forbidden")) as execute:
            result, out, _, transport = self.execute()
            execute.assert_not_called()
        self.assertTrue(result["passed"]); self.assertEqual(len(transport.calls), 87)
        summary = evaluation.read_json(out / "summary.json")
        self.assertEqual(summary["model_raw_exact_matches"], 87); self.assertEqual(summary["guarded_exact_matches"], 87)
        self.assertEqual(summary["generation_calls"], 87); self.assertEqual(summary["device_execution_calls"], 0)
        self.assertEqual(len((out / "calls.jsonl").read_text().splitlines()), 174)
        self.assertEqual(summary["stability"]["raw_semantic_stable_cases"], 29)
        self.assertEqual(summary["stability"]["guarded_semantic_stable_cases"], 29)
        self.assertEqual(summary["stability"]["guarded_code_stable_cases"], 29)
        self.assertEqual(summary["offline_injected_fixture_suite"]["count"], 26)
        self.assertEqual(summary["offline_injected_fixture_suite"]["matched"], 26)
        for round_result in summary["rounds"].values():
            self.assertEqual(round_result["case_count"], 29)
            self.assertEqual(round_result["guarded_exact_matches"], 29)
        with patch.object(evaluation, "MetadataClient", side_effect=AssertionError("report must be offline")):
            self.assertEqual(evaluation.rebuild(out), summary)
        for call in transport.calls:
            self.assertEqual(call["timeouts"], {"connect_timeout_seconds": 2.0, "total_timeout_seconds": 30.0})

    def test_w12_four_actual_failures_are_repaired_without_improving_raw_score(self):
        corpus = evaluation.read_json(evaluation.CORPUS)
        bad = {
            "W12-N08": self.answer("response", message="客厅环境状态已查询"),
            "W12-A08": self.answer("plan", [{"device_id": "bedroom_light", "action": "set_brightness", "arguments": {"brightness": 0}}]),
            "W12-I08": self.answer("plan", [{"device_id": "living_room_air_conditioner", "action": "set_temperature", "arguments": {"temperature": 30}}]),
            "W12-I09": self.answer("plan", [{"device_id": "bathroom_fan", "action": "set_speed", "arguments": {"speed": 1}}]),
        }
        overrides = {round_index * 29 + n: bad[c["id"]] for round_index in range(3) for n, c in enumerate(corpus["cases"]) if c["id"] in bad}
        result, out, _, transport = self.execute(overrides)
        self.assertTrue(result["passed"]); self.assertEqual(len(transport.calls), 87)
        s = evaluation.read_json(out / "summary.json")
        self.assertEqual(s["model_raw_exact_matches"], 75); self.assertEqual(s["guarded_exact_matches"], 87)
        self.assertFalse(s["raw_model_all_semantics_passed"]); self.assertTrue(s["guarded_all_semantics_passed"])
        self.assertEqual(s["guarded_unsafe_plan_count"], 0)
        for target in evaluation.TARGET_IDS:
            self.assertEqual(s["regressions"][target], {"observed": 3, "raw_exact_matches": 0, "guarded_exact_matches": 3})
        self.assertGreaterEqual(s["guarded_result_code_counts"]["REQUEST_ARGUMENT_MISSING"], 3)
        self.assertGreaterEqual(s["guarded_result_code_counts"]["REQUEST_ARGUMENT_INVALID"], 6)

    def test_transport_and_json_failures_keep_all_rounds_and_denominator(self):
        result, out, _, transport = self.execute({0: {"response": '{"kind":'}, 29: LlmHttpError(503)})
        self.assertFalse(result["passed"]); self.assertEqual(len(transport.calls), 87)
        s = evaluation.read_json(out / "summary.json")
        self.assertEqual(s["case_count"], 87); self.assertEqual(s["wire_contract_valid_count"], 85)
        self.assertEqual(s["model_completed_count"], 86)
        self.assertEqual(s["raw_evaluation_code_counts"]["JSON_SYNTAX"], 1)
        self.assertEqual(s["raw_evaluation_code_counts"]["MODEL_HTTP_ERROR"], 1)
        self.assertEqual(s["stability"]["raw_semantic_stable_cases"], 28)

    def test_raw_semantic_correctness_and_text_stability_are_separate(self):
        corpus = evaluation.read_json(evaluation.CORPUS); case = corpus["cases"][0]
        override = self.answer("plan", case["expected"]["commands"], message="不同措辞。")
        result, out, _, _ = self.execute({29: override})
        self.assertTrue(result["passed"])
        s = evaluation.read_json(out / "summary.json")["stability"]
        self.assertEqual(s["raw_semantic_stable_cases"], 29); self.assertEqual(s["raw_full_text_stable_cases"], 28)
        self.assertEqual(s["guarded_semantic_stable_cases"], 29)

    def test_non_target_semantic_failure_is_not_hidden_by_four_target_gates(self):
        result, out, _, _ = self.execute({0: self.answer("response", message="不知道。")})
        self.assertFalse(result["passed"])
        s = evaluation.read_json(out / "summary.json")
        self.assertTrue(s["mandatory_gates"]["four_regressions_pass_all_rounds"])
        self.assertFalse(s["mandatory_gates"]["all_guarded_semantics_match"])
        self.assertEqual(s["stability"]["raw_semantic_stable_cases"], 28)

    def test_truncated_json_shaped_response_is_not_accepted(self):
        result, out, _, _ = self.execute({58: {"done_reason": "length"}})
        self.assertFalse(result["passed"])
        record = evaluation.read_json(out / "records/00058.json")
        self.assertTrue(record["analysis"]["diagnosis"]["product_parse_accepted"])
        self.assertFalse(record["analysis"]["wire_contract_valid"])
        self.assertEqual(record["analysis"]["error_codes"]["raw_evaluation"], "MODEL_RESPONSE_TRUNCATED")

    def test_typed_unknown_device_code_is_not_inferred_from_message(self):
        _, p, _, _ = self.prepared(); case = p["corpus"]["cases"][0]
        wire = {"model": "qwen3:4b", "done": True, "done_reason": "stop", **self.answer("plan", [
            {"device_id": "not_a_device", "action": "turn_on", "arguments": {}}])}
        a = evaluation.analyze_generation({"response": wire, "error": None}, case, p)
        self.assertTrue(a["raw_unsafe_plan"])
        self.assertEqual(a["error_codes"]["raw_evaluation"], "UNKNOWN_DEVICE")

    def test_saved_code_or_score_mutation_is_rejected_even_after_reindex(self):
        _, out, _, _ = self.execute(); path = out / "records/00000.json"
        row = evaluation.read_json(path); row["analysis"]["error_codes"]["guarded_result"] = "REQUEST_REJECTED"
        path.write_text(json.dumps(row, ensure_ascii=False, indent=2) + "\n"); self.reindex(out)
        with self.assertRaisesRegex(ValueError, "Saved analysis"):
            evaluation.rebuild(out)

    def test_round_or_order_tampering_is_rejected(self):
        _, out, _, _ = self.execute(); path = out / "records/00029.json"
        row = evaluation.read_json(path); row["round"] = 0
        path.write_text(json.dumps(row, ensure_ascii=False, indent=2) + "\n"); self.reindex(out)
        with self.assertRaisesRegex(ValueError, "Frozen schedule"):
            evaluation.rebuild(out)

    def test_raw_hash_mutation_rejected_before_saved_analysis(self):
        _, out, _, _ = self.execute(); path = out / "records/00000.json"
        path.write_text(path.read_text() + " ")
        with self.assertRaisesRegex(ValueError, "Raw artifact"):
            evaluation.rebuild(out)

    def test_frozen_gate_or_oracle_tampering_is_rejected(self):
        _, p, _, _ = self.prepared(); p["acceptance_policy"]["mandatory"].remove("all_guarded_semantics_match")
        with self.assertRaisesRegex(ValueError, "acceptance policy"):
            evaluation.validate_protocol(p)
        fresh = evaluation.read_json(self.base / "prepare/protocol.json")
        fresh["corpus"]["cases"][0]["expected"]["allowed_kinds"] = ["rejected"]
        fresh["corpus_content_sha256"] = evaluation.signature(fresh["corpus"])
        with self.assertRaisesRegex(ValueError, "Frozen inline input"):
            evaluation.check_sources(fresh)

    def test_existing_output_is_rejected_before_any_api_client(self):
        out = self.base / "existing"; out.mkdir(); sentinel = out / "keep"; sentinel.write_text("unchanged")
        with patch.object(evaluation, "MetadataClient", side_effect=AssertionError("no API")) as client:
            with self.assertRaises(SystemExit) as error:
                evaluation.main(["run", "--protocol", "missing", "--outdir", str(out)])
        self.assertEqual(error.exception.code, 2); client.assert_not_called(); self.assertEqual(sentinel.read_text(), "unchanged")

    def test_dirty_prepare_is_rejected_before_metadata(self):
        out = self.base / "dirty"; out.mkdir()
        with patch.object(evaluation, "git_info", return_value={"commit": "1" * 40, "status": " M changed.py"}):
            with patch.object(evaluation, "MetadataClient") as client:
                with self.assertRaisesRegex(ValueError, "clean"):
                    evaluation.prepare(SimpleNamespace(config_dir=ROOT / "configs", corpus=evaluation.CORPUS, outdir=out))
                client.assert_not_called()

    def test_unexpected_classifier_failure_retains_received_response_before_rejecting(self):
        path, p, _, _ = self.prepared(); out = self.base / "checker-failure"; out.mkdir()
        transport = FakeTransport(p)
        with patch.object(evaluation, "analyze_generation", side_effect=RuntimeError("intentional checker failure")):
            with self.assertRaises(RuntimeError):
                evaluation.run(SimpleNamespace(protocol=path, outdir=out), client=FakeClient(), transport=transport)
        self.assertEqual(len(transport.calls), 1)
        record = evaluation.read_json(out / "records/00000.json")
        self.assertIsNone(record["analysis"])
        self.assertEqual(record["analysis_error"]["type"], "RuntimeError")
        self.assertTrue(record["generation"]["response"]["done"])
        self.assertEqual(len((out / "calls.jsonl").read_text().splitlines()), 2)
        status = evaluation.read_json(out / "run-status.json")
        self.assertFalse(status["evidence_integrity"]); self.assertEqual(status["completed_calls"], 1)


if __name__ == "__main__":
    unittest.main()
