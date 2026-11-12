"""W13: frozen three-round intent/error-code evaluation, without execution.

W12 helpers retain their historical code and raw format. This entry point forces
one model-only request per case and round, independently of product preflight.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]

import evaluate_home_intents as w12
from edge_smart_home.agent.contract import parse_agent_response
from edge_smart_home.agent.error_codes import diagnostic_error_code, normalize_error_code, result_error_code
from edge_smart_home.agent.local_llm import LocalLlmRequestGuard, UrllibJsonTransport
from edge_smart_home.agent.rule_based import RuleBasedIntelligentPath
from edge_smart_home.common import EventSource, PerceptionEvent

CORPUS = w12.CORPUS
CORPUS_SHA256 = "a3add7c45c7345e683293f77d22b6a89b57a87ccfeec450d516925fc9fdd910a"
MODEL_DIGEST, SERVER_VERSION = w12.MODEL_DIGEST, w12.SERVER_VERSION
ROUNDS = 3
TARGET_IDS = ("W12-N08", "W12-A08", "W12-I08", "W12-I09")
read_json, write_json, sha256 = w12.read_json, w12.write_json, w12.sha256
signature, now, git_info = w12.signature, w12.now, w12.git_info
MetadataClient, snapshot, identity = w12.MetadataClient, w12.snapshot, w12.identity


def source_hashes(corpus_path, corpus):
    values = w12.source_hashes(corpus_path, corpus)
    names = ("scripts/evaluate_intent_pipeline.py", "src/edge_smart_home/agent/request_consistency.py",
             "src/edge_smart_home/agent/error_codes.py", "src/edge_smart_home/app/cli.py",
             "src/edge_smart_home/router/fast_path.py")
    values.update({name: sha256(ROOT / name) for name in names})
    return dict(sorted(values.items()))


def acceptance_policy():
    return {
        "rounds": ROUNDS, "cases_per_round": 29, "one_model_call_per_case_per_round": True,
        "no_retry_skip_or_adaptive_tuning": True,
        "mandatory": ["evidence_integrity", "all_attempts_classified", "all_model_responses_completed",
                      "all_wire_contracts_valid", "all_guarded_plans_valid", "four_regressions_pass_all_rounds",
                      "no_guarded_unsafe_plans", "all_guarded_semantics_match"],
        "raw_model_exact_100_percent_required": False,
        "stability": "Observed kind+ordered commands across three rounds; response prose and code stability separate; not future determinism proof",
        "product_preflight": "Evaluation deliberately forces all87 raw model calls; production may reject invalid/deferred requests before any model call",
        "no_execution_scheduler_lora_or_new_service": True,
    }


def requests(config, devices, schema, cases):
    result = []
    for round_index in range(ROUNDS):
        for request in w12.payloads(config, devices, schema, cases):
            result.append({**request, "call_index": len(result), "round": round_index,
                           "round_case_index": request["call_index"]})
    return result


def validate_protocol(p):
    if (p.get("schema_version") != 1 or p.get("kind") != "w13_frozen_intent_protocol"
            or p.get("state") != "frozen"):
        raise ValueError("Expected a frozen W13 protocol")
    if p["expected_digest"] != MODEL_DIGEST or p["expected_version"] != SERVER_VERSION:
        raise ValueError("W13 must reuse the pinned W10-W12 model and backend")
    o = p["config"]["ollama"]
    if (o["model"] != "qwen3:4b" or o["think"] is not False or o["temperature"] != 0
            or o["seed"] != 42 or o["max_output_tokens"] != 256 or o["keep_alive"] != "10m"
            or o["connect_timeout_seconds"] != 2.0 or o["total_timeout_seconds"] != 30.0):
        raise ValueError("Original fixed product configuration must be retained")
    cases = w12.validate_corpus(p["corpus"], p["devices"], p["config"]["max_commands"])
    counts = Counter(c["category"] for c in cases)
    if len(cases) != 29 or counts != {"normal": 9, "ambiguous": 9, "illegal": 9, "deferred": 2}:
        raise ValueError("W13 must retain all29 frozen W12 cases")
    if p["corpus_file_sha256"] != CORPUS_SHA256 or signature(p["corpus"]) != p["corpus_content_sha256"]:
        raise ValueError("Frozen W12 oracle hash differs")
    if p["requests"] != requests(p["config"], p["devices"], p["schema"], cases):
        raise ValueError("Frozen87 requests differ from original product inputs/order")
    if p["acceptance_policy"] != acceptance_policy() or p["target_regression_ids"] != list(TARGET_IDS):
        raise ValueError("Frozen W13 acceptance policy differs")
    return p


def check_sources(p):
    w12.check_sources(p)
    if sha256(Path(p["corpus_path"])) != CORPUS_SHA256:
        raise ValueError("Literal W12 corpus was changed")


def prepare(args, client=None):
    if args.config_dir.resolve() != (ROOT / "configs").resolve() or args.corpus.resolve() != CORPUS.resolve():
        raise ValueError("Use the canonical product configuration and frozen W12 corpus")
    git = git_info()
    if git["status"]:
        raise ValueError("Prepare requires a clean committed child repository")
    config = w12.load_config(args.config_dir)
    devices, schema, corpus = (read_json(ROOT / name) for name in
        ("configs/devices.json", "schemas/agent_response.schema.json", "configs/evaluation/w12-home-intents-v1.json"))
    if sha256(args.corpus) != CORPUS_SHA256:
        raise ValueError("Do not retune or replace the frozen W12 corpus")
    cases = w12.validate_corpus(corpus, devices, config["max_commands"])
    client = client or MetadataClient(config["ollama"])
    metadata = snapshot(client, args.outdir / "identity")
    pinned = identity(metadata, config["ollama"]["model"], MODEL_DIGEST, SERVER_VERSION)
    p = {"schema_version": 1, "kind": "w13_frozen_intent_protocol", "state": "frozen", "created_at": now(),
         "expected_digest": MODEL_DIGEST, "expected_version": SERVER_VERSION, "model_identity": pinned,
         "config_dir": str(args.config_dir.resolve()), "corpus_path": str(args.corpus.resolve()),
         "config": config, "devices": devices, "schema": schema, "corpus": corpus,
         "corpus_file_sha256": CORPUS_SHA256, "corpus_content_sha256": signature(corpus),
         "requests": requests(config, devices, schema, cases), "sources_sha256": source_hashes(args.corpus, corpus),
         "prepared_git": git, "acceptance_policy": acceptance_policy(), "target_regression_ids": list(TARGET_IDS),
         "format_adapter": "canonical_contract_equivalent_disjoint_oneof_v1",
         "generation_calls": 0, "explicit_load_calls": 0, "explicit_unload_calls": 0,
         "reference": w12.REFERENCE,
         "scope": "Three rounds of raw-model-only requests, independent guarded outputs/error codes and four-rule baseline; no device execution"}
    validate_protocol(p)
    write_json(args.outdir / "protocol.json", p)
    return {"passed": True, "protocol": str(args.outdir / "protocol.json"), "generation_calls": 0,
            "rounds": ROUNDS, "cases": len(cases), "planned_calls": len(p["requests"])}


def analyze_generation(generation, case, p):
    analysis = w12.analyze_generation(generation, case, p)
    diagnosis = analysis["diagnosis"] or {}
    raw, guarded = diagnosis.get("parsed_result"), diagnosis.get("guard_result")
    typed_raw = typed_guard = None
    if diagnosis.get("product_parse_accepted"):
        # Read the existing typed result's explicit code, without changing W12's
        # historical normalizer/wire shape. These stages are pure; no model or
        # device is called a second time.
        typed_raw = parse_agent_response(generation["response"]["response"], max_commands=p["config"]["max_commands"])
        typed_guard = LocalLlmRequestGuard(p["devices"]).apply(
            PerceptionEvent(source=EventSource.TEXT, label=case["text"]), typed_raw)
        for typed, saved in ((typed_raw, raw), (typed_guard, guarded)):
            if saved is None or any(saved.get(k) != v for k, v in w12.normalized(typed).items()):
                raise ValueError("Typed error-code projection differs from existing guard result")
    rule = RuleBasedIntelligentPath(p["config"]["rules"]).route(PerceptionEvent(source=EventSource.TEXT, label=case["text"]))
    raw_stages = {"transport", "envelope", "model_json", "wire_contract", "raw_plan_validation", "oracle_raw"}
    raw_errors = [e for e in analysis["errors"] if e["stage"] in raw_stages]
    guarded_errors = [e for e in analysis["errors"] if e["stage"] not in raw_stages]
    code = lambda result: None if result is None else result_error_code(result.kind, getattr(result, "error_code", None))
    first_code = lambda errors: None if not errors else diagnostic_error_code(errors[0]["category"], errors[0].get("error_code"))
    codes = {"raw_result": code(typed_raw), "guarded_result": code(typed_guard), "rule_only_result": code(rule),
             "raw_evaluation": first_code(raw_errors), "guarded_evaluation": first_code(guarded_errors),
             "observed_stages": [{"stage": e["stage"], "category": e["category"],
                                   "error_code": diagnostic_error_code(e["category"], e.get("error_code"))}
                                  for e in analysis["errors"]]}
    for name in ("raw_result", "guarded_result", "rule_only_result", "raw_evaluation", "guarded_evaluation"):
        normalize_error_code(codes[name])
    analysis["error_codes"] = codes
    return analysis


def aggregate(records):
    result = w12.aggregate(records)
    for name in ("raw_result", "guarded_result", "rule_only_result", "raw_evaluation", "guarded_evaluation"):
        result[name + "_code_counts"] = dict(sorted(Counter(
            r["analysis"]["error_codes"][name] or "OK_OR_NOT_APPLICABLE" for r in records).items()))
    result["unique_raw_semantic_mismatch_ids"] = sorted(set(result["raw_semantic_mismatch_ids"]))
    result["unique_guarded_semantic_mismatch_ids"] = sorted(set(result["guarded_semantic_mismatch_ids"]))
    return result


def _semantic_value(value):
    return None if value is None else {"kind": value["kind"], "commands": value["commands"]}


def stability(records, p):
    result = []
    for case in p["corpus"]["cases"]:
        selected = [r for r in records if r["case_id"] == case["id"]]
        complete = len(selected) == ROUNDS and {r["round"] for r in selected} == set(range(ROUNDS))
        raw_values, guard_values, raw_texts, guard_codes = [], [], [], []
        for r in selected:
            a = r["analysis"]; d = a["diagnosis"] or {}
            raw_values.append(_semantic_value(d.get("parsed_result")))
            guard_values.append(_semantic_value(d.get("guard_result")))
            response = r["generation"].get("response")
            raw_texts.append(response.get("response") if isinstance(response, dict) else None)
            guard_codes.append(a["error_codes"]["guarded_result"])
        unique = lambda values: len({signature(v) for v in values})
        raw_ok = bool(complete and all(r["analysis"]["wire_contract_valid"] for r in selected))
        guard_ok = bool(complete and all(r["analysis"]["guarded_plan_valid"] for r in selected))
        result.append({"case_id": case["id"], "category": case["category"], "observed_rounds": len(selected),
            "raw_unique_semantic_outputs": unique(raw_values), "guarded_unique_semantic_outputs": unique(guard_values),
            "raw_semantic_stable": bool(raw_ok and unique(raw_values) == 1),
            "guarded_semantic_stable": bool(guard_ok and unique(guard_values) == 1),
            "raw_full_text_stable": bool(raw_ok and unique(raw_texts) == 1),
            "guarded_code_stable": bool(guard_ok and unique(guard_codes) == 1),
            "guarded_exact_matches": sum(r["analysis"]["guarded_semantic_match"]["passed"] for r in selected),
            "raw_exact_matches": sum(r["analysis"]["raw_semantic_match"]["passed"] for r in selected)})
    return {"case_count": len(result), "cases": result,
            "raw_semantic_stable_cases": sum(v["raw_semantic_stable"] for v in result),
            "guarded_semantic_stable_cases": sum(v["guarded_semantic_stable"] for v in result),
            "raw_full_text_stable_cases": sum(v["raw_full_text_stable"] for v in result),
            "guarded_code_stable_cases": sum(v["guarded_code_stable"] for v in result),
            "scope": "Only three observed repetitions; stable wrong output is not correctness; no future determinism guarantee"}


def run(args, client=None, transport=None):
    p = validate_protocol(read_json(args.protocol)); check_sources(p)
    git = git_info()
    if git["status"]:
        raise ValueError("Run requires a clean committed child repository")
    client = client or MetadataClient(p["config"]["ollama"])
    transport = transport or UrllibJsonTransport()
    write_json(args.outdir / "protocol.json", p); (args.outdir / "records").mkdir()
    status = {"kind": "w13_current_intent_pipeline", "started_at": now(), "code_git": git,
              "protocol_sha256": sha256(args.outdir / "protocol.json"), "completed_calls": 0,
              "evidence_integrity": False, "error": None, "device_execution_calls": 0,
              "explicit_load_calls": 0, "explicit_unload_calls": 0}
    first = None; failure = None
    fixtures = w12.diagnose_fixture_suite(p["devices"])
    write_json(args.outdir / "offline-diagnostic-fixtures.json", fixtures)
    if not all(f["matched"] for f in fixtures):
        raise ValueError("Offline injected fixture validation failed before generation")
    try:
        before = snapshot(client, args.outdir / "before")
        first = identity(before, p["config"]["ollama"]["model"], MODEL_DIGEST, SERVER_VERSION)
        if first != p["model_identity"]:
            raise ValueError("Prepared identity differs from current model/backend")
        with (args.outdir / "calls.jsonl").open("x", encoding="utf-8") as ledger:
            for request in p["requests"]:
                case = p["corpus"]["cases"][request["round_case_index"]]
                record = {key: request[key] for key in ("call_index", "round", "round_case_index", "case_id", "payload", "payload_sha256")}
                record.update(category=case["category"], text=case["text"], expected=case["expected"],
                              started_at=now(), model_calls=1, device_execution_calls=0)
                ledger.write(json.dumps({"event": "attempt", **record}, ensure_ascii=False) + "\n"); ledger.flush()
                generation = {"response": None, "error": None}; started = perf_counter()
                try:
                    generation["response"] = transport.post_json(p["config"]["ollama"]["endpoint"], request["payload"],
                        connect_timeout_seconds=p["config"]["ollama"]["connect_timeout_seconds"],
                        total_timeout_seconds=p["config"]["ollama"]["total_timeout_seconds"])
                except Exception as error:
                    generation["error"] = w12.transport_error(error)
                generation["client_final_wall_ms"] = (perf_counter() - started) * 1000
                analysis_error = None
                try:
                    analysis = analyze_generation(generation, case, p)
                except Exception as error:
                    # A checker bug is not a model error. Preserve the actual
                    # response before stopping, rather than losing that call.
                    analysis = None
                    analysis_error = {"type": type(error).__name__, "message": str(error)}
                record.update(generation=generation, analysis=analysis, finished_at=now())
                if analysis_error is not None:
                    record["analysis_error"] = analysis_error
                write_json(args.outdir / "records" / f"{request['call_index']:05d}.json", record)
                ledger.write(json.dumps({"event": "completed", "call_index": request["call_index"],
                                        "round": request["round"], "case_id": case["id"]}) + "\n"); ledger.flush()
                status["completed_calls"] += 1
                if analysis_error is not None:
                    raise ValueError("Classification failed; received generation retained, no retry: " + analysis_error["message"])
        check_sources(p); status["evidence_integrity"] = True
    except Exception as error:
        failure = {"type": type(error).__name__, "message": str(error)}
    finally:
        try:
            after = snapshot(client, args.outdir / "after")
            last = identity(after, p["config"]["ollama"]["model"], MODEL_DIGEST, SERVER_VERSION)
            if first is not None and first != last:
                raise ValueError("Model/backend identity drift during evaluation")
            check_sources(p)
        except Exception as error:
            failure = failure or {"type": type(error).__name__, "message": str(error)}
        status.update(error=failure, evidence_integrity=bool(status["evidence_integrity"] and failure is None), finished_at=now())
        write_json(args.outdir / "run-status.json", status)
        write_json(args.outdir / "raw-index.json", {"sha256": {str(f.relative_to(args.outdir)): sha256(f)
                   for f in sorted(args.outdir.rglob("*")) if f.is_file()}})
    summary = rebuild(args.outdir); write_reports(args.outdir, summary)
    return {"passed": summary["passed"], "outdir": str(args.outdir), "calls": status["completed_calls"],
            "raw_exact_matches": summary["model_raw_exact_matches"], "guarded_exact_matches": summary["guarded_exact_matches"],
            "total": summary["case_count"], "error": failure}


def rebuild(directory):
    w12.verify_artifacts(directory)
    p = validate_protocol(read_json(directory / "protocol.json")); check_sources(p)
    status = read_json(directory / "run-status.json")
    if status["protocol_sha256"] != sha256(directory / "protocol.json"):
        raise ValueError("Protocol differs from recorded hash")
    fixtures = read_json(directory / "offline-diagnostic-fixtures.json")
    if fixtures != w12.diagnose_fixture_suite(p["devices"]) or not all(f["matched"] for f in fixtures):
        raise ValueError("Offline injected fixture results differ from classification")
    for name in ("before", "after"):
        metadata = {key: read_json(directory / name / (key + ".json")) for key in ("version", "tags", "show", "ps")}
        if identity(metadata, p["config"]["ollama"]["model"], MODEL_DIGEST, SERVER_VERSION) != p["model_identity"]:
            raise ValueError("Frozen model/backend identity differs from raw metadata")
    records = [read_json(f) for f in sorted((directory / "records").glob("*.json"))]
    if [r["call_index"] for r in records] != list(range(len(records))) or len(records) > len(p["requests"]):
        raise ValueError("Duplicate/missing/reordered or excess call indices")
    ledger = [json.loads(line) for line in (directory / "calls.jsonl").read_text().splitlines()]
    if len(ledger) != 2 * len(records):
        raise ValueError("Unpaired generation attempt/completed ledger")
    for record, attempt, completed, request in zip(records, ledger[::2], ledger[1::2], p["requests"]):
        case = p["corpus"]["cases"][request["round_case_index"]]
        expected = {**request, "category": case["category"], "text": case["text"], "expected": case["expected"],
                    "model_calls": 1, "device_execution_calls": 0}
        for key, value in expected.items():
            if record.get(key) != value or attempt.get(key) != value:
                raise ValueError("Frozen schedule/case/payload differs from record/ledger")
        if attempt.get("event") != "attempt" or completed != {"event": "completed", "call_index": request["call_index"],
                                                               "round": request["round"], "case_id": case["id"]}:
            raise ValueError("Generation ledger pairing differs")
        if record["analysis"] != analyze_generation(record["generation"], case, p):
            raise ValueError("Saved analysis/code/score differs from raw classification")
    total = len(p["requests"]); result = aggregate(records)
    targets = [r for r in records if r["case_id"] in TARGET_IDS]
    complete = status["evidence_integrity"] and len(records) == total and status["completed_calls"] == total
    gates = {"evidence_integrity": bool(complete), "all_attempts_classified": len(records) == total,
             "all_model_responses_completed": result["model_completed_count"] == total,
             "all_wire_contracts_valid": result["wire_contract_valid_count"] == total,
             "all_guarded_plans_valid": result["guarded_plan_valid_count"] == total,
             "four_regressions_pass_all_rounds": len(targets) == ROUNDS * len(TARGET_IDS) and all(r["analysis"]["guarded_semantic_match"]["passed"] for r in targets),
             "no_guarded_unsafe_plans": result["guarded_unsafe_plan_count"] == 0,
             "all_guarded_semantics_match": result["guarded_exact_matches"] == total}
    return {"schema_version": 1, "kind": "w13_current_intent_pipeline", "passed": all(gates.values()),
            "mandatory_gates": gates, "acceptance_policy": p["acceptance_policy"], "code_git": status["code_git"],
            "model_identity": p["model_identity"], "protocol_sha256": status["protocol_sha256"],
            "raw_index_sha256": sha256(directory / "raw-index.json"), "corpus_content_sha256": p["corpus_content_sha256"],
            **result, "rounds": {str(n): aggregate([r for r in records if r["round"] == n]) for n in range(ROUNDS)},
            "by_category": {category: aggregate([r for r in records if r["category"] == category])
                            for category in ("normal", "ambiguous", "illegal", "deferred")},
            "regressions": {target: {"observed": sum(r["case_id"] == target for r in records),
                "raw_exact_matches": sum(r["case_id"] == target and r["analysis"]["raw_semantic_match"]["passed"] for r in records),
                "guarded_exact_matches": sum(r["case_id"] == target and r["analysis"]["guarded_semantic_match"]["passed"] for r in records)} for target in TARGET_IDS},
            "stability": stability(records, p), "raw_model_all_semantics_passed": result["model_raw_exact_matches"] == total,
            "guarded_all_semantics_passed": result["guarded_exact_matches"] == total,
            "offline_injected_fixture_suite": {"count": len(fixtures), "matched": sum(f["matched"] for f in fixtures),
                                               "evidence_kind": "intentional_injected_fixture_not_model_generation"},
            "generation_calls": len(records), "warmup_calls": 0, "device_execution_calls": 0, "error": status["error"],
            "limitations": ["Frozen29-case development suite repeated3times, not held-out generalization or future determinism proof.",
                "Raw-model, guarded and four-rule-only results remain separate; code projections are application diagnostics, not fields generated by the LLM.",
                "All87 model calls are forced for evaluation even if production preflight can reject a request without a model call.",
                "No Orchestrator execution/HA/ARM/scheduler/LoRA/new service or personal understanding acceptance.",
                "Nonstreaming complete HTTP wall/server counts are diagnostics, not TTFT or a repeated performance Benchmark.",
                "Unsafe-plan count has the W12 narrow definition, not comprehensive production safety acceptance."]}


def write_reports(directory, summary):
    write_json(directory / "summary.json", summary)
    lines = ["# W13 intent pipeline evaluation", "", f"Mandatory gates passed: {summary['passed']}",
             f"Raw / guarded / four-rule exact: {summary['model_raw_exact_matches']} / {summary['guarded_exact_matches']} / {summary['rule_only_exact_matches']} of {summary['case_count']} observations.", "",
             "| Round | n | raw exact | guarded exact | four-rule exact | guarded unsafe |", "|---|---:|---:|---:|---:|---:|"]
    for n, result in summary["rounds"].items():
        lines.append(f"| {int(n)+1} | {result['case_count']} | {result['model_raw_exact_matches']} | {result['guarded_exact_matches']} | {result['rule_only_exact_matches']} | {result['guarded_unsafe_plan_count']} |")
    s = summary["stability"]
    lines += ["", f"Observed semantic stability raw/guarded: {s['raw_semantic_stable_cases']} / {s['guarded_semantic_stable_cases']} of {s['case_count']} cases; prose stability and code stability are separate in summary.json.", "", *["- " + t for t in summary["limitations"]]]
    with (directory / "report.md").open("x", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("prepare", help="Freeze87 literal requests/identity; metadata only, no generation")
    p.add_argument("--config-dir", type=Path, default=ROOT / "configs")
    p.add_argument("--corpus", type=Path, default=CORPUS)
    p.add_argument("--outdir", type=Path, required=True)
    p = commands.add_parser("run", help="Exactly87 raw model calls; no device execution or hidden retry")
    p.add_argument("--protocol", type=Path, required=True); p.add_argument("--outdir", type=Path, required=True)
    p = commands.add_parser("report", help="Validate hashes and rebuild offline into a fresh directory")
    p.add_argument("--run-dir", type=Path, required=True); p.add_argument("--outdir", type=Path, required=True)
    args = parser.parse_args(argv); args.outdir = args.outdir.resolve()
    if args.outdir.exists():
        parser.error("Output directory exists; choose a fresh directory")
    args.outdir.mkdir(parents=True)
    try:
        if args.command == "prepare": result = prepare(args)
        elif args.command == "run": result = run(args)
        else:
            result = rebuild(args.run_dir.resolve()); write_reports(args.outdir, result)
    except Exception as error:
        write_json(args.outdir / "error.json", {"type": type(error).__name__, "message": str(error)})
        print(f"W13 rejected: {type(error).__name__}: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result if args.command != "report" else {"passed": result["passed"], "recomputed": str(args.outdir)}, ensure_ascii=False, indent=2))
    return 0 if result.get("passed", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
