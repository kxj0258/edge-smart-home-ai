"""Explicit, frozen streaming Ollama benchmark; product configuration is read-only."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from inspect_llm_backend import MetadataClient, identity, load_config, sha256, snapshot, write_json
from edge_smart_home.agent.contract import parse_agent_response
from edge_smart_home.agent.local_llm import build_agent_prompt
from edge_smart_home.common import EventSource, PerceptionEvent
from edge_smart_home.device.mock import MockDeviceBackend

MODEL_DIGEST = "359d7dd4bcdab3d86b87d73ac27966f4dbb9f5efdfcc75d34a8764a09474fae7"
SERVER_VERSION = "0.40.2"
OFFICIAL_COMMIT = "b061384d90ff455462bc32745dd0de479de717a3"
REFERENCE = "https://github.com/ollama/ollama/blob/" + OFFICIAL_COMMIT + "/api/types.go"


def now():
    return datetime.now(timezone.utc).isoformat()


def signature(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def git_info():
    def call(*args):
        return subprocess.run(["git", "-C", str(ROOT), *args], check=True, capture_output=True, text=True).stdout.strip()
    return {"commit": call("rev-parse", "HEAD"), "status": call("status", "--short")}


def source_hashes():
    paths = ["scripts/benchmark_llm.py", "scripts/inspect_llm_backend.py",
             "src/edge_smart_home/agent/stream_metrics.py", "src/edge_smart_home/common/llm_resources.py",
             "src/edge_smart_home/agent/local_llm.py", "src/edge_smart_home/agent/contract.py",
             "src/edge_smart_home/device/mock.py", "configs/agent.json", "configs/devices.json",
             "configs/llm-model-source-v1.json", "schemas/agent_response.schema.json"]
    return {name: sha256(ROOT / name) for name in paths}


def check_sources(protocol):
    if source_hashes() != protocol["sources_sha256"]:
        raise ValueError("Frozen benchmark/model/configuration sources changed; prepare a new protocol")
    reference = protocol.get("references", {}).get("additional_evidence")
    if reference and sha256(Path(reference["path"])) != reference["sha256"]:
        raise ValueError("Thinking/protocol selection evidence changed")


def base_payload(config_dir):
    config = load_config(config_dir)
    devices = read_json(config_dir / "devices.json")
    states = MockDeviceBackend(devices).list_states()
    schema = read_json(config_dir.parent / "schemas/agent_response.schema.json")
    prompt = build_agent_prompt(PerceptionEvent(source=EventSource.TEXT, label="你能做什么"),
                                devices, states, schema, config["max_commands"])
    o = config["ollama"]
    return config, {"model": o["model"], "prompt": prompt, "format": schema, "stream": True,
                    "think": o["think"], "keep_alive": o["keep_alive"],
                    "options": {"temperature": o["temperature"], "seed": o["seed"],
                                "num_predict": o["max_output_tokens"]}}


def validate_protocol(p, *, formal=False):
    if p.get("schema_version") != 1 or p.get("kind") != "w11_streaming_protocol":
        raise ValueError("Unknown protocol schema")
    if formal and p.get("state") != "frozen": raise ValueError("Formal run requires a frozen protocol")
    contexts = p.get("contexts")
    if (not isinstance(contexts, list) or len(contexts) != 2 or len(set(contexts)) != 2
            or any(type(n) is not int or not 512 <= n <= 262144 for n in contexts)):
        raise ValueError("Exactly two distinct explicit context capacities are required")
    for key, low in (("rounds", 3), ("samples_per_context_per_round", 20), ("warmup_per_block", 1)):
        if type(p.get(key)) is not int or p[key] < low: raise ValueError("Invalid frozen sampling count: " + key)
    payload = p["base_payload"]; options = payload["options"]
    if payload.get("stream") is not True or type(payload.get("think")) is not bool:
        raise ValueError("stream=true and explicit boolean think are required")
    if payload.get("model") != "qwen3:4b" or p["expected_digest"] != MODEL_DIGEST or p["expected_version"] != SERVER_VERSION:
        raise ValueError("W11 must reuse the W10 fixed model/server identity")
    if type(options.get("num_thread")) is not int or not 1 <= options["num_thread"] <= 256:
        raise ValueError("An explicit fixed positive thread count is required")
    if options.get("temperature") != 0 or type(options.get("seed")) is not int:
        raise ValueError("Deterministic temperature/seed must be explicit")
    if type(options.get("num_predict")) is not int or not 1 <= options["num_predict"] <= 4096:
        raise ValueError("Invalid output token budget")
    if (not isinstance(payload.get("prompt"), str) or not payload["prompt"].strip()
            or signature(payload) != p.get("base_payload_sha256")):
        raise ValueError("Literal prompt/payload hash differs")
    if p.get("cache_cohort") != "warm-resident-repeat-prefix":
        raise ValueError("Unsupported cache cohort")
    if p.get("expected_prompt_eval_count") != 3752: raise ValueError("Expected fixed W10 input count is 3752")
    if p.get("comparison") != "context-capacity-same-literal-input":
        raise ValueError("This protocol supports capacity comparisons with the same literal inputs")
    for name in ("connect_timeout_seconds", "total_timeout_seconds", "memory_interval_seconds"):
        n = p[name]
        if isinstance(n, bool) or not isinstance(n, (int, float)) or not math.isfinite(n) or n <= 0:
            raise ValueError("Invalid timeout/sampling interval")
    if p["total_timeout_seconds"] > 600 or p["connect_timeout_seconds"] > 30:
        raise ValueError("Timeout exceeds bounded stream client support")
    if p["memory_interval_seconds"] < .01: raise ValueError("RAM sampler interval must be at least 10ms")
    return p


def prepare(args, client=None):
    config, payload = base_payload(args.config_dir)
    payload["think"] = args.think == "true"
    payload["options"]["num_thread"] = args.num_thread
    if args.num_predict is not None: payload["options"]["num_predict"] = args.num_predict
    client = client or MetadataClient(config["ollama"])
    before = snapshot(client, args.outdir / "identity")
    pinned = identity(before, payload["model"], MODEL_DIGEST, SERVER_VERSION)
    references = {"official_source_commit": OFFICIAL_COMMIT, "metrics_and_options": REFERENCE,
                  "reuse": "Adapt official HTTP streaming/counting API; reuse existing W10 identity and product prompt/schema. No vendor source copied."}
    if args.references:
        references["additional_evidence"] = {"path": str(args.references.resolve()), "sha256": sha256(args.references)}
    p = {"schema_version": 1, "kind": "w11_streaming_protocol", "state": "draft", "created_at": now(),
         "expected_digest": MODEL_DIGEST, "expected_version": SERVER_VERSION, "model_identity": pinned,
         "config_dir": str(args.config_dir.resolve()), "endpoint": config["ollama"]["endpoint"],
         "config_unchanged": True, "base_payload": payload, "base_payload_sha256": signature(payload),
         "contexts": args.contexts, "comparison": "context-capacity-same-literal-input",
         "expected_prompt_eval_count": 3752,
         "cache_cohort": "warm-resident-repeat-prefix", "rounds": args.rounds,
         "samples_per_context_per_round": args.samples_per_context, "warmup_per_block": args.warmup,
         "connect_timeout_seconds": config["ollama"]["connect_timeout_seconds"],
         "total_timeout_seconds": args.timeout, "memory_interval_seconds": args.memory_interval,
         "thinking_strategy": args.thinking_strategy,
         "thinking_note": "Request and observed thinking are separate; advertised support does not predict the observed false-request outcome.",
         "request_order": "Single sequential client; alternating context block order by round; fixed sample order, no concurrency/retries/adaptive tuning.",
         "prefix_policy": "No cache clearing or unload. Identical prefix is repeated after two warmups. Counters decide observed reuse; no prefix-cold claim.",
         "quantile_method": "Linear interpolation at (N-1)*q; pooled raw samples, never average per-round P95.",
         "sources_sha256": source_hashes(), "references": references, "prepared_git": git_info(),
         "generation_calls": 0, "explicit_load_calls": 0, "explicit_unload_calls": 0}
    validate_protocol(p); write_json(args.outdir / "draft.json", p)
    return {"draft": str(args.outdir / "draft.json"), "generation_calls": 0, "passed": True}


def freeze(args):
    p = validate_protocol(read_json(args.draft)); check_sources(p)
    pilot_dir = args.pilot_dir.resolve()
    pilot = rebuild(pilot_dir)
    if pilot != read_json(pilot_dir / "summary.json"): raise ValueError("Pilot summary differs from raw reconstruction")
    if pilot.get("kind") != "pilot" or pilot.get("passed") is not True:
        raise ValueError("Freeze requires a successful explicit pilot; failed candidates remain recorded")
    if pilot["protocol_sha256"] != sha256(args.draft): raise ValueError("Pilot was not run with this literal draft")
    verify_artifacts(pilot_dir)
    p = dict(p, state="frozen", frozen_at=now(), pilot={"directory": str(pilot_dir),
        "summary_sha256": sha256(pilot_dir / "summary.json"), "raw_index_sha256": sha256(pilot_dir / "raw-index.json"),
        "protocol_sha256": sha256(args.draft)}, frozen_git=git_info())
    validate_protocol(p, formal=True); write_json(args.out, p)
    return {"protocol": str(args.out), "sha256": sha256(args.out), "generation_calls": 0, "passed": True}


def schedule(p, *, pilot_samples=None):
    rounds = 1 if pilot_samples is not None else p["rounds"]
    count = pilot_samples if pilot_samples is not None else p["samples_per_context_per_round"]
    result = []
    for round_index in range(rounds):
        contexts = p["contexts"] if round_index % 2 == 0 else list(reversed(p["contexts"]))
        for context in contexts:
            for phase, n in (("warmup", p["warmup_per_block"]), ("sample", count)):
                for sample_index in range(n):
                    result.append({"call_index": len(result), "round": round_index, "context": context,
                                   "phase": phase, "sample_index": sample_index})
    return result


def request_payload(p, row):
    payload = json.loads(json.dumps(p["base_payload"]))
    payload["options"]["num_ctx"] = row["context"]
    return payload


def allocated_model(ps, p, context):
    rows = [r for r in ps.get("models", []) if r.get("name") == p["base_payload"]["model"] or r.get("model") == p["base_payload"]["model"]]
    if len(rows) != 1: raise ValueError("Expected model must actually be resident after generation")
    row = rows[0]
    if row.get("digest", "").removeprefix("sha256:") != p["expected_digest"]:
        raise ValueError("Resident model digest drift")
    if type(row.get("context_length")) is not int or row["context_length"] != context:
        raise ValueError("/api/ps allocated context differs from requested capacity")
    return row


def validate_sample(record, p):
    stream = record["stream"]; final = stream.get("final") or {}; timings = stream.get("timings") or {}
    if stream.get("passed") is not True: raise ValueError("Streaming request failed")
    if stream.get("request", {}).get("payload") != record["payload"]:
        raise ValueError("Actual stream payload differs from planned payload")
    accumulated = {"response": [], "thinking": []}; first = {}
    previous = stream["t0_ns"]; last_done = None
    for index, event in enumerate(stream["events"]):
        if event["index"] != index: raise ValueError("Raw event indices differ")
        if not event["raw_line"].strip(): continue
        value = json.loads(event["raw_line"])
        if value != event["event"]: raise ValueError("Parsed event differs from retained NDJSON")
        stamp = event["parsed_ns"]
        received = event["received_ns"]
        if type(received) is not int or not stream["t0_ns"] <= received <= stamp:
            raise ValueError("Invalid received/parsed timestamp relationship")
        if type(stamp) is not int or stamp < previous or stamp > stream["finish_ns"]:
            raise ValueError("Invalid event timestamp order")
        if last_done is not None: raise ValueError("JSON event after done")
        previous = stamp; elapsed = (stamp-stream["t0_ns"])/1e6
        if event["elapsed_ms"] != elapsed: raise ValueError("Event elapsed time differs from raw timestamps")
        first.setdefault("first_event_ms", elapsed)
        for key in accumulated:
            chunk = value.get(key, ""); accumulated[key].append(chunk)
            if chunk: first.setdefault("first_" + key + "_ms", elapsed)
        if value.get("done") is True:
            if last_done is not None: raise ValueError("Duplicate done event")
            last_done = value; first["done_ms"] = elapsed
    if last_done != final: raise ValueError("Final counters differ from raw done event")
    for key, chunks in accumulated.items():
        if "".join(chunks) != stream.get(key + "_text", ""): raise ValueError("Accumulated stream text differs from raw events")
    for key in ("first_event_ms", "first_response_ms", "first_thinking_ms", "done_ms"):
        if timings.get(key) != first.get(key): raise ValueError("Client timing differs from raw event timestamps: " + key)
    if timings.get("finish_ms") != (stream["finish_ns"]-stream["t0_ns"])/1e6:
        raise ValueError("Final wall time differs from raw timestamp")
    if final.get("done") is not True or final.get("done_reason") != "stop": raise ValueError("Output is incomplete or truncated")
    if final.get("model") != p["base_payload"]["model"]: raise ValueError("Response model drift")
    if not isinstance(timings.get("first_response_ms"), (int, float)) or not stream.get("response_text", "").strip():
        raise ValueError("Client visible-response TTFT was not observed")
    text = stream["response_text"]
    result = parse_agent_response(text, max_commands=8)
    if result.kind.value != "response" or result.commands:
        raise ValueError("Fixed read-only capability query must return response with empty commands")
    if p["base_payload"]["think"] is False and (stream.get("thinking_text") or "<think" in text.lower() or "</think" in text.lower()):
        raise ValueError("Observed thinking differs from frozen false-request output policy")
    for key in ("prompt_eval_count", "prompt_eval_cached_count", "prompt_eval_duration", "load_duration", "eval_count", "eval_duration"):
        if type(final.get(key)) is not int or not 0 <= final[key] <= 2**63-1: raise ValueError("Required server counter missing/invalid: " + key)
    if p["cache_cohort"] == "warm-resident-repeat-prefix" and final["prompt_eval_count"] != p["expected_prompt_eval_count"]:
        raise ValueError("Actual input token count differs from fixed W10 input")
    if not 0 <= final["prompt_eval_cached_count"] <= final["prompt_eval_count"]:
        raise ValueError("Invalid prefix cache count")
    if not final["eval_count"] or not final["eval_duration"]: raise ValueError("Server output TPS cannot be calculated")
    if final["prompt_eval_count"] + p["base_payload"]["options"]["num_predict"] > record["context"]:
        raise ValueError("Prompt plus frozen output budget does not fit allocated context")
    allocated_model(record["ps_after"], p, record["context"])
    if record["phase"] == "sample":
        allocated_model(record["ps_before"], p, record["context"])
        from edge_smart_home.common.llm_resources import summarize_memory
        memory = record["memory"]; summary = memory["summary"]
        if summary != summarize_memory(memory["samples"]): raise ValueError("RAM summary differs from raw samples")
        if summary["sample_count"] < 2 or summary["incomplete_rss_samples"]:
            raise ValueError("Measured request lacks complete sampled RSS evidence")
        samples = memory["samples"]
        if (samples[0]["monotonic_ns"] + samples[0]["sample_duration_ns"] > stream["t0_ns"]
                or samples[-1]["monotonic_ns"] < stream["finish_ns"]):
            raise ValueError("RAM sampling does not cover the timed stream interval")
        for sample in memory["samples"]:
            roles = {process["role"] for process in sample["processes"]}
            if not {"server", "runner"}.issubset(roles): raise ValueError("Measured RAM sample lacks server/runner")
            if sample.get("process_tree_discovery_complete") is not True:
                raise ValueError("Measured RAM process discovery is incomplete")
            if type(sample["sum_rss_bytes"]) is not int or sample["sum_rss_bytes"] <= 0:
                raise ValueError("RAM sample must contain positive measured RSS")


def execute(args, *, client=None, streamer=None, sampler_factory=None):
    p = validate_protocol(read_json(args.protocol), formal=args.command == "run"); check_sources(p)
    git = git_info()
    if args.command == "run" and git["status"]: raise ValueError("Formal run requires a clean child repository")
    if streamer is None:
        from edge_smart_home.agent.stream_metrics import stream_generate
        streamer = stream_generate
    if sampler_factory is None:
        from edge_smart_home.common.llm_resources import MemorySampler
        sampler_factory = MemorySampler
    client = client or MetadataClient(load_config(Path(p["config_dir"]))["ollama"])
    write_json(args.outdir / "protocol.json", p)
    (args.outdir / "records").mkdir()
    planned = schedule(p, pilot_samples=args.pilot_samples if args.command == "pilot" else None)
    write_json(args.outdir / "schedule.json", planned)
    status = {"kind": args.command, "started_at": now(), "code_git": git,
              "protocol_sha256": sha256(args.outdir / "protocol.json"), "completed_calls": 0, "passed": False,
              "pilot_samples": args.pilot_samples if args.command == "pilot" else None,
              "explicit_load_calls": 0, "explicit_unload_calls": 0}
    failure = None
    first = None
    try:
        before = snapshot(client, args.outdir / "before")
        first = identity(before, p["base_payload"]["model"], p["expected_digest"], p["expected_version"])
        if first != p["model_identity"]: raise ValueError("Model/API identity differs from prepared identity")
        with (args.outdir / "calls.jsonl").open("x", encoding="utf-8") as ledger:
            for row in planned:
                payload = request_payload(p, row)
                record = dict(row, started_at=now(), payload=payload, payload_sha256=signature(payload),
                              operation="generate", cache_cohort=p["cache_cohort"])
                record["ps_before"] = client.get_json("/api/ps")
                if row["phase"] == "sample": allocated_model(record["ps_before"], p, row["context"])
                # Flush the attempt before calling; never silently retry a partially observed call.
                ledger.write(json.dumps({"event": "attempt", **record}, ensure_ascii=False) + "\n"); ledger.flush()
                error = None
                sampler = sampler_factory(interval_seconds=p["memory_interval_seconds"])
                try:
                    with sampler:
                        record["stream"] = streamer(p["endpoint"], payload, expected_model=payload["model"],
                            connect_timeout_seconds=p["connect_timeout_seconds"], total_timeout_seconds=p["total_timeout_seconds"])
                except Exception as exc:
                    record["stream"] = getattr(exc, "result", {})
                    error = {"type": type(exc).__name__, "message": str(exc)}
                finally:
                    try: record["memory"] = sampler.result()
                    except Exception as exc:
                        record["memory"] = {"error": {"type": type(exc).__name__, "message": str(exc)}}
                        error = error or record["memory"]["error"]
                try:
                    record["ps_after"] = client.get_json("/api/ps")
                    validate_sample(record, p)
                    if error: raise ValueError(error["message"])
                except Exception as exc:
                    error = error or {"type": type(exc).__name__, "message": str(exc)}
                record["passed"] = error is None; record["error"] = error; record["finished_at"] = now()
                write_json(args.outdir / "records" / f"{row['call_index']:05d}.json", record)
                status["completed_calls"] += 1
                ledger.write(json.dumps({"event": "completed", "call_index": row["call_index"], "passed": record["passed"]}) + "\n"); ledger.flush()
                if error: raise ValueError("Call failed; raw evidence retained, no retry: " + error["message"])
        check_sources(p)
        status["passed"] = True
    except Exception as exc:
        failure = {"type": type(exc).__name__, "message": str(exc)}
    finally:
        try:
            after = snapshot(client, args.outdir / "after")
            last = identity(after, p["base_payload"]["model"], p["expected_digest"], p["expected_version"])
            if first is not None and last != first: raise ValueError("Model/API identity drift during run")
            check_sources(p)
        except Exception as exc:
            failure = failure or {"type": type(exc).__name__, "message": str(exc)}
        status["passed"] = bool(status["passed"] and failure is None)
        status["error"] = failure; status["finished_at"] = now()
        write_json(args.outdir / "run-status.json", status)
        write_json(args.outdir / "raw-index.json", {"sha256": {str(f.relative_to(args.outdir)): sha256(f)
                   for f in sorted(args.outdir.rglob("*")) if f.is_file()}})
    summary = rebuild(args.outdir)
    write_reports(args.outdir, summary)
    return {"outdir": str(args.outdir), "passed": summary["passed"], "calls": status["completed_calls"],
            "kind": args.command, "error": failure}


def quantiles(values):
    values = sorted(v for v in values if v is not None)
    if not values: return {"n": 0, "p50": None, "p95": None, "min": None, "max": None}
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in values):
        raise ValueError("Non-finite sample metric")
    def at(q):
        i = (len(values)-1)*q; lo = math.floor(i); hi = math.ceil(i)
        return values[lo] + (values[hi]-values[lo])*(i-lo)
    return {"n": len(values), "p50": at(.5), "p95": at(.95), "min": values[0], "max": values[-1]}


def verify_artifacts(directory):
    index = read_json(directory / "raw-index.json")["sha256"]
    for name, digest in index.items():
        path = (directory / name).resolve()
        if not path.is_relative_to(directory.resolve()) or not path.is_file() or sha256(path) != digest:
            raise ValueError("Raw artifact missing or changed: " + name)
    actual_records = {str(f.relative_to(directory)) for f in (directory / "records").glob("*.json")}
    if actual_records != {k for k in index if k.startswith("records/")}:
        raise ValueError("Unindexed raw request record")


def group_metrics(records):
    def time(key): return quantiles([r["stream"]["timings"].get(key) for r in records])
    def count(key): return quantiles([r["stream"]["final"].get(key) for r in records])
    def rate():
        return quantiles([r["stream"]["final"]["eval_count"]*1e9/r["stream"]["final"]["eval_duration"] for r in records])
    def ram(key): return quantiles([(r.get("memory") or {}).get("summary", {}).get(key) for r in records])
    return {"sample_count": len(records), "client_visible_ttft_ms": time("first_response_ms"),
            "client_first_thinking_ms": time("first_thinking_ms"), "client_first_event_ms": time("first_event_ms"),
            "client_final_wall_ms": time("finish_ms"), "server_generation_tps": rate(),
            "server_prompt_eval_count": count("prompt_eval_count"), "server_prompt_cached_count": count("prompt_eval_cached_count"),
            "server_prompt_eval_ms": quantiles([r["stream"]["final"]["prompt_eval_duration"]/1e6 for r in records]),
            "server_load_ms": quantiles([r["stream"]["final"]["load_duration"]/1e6 for r in records]),
            "server_eval_count": count("eval_count"), "sampled_peak_sum_rss_bytes": ram("peak_sum_rss_bytes"),
            "sampled_pre_sum_rss_bytes": ram("pre_sum_rss_bytes"), "sampled_post_sum_rss_bytes": ram("post_sum_rss_bytes"),
            "sampled_peak_sum_pss_bytes": ram("peak_sum_pss_bytes"),
            "incomplete_rss_samples": sum((r.get("memory") or {}).get("summary", {}).get("incomplete_rss_samples", 0) for r in records),
            "incomplete_pss_samples": sum((r.get("memory") or {}).get("summary", {}).get("incomplete_pss_samples", 0) for r in records),
            "memory_max_sample_gap_ms": ram("max_sample_gap_ms"),
            "observed_nonempty_thinking_samples": sum(bool(r["stream"].get("thinking_text")) for r in records),
            "memory_source_missing_samples": sum((r.get("memory") or {}).get("summary", {}).get("peak_sum_rss_bytes") is None for r in records)}


def rebuild(directory):
    verify_artifacts(directory)
    p = validate_protocol(read_json(directory / "protocol.json"))
    status = read_json(directory / "run-status.json"); planned = read_json(directory / "schedule.json")
    if status["protocol_sha256"] != sha256(directory / "protocol.json"):
        raise ValueError("Run status protocol hash differs from literal protocol artifact")
    expected_schedule = schedule(p, pilot_samples=status["pilot_samples"] if status["kind"] == "pilot" else None)
    if planned != expected_schedule: raise ValueError("Recorded schedule differs from frozen sampling plan")
    records = [read_json(f) for f in sorted((directory / "records").glob("*.json"))]
    if [r["call_index"] for r in records] != list(range(len(records))):
        raise ValueError("Request indices are duplicated, missing, or reordered")
    ledger = [json.loads(line) for line in (directory / "calls.jsonl").read_text().splitlines()]
    if len(ledger) != len(records)*2: raise ValueError("Unpaired generation intent/completion in call ledger")
    for row, attempt, completed in zip(records, ledger[::2], ledger[1::2]):
        if attempt.get("event") != "attempt" or any(attempt.get(k) != row[k] for k in planned[row["call_index"]]):
            raise ValueError("Generation attempt ledger differs from request record")
        if attempt.get("payload") != row["payload"] or completed != {"event": "completed", "call_index": row["call_index"], "passed": row["passed"]}:
            raise ValueError("Generation call ledger differs from raw payload/result")
    before = {name: read_json(directory / "before" / (name + ".json")) for name in ("version", "tags", "show", "ps")}
    after = {name: read_json(directory / "after" / (name + ".json")) for name in ("version", "tags", "show", "ps")}
    for value in (before, after):
        if identity(value, p["base_payload"]["model"], p["expected_digest"], p["expected_version"]) != p["model_identity"]:
            raise ValueError("Raw before/after metadata differs from prepared identity")
    valid = []
    for row in records:
        expected = planned[row["call_index"]]
        if any(row.get(k) != v for k, v in expected.items()): raise ValueError("Raw request order differs from frozen schedule")
        if row["payload"] != request_payload(p, row): raise ValueError("Raw payload differs from frozen request")
        if row.get("passed"):
            validate_sample(row, p)
            if row["phase"] == "sample": valid.append(row)
    complete = len(records) == len(planned) and all(r.get("passed") for r in records)
    groups = {}
    for context in p["contexts"]:
        selected = [r for r in valid if r["context"] == context]
        groups[str(context)] = {"pooled": group_metrics(selected), "rounds": {
            str(n): group_metrics([r for r in selected if r["round"] == n]) for n in sorted({r["round"] for r in selected})}}
    return {"schema_version": 1, "kind": status["kind"], "passed": bool(status["passed"] and complete),
            "protocol_sha256": status["protocol_sha256"], "raw_index_sha256": sha256(directory / "raw-index.json"),
            "code_git": status["code_git"], "model_identity": p["model_identity"], "comparison": p["comparison"],
            "cache_cohort": p["cache_cohort"], "thinking_request": p["base_payload"]["think"],
            "thinking_strategy": p["thinking_strategy"], "options": p["base_payload"]["options"],
            "planned_calls": len(planned), "completed_calls": len(records), "measured_samples": len(valid),
            "warmups_excluded": sum(r["phase"] == "warmup" for r in records), "groups": groups,
            "error": status.get("error"), "limitations": [
                "Context capacities are compared with identical literal input sets, not two actual input token lengths.",
                "Sequential reused backend; context switches can reload a runner. Warmup samples are excluded; no disk-cold claim.",
                "Prefix reuse is measured by explicit server counters; loaded /ps allocations are not process RAM or measured VRAM peaks.",
                "Server output count may include thinking; server TPS is neither visible-text TPS nor client token throughput.",
                "RSS is a sampled process aggregate and may double-count shared pages; peak between samples may be missed. PSS can be unavailable.",
                "Per-round and pooled quantiles use raw samples; no cross-boundary historical speedup or personal understanding acceptance."]}


def write_reports(directory, summary):
    write_json(directory / "summary.json", summary)
    lines = ["# W11 streaming benchmark", "", f"Passed: {summary['passed']}; kind: {summary['kind']}; samples: {summary['measured_samples']}",
             f"Cache cohort: `{summary['cache_cohort']}`; think request: `{summary['thinking_request']}`.", "",
             "| allocated context | n | client visible TTFT P50/P95 ms | server TPS P50/P95 | sampled RSS peak P50/P95 MiB | prompt/cached P50 |", "|---|---:|---:|---:|---:|---:|"]
    def pair(value, scale=1):
        return "unavailable" if value["p50"] is None else f"{value['p50']/scale:.3f} / {value['p95']/scale:.3f}"
    for context, group in summary["groups"].items():
        m = group["pooled"]
        lines.append(f"| {context} | {m['sample_count']} | {pair(m['client_visible_ttft_ms'])} | {pair(m['server_generation_tps'])} | {pair(m['sampled_peak_sum_rss_bytes'], 1024**2)} | {m['server_prompt_eval_count']['p50']} / {m['server_prompt_cached_count']['p50']} |")
    lines += ["", "All warmups are excluded. Raw per-round distributions and thinking TTFT are in summary.json.", "", *["- " + text for text in summary["limitations"]]]
    with (directory / "report.md").open("x", encoding="utf-8") as stream: stream.write("\n".join(lines) + "\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__); commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("prepare", help="Read metadata and save literal draft; no model generation")
    p.add_argument("--config-dir", type=Path, default=ROOT / "configs"); p.add_argument("--outdir", type=Path, required=True)
    p.add_argument("--contexts", nargs=2, type=int, default=[4096, 8192]); p.add_argument("--think", choices=("true", "false"), required=True)
    p.add_argument("--thinking-strategy", required=True); p.add_argument("--num-thread", type=int, required=True)
    p.add_argument("--num-predict", type=int); p.add_argument("--rounds", type=int, default=3)
    p.add_argument("--samples-per-context", type=int, default=20); p.add_argument("--warmup", type=int, default=2)
    p.add_argument("--timeout", type=float, default=120); p.add_argument("--memory-interval", type=float, default=.05)
    p.add_argument("--references", type=Path)
    p = commands.add_parser("freeze", help="Bind a successful explicit pilot; no generation")
    p.add_argument("--draft", type=Path, required=True); p.add_argument("--pilot-dir", type=Path, required=True); p.add_argument("--out", type=Path, required=True)
    for command in ("pilot", "run"):
        p = commands.add_parser(command); p.add_argument("--protocol", type=Path, required=True); p.add_argument("--outdir", type=Path, required=True)
        if command == "pilot": p.add_argument("--pilot-samples", type=int, default=1)
    p = commands.add_parser("report", help="Verify raw hashes and rebuild into a fresh directory; no service calls")
    p.add_argument("--run-dir", type=Path, required=True); p.add_argument("--outdir", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "pilot" and not 1 <= args.pilot_samples <= 5: parser.error("Pilot sample count must be within [1,5]")
    if args.command == "freeze":
        if args.out.exists(): parser.error("Frozen protocol output exists")
    else:
        args.outdir = args.outdir.resolve()
        if args.outdir.exists(): parser.error("Output directory exists; choose a fresh directory")
        args.outdir.mkdir(parents=True)
    try:
        if args.command == "prepare": result = prepare(args)
        elif args.command == "freeze": result = freeze(args)
        elif args.command == "report":
            result = rebuild(args.run_dir.resolve()); write_reports(args.outdir, result)
        else: result = execute(args)
    except Exception as error:
        if hasattr(args, "outdir"): write_json(args.outdir / "error.json", {"type": type(error).__name__, "message": str(error)})
        print(f"W11 rejected: {type(error).__name__}: {error}", file=sys.stderr); return 2
    print(json.dumps(result if args.command != "report" else {"passed": result["passed"], "recomputed": str(args.outdir)}, ensure_ascii=False, indent=2))
    return 0 if result.get("passed", True) else 1


if __name__ == "__main__": raise SystemExit(main())
