"""Bounded, measurement-only Ollama NDJSON client (no product routing changes).

Protocol reference: Ollama v0.40.2 api/types.go (MIT), GenerateResponse/Metrics.
https://github.com/ollama/ollama/blob/v0.40.2/api/types.go

Client TTFT starts immediately BEFORE JSON encoding and HTTP send and ends when
the first complete NDJSON object containing a nonempty response is parsed. It
includes client encoding, connection, HTTP, buffering and parsing. Thinking and
empty events are retained, but are not visible-answer TTFT. Events are retained
in memory; callers must persist them only after this function finishes timing.
Server tokenizer counts/nanosecond durations are separate from these clocks.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import math
import socket
import threading
from time import perf_counter_ns

from .local_llm import _set_socket_timeout, _validate_local_http_url


REFERENCE = "https://github.com/ollama/ollama/blob/v0.40.2/api/types.go"
MAX_REQUEST_BYTES = 1024 * 1024
MAX_BODY_BYTES = 4 * 1024 * 1024
MAX_LINE_BYTES = 128 * 1024
MAX_EVENTS = 32768
MAX_INT64 = 2**63 - 1


class StreamMetricsError(RuntimeError):
    """An unsuccessful probe, with all partial evidence in ``result``."""

    def __init__(self, message: str, result: dict):
        super().__init__(message)
        self.result = result


def _strict_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate JSON object key")
        value[key] = item
    return value


def _bad_constant(value):
    raise ValueError("Nonfinite JSON number: " + value)


def _validate_unicode(value):
    """Reject escaped unpaired surrogates before retaining parsed JSON evidence."""
    if isinstance(value, str):
        value.encode("utf-8", errors="strict")
    elif isinstance(value, dict):
        for key, item in value.items():
            _validate_unicode(key)
            _validate_unicode(item)
    elif isinstance(value, list):
        for item in value:
            _validate_unicode(item)


def _nonnegative_int(value):
    return type(value) is int and 0 <= value <= MAX_INT64


def server_rates(final: dict | None) -> dict:
    """Official server counters only; no event/character-to-token conversion."""
    result = {
        "server_generation_tps": None,
        "server_uncached_prompt_tps": None,
        "prompt_cache_count_observed": False,
        "prompt_cache_count_valid": False,
        "invalid_fields": [],
        "counter_units": "Ollama tokenizer counts; duration nanoseconds",
        "output_token_scope": "eval_count may include thinking; not visible-response token count or client throughput",
        "source": REFERENCE,
    }
    if not isinstance(final, dict) or final.get("done") is not True:
        result["status"] = "NO_COMPLETED_COUNTERS"
        return result
    for key in ("total_duration", "load_duration", "prompt_eval_count", "prompt_eval_cached_count",
                "prompt_eval_duration", "eval_count", "eval_duration"):
        if key in final and not _nonnegative_int(final[key]):
            result["invalid_fields"].append(key)
    count, duration = final.get("eval_count"), final.get("eval_duration")
    if _nonnegative_int(count) and count > 0 and _nonnegative_int(duration) and duration > 0:
        result["server_generation_tps"] = count * 1e9 / duration
    total, cached, elapsed = (final.get(key) for key in
                             ("prompt_eval_count", "prompt_eval_cached_count", "prompt_eval_duration"))
    result["prompt_cache_count_observed"] = "prompt_eval_cached_count" in final
    valid_cache = _nonnegative_int(total) and _nonnegative_int(cached) and cached <= total
    result["prompt_cache_count_valid"] = valid_cache
    if _nonnegative_int(total) and _nonnegative_int(cached) and cached > total:
        result["invalid_fields"].append("prompt_eval_cached_count:exceeds_prompt_eval_count")
    if valid_cache and _nonnegative_int(elapsed) and elapsed > 0 and total > cached:
        result["server_uncached_prompt_tps"] = (total - cached) * 1e9 / elapsed
    result["status"] = "INVALID_COUNTERS" if result["invalid_fields"] else "COMPLETED_COUNTERS"
    return result


def _text_evidence(value: str) -> dict:
    encoded = value.encode("utf-8")
    return {"chars": len(value), "bytes": len(encoded), "sha256": hashlib.sha256(encoded).hexdigest()}


def _response_socket(connection, response):
    sock = getattr(connection, "sock", None)
    if sock is None and response is not None:
        sock = getattr(getattr(getattr(response, "fp", None), "raw", None), "_sock", None)
    return sock


def stream_generate(endpoint: str, payload: dict, *, expected_model: str,
                    connect_timeout_seconds: float = 2.0,
                    total_timeout_seconds: float = 120.0) -> dict:
    """Make exactly one local streaming request, returning metrics or partial error.

    The caller supplies the complete fixed request; this client never adds or
    overrides options, format, thinking, prompt or cache policy and never retries.
    Preflight argument failures raise ValueError without making a request. Once
    t0 is set, failures raise StreamMetricsError carrying timestamped evidence.
    A watchdog shuts down a blocked socket at the whole-request deadline, even
    if a peer keeps sending partial lines/chunks within per-read idle timeouts.
    Success requires one terminal done=true/stop event and nonempty response.
    W10 MetadataClient/identity checks remain the caller's before/after guards.
    """
    parsed = _validate_local_http_url(endpoint)
    if parsed.path != "/api/generate" or parsed.query:
        raise ValueError("Streaming requires loopback /api/generate without query")
    if not isinstance(expected_model, str) or not expected_model.strip():
        raise ValueError("An explicit expected local model is required")
    if not isinstance(payload, dict) or payload.get("model") != expected_model:
        raise ValueError("Request model must match expected model")
    if payload.get("stream") is not True or type(payload.get("think")) is not bool:
        raise ValueError("Request requires stream=true and explicit boolean think")
    if any(payload.get(key) for key in ("remote_host", "remote_model")):
        raise ValueError("Remote model requests are not allowed")
    if not isinstance(payload.get("prompt"), str) or not payload["prompt"]:
        raise ValueError("Request requires an explicit nonempty prompt")
    for name, value, limit in (("connect", connect_timeout_seconds, 30), ("total", total_timeout_seconds, 600)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 < value <= limit:
            raise ValueError(f"{name} timeout must be finite and within (0,{limit}] seconds")

    result = {
        "schema_version": 1, "passed": False, "error": None,
        "timing_boundary": "t0 before JSON encoding/HTTP send; event detected after complete UTF-8 NDJSON object parsing; finish after body EOF, before close/persistence",
        "t0_ns": None, "finish_ns": None,
        "timings": {key: None for key in ("first_event_ms", "first_thinking_ms", "first_response_ms", "done_ms", "finish_ms")},
        "request": {}, "events": [], "event_count": 0, "body_bytes": 0, "blank_lines": 0,
        "response_text": "", "thinking_text": "", "final": None,
        "rates": server_rates(None), "contents": {}, "http_status": None,
    }
    connection = http.client.HTTPConnection(parsed.hostname, parsed.port or 80,
                                             timeout=min(connect_timeout_seconds, total_timeout_seconds))
    response = None
    expired = threading.Event()
    watchdog = None
    chunks = {"response": [], "thinking": []}
    buffered = b""
    t0 = perf_counter_ns()
    result["t0_ns"] = t0
    deadline = t0 + int(total_timeout_seconds * 1e9)

    def remaining():
        seconds = (deadline - perf_counter_ns()) / 1e9
        if expired.is_set() or seconds <= 0:
            raise TimeoutError("Whole streaming request deadline exceeded")
        return seconds

    def expire():
        expired.set()
        sock = _response_socket(connection, response)
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def consume(line: bytes, received_ns: int):
        if len(result["events"]) >= MAX_EVENTS:
            raise ValueError("NDJSON line count exceeded limit")
        if len(line) > MAX_LINE_BYTES:
            raise ValueError("NDJSON line exceeded size limit")
        row = {"index": len(result["events"]), "received_ns": received_ns, "parsed_ns": None,
               "elapsed_ms": None, "raw_line": None, "event": None}
        result["events"].append(row)
        try:
            raw = line.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            row["raw_line_hex"] = line.hex()
            raise
        row["raw_line"] = raw
        if not raw.strip():
            result["blank_lines"] += 1
            return
        value = json.loads(raw, object_pairs_hook=_strict_object, parse_constant=_bad_constant)
        _validate_unicode(value)
        when = perf_counter_ns()
        elapsed = (when - t0) / 1e6
        row.update(parsed_ns=when, elapsed_ms=elapsed, event=value)
        result["event_count"] += 1
        if result["event_count"] > MAX_EVENTS:
            raise ValueError("NDJSON event count exceeded limit")
        if not isinstance(value, dict):
            raise ValueError("NDJSON event must be an object")
        if result["timings"]["first_event_ms"] is None:
            result["timings"]["first_event_ms"] = elapsed
        if result["final"] is not None:
            raise ValueError("Received another event after done=true")
        if value.get("error"):
            raise ValueError("Ollama streamed error: " + str(value["error"]))
        if value.get("model") != expected_model:
            raise ValueError("Stream response model differs from expected local tag")
        if any(value.get(key) for key in ("remote_host", "remote_model")):
            raise ValueError("Remote model stream is not allowed")
        if type(value.get("done")) is not bool:
            raise ValueError("Stream event requires an explicit boolean done")
        for key, first in (("response", "first_response_ms"), ("thinking", "first_thinking_ms")):
            content = value.get(key, "")
            if not isinstance(content, str):
                raise ValueError("Stream content must be text")
            chunks[key].append(content)
            if content and result["timings"][first] is None:
                result["timings"][first] = elapsed
        if value["done"]:
            result["final"] = value
            result["timings"]["done_ms"] = elapsed
            if value.get("done_reason") != "stop":
                raise ValueError("Generation did not complete normally: done_reason=" + str(value.get("done_reason")))

    try:
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) > MAX_REQUEST_BYTES:
            raise ValueError("Request exceeded size limit")
        # Roundtrip records exactly what was sent, not a mutable caller reference.
        actual = json.loads(encoded)
        result["request"] = {"payload": actual, "sha256": hashlib.sha256(encoded).hexdigest(),
                             "bytes": len(encoded), "think": actual["think"], "format": actual.get("format"),
                             "prompt_chars": len(actual["prompt"]),
                             "prompt_sha256": hashlib.sha256(actual["prompt"].encode("utf-8")).hexdigest()}
        watchdog = threading.Timer(remaining(), expire)
        watchdog.daemon = True
        watchdog.start()
        connection.connect()
        if connection.sock is not None:
            connection.sock.settimeout(remaining())
        connection.request("POST", "/api/generate", body=encoded,
                           headers={"Content-Type": "application/json", "Accept": "application/x-ndjson"})
        if connection.sock is not None:
            connection.sock.settimeout(remaining())
        response = connection.getresponse()
        result["http_status"] = response.status
        if response.status != 200:
            raise ValueError(f"Streaming request returned HTTP {response.status}")
        while True:
            _set_socket_timeout(response, remaining())
            part = response.read1(4096)
            received_ns = perf_counter_ns()
            remaining()
            if not part:
                break
            result["body_bytes"] += len(part)
            if result["body_bytes"] > MAX_BODY_BYTES:
                raise ValueError("Streaming body exceeded size limit")
            buffered += part
            while b"\n" in buffered:
                line, buffered = buffered.split(b"\n", 1)
                consume(line, received_ns)
            if len(buffered) > MAX_LINE_BYTES:
                raise ValueError("NDJSON line exceeded size limit")
        if buffered:
            raise ValueError("EOF with unterminated NDJSON line")
        if result["final"] is None:
            raise ValueError("EOF before terminal done=true event")
        if result["timings"]["first_response_ms"] is None:
            raise ValueError("Completed stream has no nonempty visible response")
        result["rates"] = server_rates(result["final"])
        if result["rates"]["invalid_fields"]:
            raise ValueError("Completed stream contains invalid server counters")
        remaining()
        result["passed"] = True
    except Exception as error:
        result["error"] = {"type": type(error).__name__, "message": str(error), "deadline_expired": expired.is_set()}
    finally:
        finish = perf_counter_ns()
        if result["passed"] and (expired.is_set() or finish > deadline):
            result["passed"] = False
            result["error"] = {"type": "TimeoutError", "message": "Whole streaming request deadline exceeded",
                               "deadline_expired": True}
        result["finish_ns"] = finish
        result["timings"]["finish_ms"] = (finish - t0) / 1e6
        result["response_text"] = "".join(chunks["response"])
        result["thinking_text"] = "".join(chunks["thinking"])
        result["contents"] = {key: _text_evidence(result[key + "_text"]) for key in ("response", "thinking")}
        if buffered and not result["passed"]:
            result["unparsed_tail_hex"] = buffered[:MAX_LINE_BYTES + 1].hex()
        result["rates"] = server_rates(result["final"])
        if watchdog is not None:
            watchdog.cancel()
            watchdog.join()
        connection.close()
    if not result["passed"]:
        raise StreamMetricsError(result["error"]["message"], result)
    return result
