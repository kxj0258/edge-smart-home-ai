"""Inspect the configured local Ollama identity; optionally make one planner-only smoke call.

This is a bounded W10 inspection, not a latency benchmark. The unchanged
product transport is nonstreaming, so client TTFT cannot be observed here.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import http.client
import json
import math
from pathlib import Path
import re
import subprocess
import sys
from time import monotonic, perf_counter
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from edge_smart_home.agent.local_llm import (
    LocalLlmIntelligentPath, UrllibJsonTransport, LlmTransportError,
    MAX_HTTP_RESPONSE_BYTES, _validate_local_http_url, _set_socket_timeout,
)
from edge_smart_home.common import EventSource, PerceptionEvent
from edge_smart_home.device.mock import MockDeviceBackend


SMOKE_QUERY = "你能做什么"
REFERENCE = "https://github.com/ollama/ollama/blob/v0.40.2/api/types.go"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def digest_value(value: str) -> str:
    if not isinstance(value, str): raise ValueError("Model digest must be a string")
    result = value.removeprefix("sha256:")
    if not re.fullmatch(r"[0-9a-f]{64}", result): raise ValueError("Model digest must contain 64 lowercase hex digits")
    return result


def load_config(directory: Path, config_path: Path | None = None) -> dict:
    config_path = (config_path or directory / "agent.json").resolve()
    if not config_path.is_file() or config_path.stat().st_size > MAX_HTTP_RESPONSE_BYTES:
        raise ValueError("Configuration must be a regular JSON file no larger than 1 MiB")
    value = json.loads(config_path.read_text())
    if value.get("enabled") is not True or value.get("provider") != "ollama":
        raise ValueError("Inspection requires the enabled existing Ollama provider")
    config = value["ollama"]
    parsed = _validate_local_http_url(config["endpoint"])
    if parsed.path != "/api/generate" or parsed.query:
        raise ValueError("Configured endpoint must be the loopback /api/generate endpoint without query")
    for name in ("connect_timeout_seconds", "total_timeout_seconds"):
        number = config[name]
        if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number) or not 0 < number <= 120:
            raise ValueError("Configured timeouts must be finite and within (0,120] seconds")
    if type(config["max_output_tokens"]) is not int or not 0 < config["max_output_tokens"] <= 4096:
        raise ValueError("Configured max_output_tokens must be an integer within [1,4096]")
    if (type(config["think"]) is not bool or isinstance(config["temperature"], bool)
            or not isinstance(config["temperature"], (int, float)) or config["temperature"] != 0.0 or type(config["seed"]) is not int):
        raise ValueError("Expected explicit boolean think, temperature0 and integer seed")
    if type(value.get("max_commands")) is not int or not 1 <= value["max_commands"] <= 8:
        raise ValueError("Configured max_commands must be an integer within [1,8]")
    if not isinstance(config.get("keep_alive"), str) or not re.fullmatch(r"[1-9][0-9]{0,3}(?:ms|s|m|h)", config["keep_alive"]):
        raise ValueError("W10 keep_alive must be an explicit positive duration string, never zero/unload")
    if not isinstance(config["model"], str) or not config["model"].strip(): raise ValueError("Configured model is empty")
    return value


class MetadataClient:
    """Bounded GET inspection; /show reuses the product JSON POST transport."""
    def __init__(self, config: dict):
        self.config = config
        parsed = urlsplit(config["endpoint"])
        self.origin = urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))

    def get_json(self, path: str) -> dict:
        if path not in ("/api/version", "/api/tags", "/api/ps"):
            raise ValueError("Metadata GET path is not allowed")
        parsed = _validate_local_http_url(self.origin)
        total = min(self.config["total_timeout_seconds"], 10.0)
        deadline = monotonic() + total
        connection = http.client.HTTPConnection(parsed.hostname, parsed.port or 80,
                                                 timeout=self.config["connect_timeout_seconds"])
        try:
            connection.request("GET", path, headers={"Accept": "application/json"})
            remaining = deadline - monotonic()
            if remaining <= 0: raise LlmTransportError("Metadata request exceeded timeout")
            if connection.sock is not None: connection.sock.settimeout(remaining)
            response = connection.getresponse(); _set_socket_timeout(response, remaining)
            body = response.read(MAX_HTTP_RESPONSE_BYTES + 1)
            if response.status != 200: raise LlmTransportError(f"Metadata request returned HTTP {response.status}")
            if len(body) > MAX_HTTP_RESPONSE_BYTES: raise LlmTransportError("Metadata response exceeded size limit")
            value = json.loads(body.decode("utf-8"))
            if not isinstance(value, dict): raise ValueError("Metadata response must be an object")
            return value
        finally:
            connection.close()

    def show(self) -> dict:
        return UrllibJsonTransport().post_json(
            self.origin + "/api/show", {"model": self.config["model"], "verbose": False},
            connect_timeout_seconds=self.config["connect_timeout_seconds"],
            total_timeout_seconds=min(self.config["total_timeout_seconds"], 10.0))


def snapshot(client, directory: Path) -> dict:
    directory.mkdir()
    result = {}
    for name, get in (("version", lambda: client.get_json("/api/version")),
                      ("tags", lambda: client.get_json("/api/tags")),
                      ("show", client.show), ("ps", lambda: client.get_json("/api/ps"))):
        result[name] = get(); write_json(directory / (name + ".json"), result[name])
    return result


def identity(snapshot: dict, model: str, expected_digest: str | None, expected_version: str | None) -> dict:
    version = snapshot["version"].get("version")
    if not isinstance(version, str) or not version: raise ValueError("Server version is missing")
    if expected_version is not None and version != expected_version: raise ValueError("Server version differs from expected version")
    candidates = [row for row in snapshot["tags"]["models"] if row.get("name") == model or row.get("model") == model]
    if len(candidates) != 1: raise ValueError("Configured model must have one unambiguous local tag")
    tag = candidates[0]; digest = digest_value(tag["digest"])
    if expected_digest is not None and digest != digest_value(expected_digest): raise ValueError("Configured model digest differs from pinned model")
    show = snapshot["show"]
    if any(row.get(key) for row in (tag, show) for key in ("remote_host", "remote_model")):
        raise ValueError("W10 inspection requires a local model, not a remotely delegated tag")
    tag_details, show_details = tag.get("details"), show.get("details")
    if not isinstance(tag_details, dict) or not isinstance(show_details, dict):
        raise ValueError("Tag/show model details must be objects")
    for key in ("format", "family", "parameter_size", "quantization_level"):
        if (not isinstance(tag_details.get(key), str) or not tag_details[key].strip()
                or not isinstance(show_details.get(key), str) or not show_details[key].strip()):
            raise ValueError("Tag/show model core details are missing or empty")
    # /tags provides richer details than /show in Ollama 0.40.2. Extra fields
    # are valid, but every shared field and the four core fields must agree.
    for key in tag_details.keys() & show_details.keys():
        if type(tag_details[key]) is not type(show_details[key]) or tag_details[key] != show_details[key]:
            raise ValueError("Tag/show shared model details disagree")
    info = show["model_info"]; architecture = info["general.architecture"]
    if not isinstance(architecture, str) or not architecture: raise ValueError("Model architecture is missing")
    # head_dim is taken from GGUF key/value lengths, never hidden_size/n_head.
    keys = {"layers": "block_count", "embedding_length": "embedding_length", "query_heads": "attention.head_count",
            "kv_heads": "attention.head_count_kv", "key_head_dim": "attention.key_length",
            "value_head_dim": "attention.value_length", "model_context_length": "context_length"}
    dimensions = {name: info.get(architecture + "." + suffix) for name, suffix in keys.items()}
    if any(type(value) is not int or value <= 0 for value in dimensions.values()):
        raise ValueError("Model must provide explicit positive architecture dimensions")
    if dimensions["query_heads"] < dimensions["kv_heads"] or dimensions["query_heads"] % dimensions["kv_heads"]:
        raise ValueError("Query heads must form complete groups of KV heads")
    for details in (tag_details, show_details):
        for key, dimension in (("context_length", "model_context_length"), ("embedding_length", "embedding_length")):
            if key in details and (type(details[key]) is not int or details[key] != dimensions[dimension]):
                raise ValueError("Tag/show detail dimension differs from explicit model_info")
    for resident in snapshot["ps"]["models"]:
        if resident.get("name") == model or resident.get("model") == model:
            if digest_value(resident["digest"]) != digest: raise ValueError("Loaded model digest differs from tag identity")
    stable = {key: show.get(key) for key in ("details", "model_info", "template", "parameters", "license", "capabilities", "thinking")}
    signature = hashlib.sha256(json.dumps(stable, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    return {"model_tag": model, "digest": digest, "expected_digest_checked": expected_digest is not None,
            "server_version": version, "show_identity_sha256": signature, "details": show_details, "tag_details": tag_details,
            "dimensions": dimensions, "thinking_support": show.get("thinking"),
            "model_provenance": {key: info.get(key) for key in ("general.basename", "general.finetune", "general.version", "general.license", "general.license.link")},
            "kv_cache_dtype": None, "kv_cache_dtype_status": "NOT_OBSERVED: weight quantization does not determine KV precision"}


def thinking_support(show: dict, requested: bool) -> bool | None:
    values = show.get("thinking", {}).get("values")
    if not isinstance(values, list): return None
    return any(type(value) is bool and value == requested for value in values)


def server_rates(response: dict | None) -> dict:
    """v0.40.2 API counters, never substitutes for client TTFT or user-text TPS."""
    result = {"server_generation_tps": None, "server_uncached_prompt_tps": None,
              "client_ttft_ms": None, "client_ttft_status": "NOT_MEASURED: stream=false returns one final object",
              "counter_units": "Ollama duration nanoseconds and server tokenizer counts", "source": REFERENCE}
    if not isinstance(response, dict) or response.get("done") is not True:
        result["status"] = "No completed server counters"; return result
    def positive_int(value): return type(value) is int and 0 < value <= 2**63 - 1
    count, duration = response.get("eval_count"), response.get("eval_duration")
    if positive_int(count) and positive_int(duration): result["server_generation_tps"] = count * 1e9 / duration
    total, cached, elapsed = response.get("prompt_eval_count"), response.get("prompt_eval_cached_count"), response.get("prompt_eval_duration")
    result["prompt_cache_count_observed"] = cached is not None
    if type(total) is int and total >= 0 and type(cached) is int and 0 <= cached <= total and positive_int(elapsed) and total > cached:
        result["server_uncached_prompt_tps"] = (total - cached) * 1e9 / elapsed
    result["status"] = "Counter rates only where completed positive durations/counts permit; missing/zero/invalid fields remain null"
    result["output_token_scope"] = "Server eval_count may include thinking; not visible-response token count or client throughput"
    return result


class RecordingTransport:
    def __init__(self, delegate): self.delegate = delegate; self.calls = []
    def post_json(self, url, payload, **timeouts):
        call = {"url": url, "payload": dict(payload), "timeouts": dict(timeouts)}
        self.calls.append(call); started = perf_counter()
        try:
            call["response"] = self.delegate.post_json(url, payload, **timeouts)
            return call["response"]
        except Exception as error:
            call["error"] = {"type": type(error).__name__, "message": str(error), "http_status": getattr(error, "status", None)}
            raise
        finally: call["client_final_wall_ms"] = (perf_counter() - started) * 1000


def smoke(config_dir: Path, config: dict, directory: Path, delegate=None) -> dict:
    devices = json.loads((config_dir / "devices.json").read_text())
    backend = MockDeviceBackend(devices)  # Read-only initial state; no execution path is constructed.
    states = backend.list_states(); o = config["ollama"]
    schema_path = config_dir.parent / "schemas/agent_response.schema.json"
    schema = json.loads(schema_path.read_text()) if schema_path.is_file() else None
    transport = RecordingTransport(delegate or UrllibJsonTransport())
    path = LocalLlmIntelligentPath(devices, transport, endpoint=o["endpoint"], model=o["model"],
        max_commands=config["max_commands"], max_output_tokens=o["max_output_tokens"],
        connect_timeout_seconds=o["connect_timeout_seconds"], total_timeout_seconds=o["total_timeout_seconds"],
        temperature=o["temperature"], seed=o["seed"], think=o["think"], keep_alive=o["keep_alive"],
        state_provider=lambda: states, response_schema=schema)
    started = perf_counter(); result = path.route(PerceptionEvent(source=EventSource.TEXT, label=SMOKE_QUERY)); elapsed = (perf_counter()-started)*1000
    if len(transport.calls) != 1 or path.last_call_count != 1: raise ValueError("Smoke must make exactly one configured model call")
    write_json(directory / "generation.json", transport.calls[0])
    if backend.list_states() != states: raise ValueError("Read-only smoke changed mock device state")
    response = transport.calls[0].get("response")
    observed = response if isinstance(response, dict) else {}
    response_text = observed.get("response") if isinstance(observed.get("response"), str) else ""
    thinking_text = observed.get("thinking") if isinstance(observed.get("thinking"), str) else ""
    if observed.get("done") is True and observed.get("model") != o["model"]:
        raise ValueError("Completed generation response model differs from inspected configured tag")
    passed = (result.kind.value == "response" and not result.commands and observed.get("done") is True
              and observed.get("done_reason") != "length" and bool(response_text.strip()))
    return {"passed": passed, "fixed_query": SMOKE_QUERY, "planner_kind": result.kind.value,
            "planner_message": result.message, "planner_reason": result.reason,
            "commands": [{"device_id": c.device_id, "action": c.action, "arguments": dict(c.arguments)} for c in result.commands],
            "device_execution_calls": 0, "model_calls": len(transport.calls), "client_route_wall_ms": elapsed,
            "configured_think": o["think"], "observed_nonempty_thinking": bool(thinking_text),
            "observed_response_think_tag": "<think>" in response_text,
            "thinking_effective_mode": "Only configured request and raw observed output are known; no disabling guarantee inferred",
            "rates": server_rates(response), "request_stream": False,
            "scope": "One planner-only functional probe, no preload/warmup/repeats; no TTFT or P50/P95 benchmark"}


def run(args, client=None, delegate=None) -> dict:
    config_path = args.config or args.config_dir / "agent.json"
    config = load_config(args.config_dir, config_path); o = config["ollama"]; client = client or MetadataClient(o)
    source_paths = [ROOT / "scripts/inspect_llm_backend.py", ROOT / "src/edge_smart_home/agent/local_llm.py",
                    ROOT / "src/edge_smart_home/agent/contract.py", ROOT / "src/edge_smart_home/agent/interfaces.py",
                    ROOT / "src/edge_smart_home/device/mock.py",
                    config_path, args.config_dir / "agent.json", args.config_dir / "devices.json"]
    schema = args.config_dir.parent / "schemas/agent_response.schema.json"
    if schema.is_file(): source_paths.append(schema)
    source_before = {str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p): sha256(p) for p in source_paths}
    before = snapshot(client, args.outdir / "before")
    first = identity(before, o["model"], args.expected_digest, args.expected_version)
    result = smoke(args.config_dir, config, args.outdir, delegate) if args.smoke else None
    after = snapshot(client, args.outdir / "after")
    second = identity(after, o["model"], args.expected_digest, args.expected_version)
    if first != second: raise ValueError("Runtime/model identity changed during inspection")
    if source_before != {str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p): sha256(p) for p in source_paths}:
        raise ValueError("Configuration or planner source changed during inspection")
    git = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    return {"schema_version": 1, "kind": "w10_local_llm_inspection", "created_at": datetime.now(timezone.utc).isoformat(),
            "passed": result is None or result["passed"], "code_commit": git, "model_identity": first,
            "config": config, "config_path": str(config_path.resolve()),
            "uses_default_product_config": config_path.resolve() == (args.config_dir / "agent.json").resolve(),
            "configured_thinking_request_supported": thinking_support(before["show"], o["think"]),
            "smoke": result, "client_ttft_measured": False, "generation_calls": 1 if args.smoke else 0,
            "model_loaded_before": before["ps"], "model_loaded_after": after["ps"],
            "sources_sha256": source_before,
            "artifacts_sha256": {str(p.relative_to(args.outdir)): sha256(p) for p in sorted(args.outdir.rglob("*")) if p.is_file()},
            "limitations": ["Inspection success is not configuration compatibility or personal-understanding acceptance.",
                "No tag/model creation, deletion, pull, forced unload, context/precision or product-option overrides.",
                "Native GGUF weight precision, model maximum context and loaded /ps context are separate facts.",
                "KV cache dtype, exact GPU layer allocation, RAM high-water and repeat latency distributions are not measured.",
                "A nonstreaming final response cannot supply client TTFT; server counter TPS is not visible-text TPS."]}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-dir", type=Path, default=ROOT / "configs")
    parser.add_argument("--config", type=Path, help="Explicit agent JSON candidate; device/schema context still uses --config-dir")
    parser.add_argument("--outdir", type=Path, required=True)
    parser.add_argument("--expected-digest")
    parser.add_argument("--expected-version")
    parser.add_argument("--smoke", action="store_true", help="One configured nonstreaming planner call; no device execution")
    args = parser.parse_args(argv)
    if args.smoke and not args.expected_digest: parser.error("--smoke requires --expected-digest to bind the mutable model tag")
    if args.expected_digest:
        try: args.expected_digest = digest_value(args.expected_digest)
        except ValueError as error: parser.error(str(error))
    args.config_dir = args.config_dir.resolve(); args.outdir = args.outdir.resolve()
    if args.config is not None: args.config = args.config.resolve()
    if args.outdir.exists(): parser.error("Output directory exists; choose a new directory")
    args.outdir.mkdir(parents=True, exist_ok=False)
    try:
        report = run(args); write_json(args.outdir / "report.json", report)
    except Exception as error:
        write_json(args.outdir / "error.json", {"status": "error", "type": type(error).__name__, "message": str(error)})
        print(f"LLM inspection rejected: {type(error).__name__}: {error}", file=sys.stderr); return 2
    print(json.dumps({"outdir": str(args.outdir), "passed": report["passed"], "generation_calls": report["generation_calls"], "client_ttft_measured": False}, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__": raise SystemExit(main())
