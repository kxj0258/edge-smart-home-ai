from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import threading
import unittest
from unittest.mock import patch

from edge_smart_home.agent import stream_metrics as metrics


PAYLOAD = {"model": "qwen3:4b", "prompt": "说明本地能力", "stream": True, "think": False,
           "format": {"type": "object"}, "keep_alive": "10m",
           "options": {"num_ctx": 4096, "num_predict": 256, "temperature": 0.0, "seed": 42}}


def event(response="", *, done=False, **kwargs):
    return {"model": "qwen3:4b", "response": response, "done": done, **kwargs}


def terminal(**kwargs):
    return event(done=True, done_reason="stop", eval_count=20, eval_duration=2_000_000_000,
                 prompt_eval_count=100, prompt_eval_cached_count=80,
                 prompt_eval_duration=1_000_000_000, **kwargs)


def wire(*events):
    return b"".join(json.dumps(row, ensure_ascii=False).encode() + b"\n" for row in events)


class FakeSocket:
    def __init__(self):
        self.timeouts = []
        self.shutdown_called = threading.Event()

    def settimeout(self, value):
        self.timeouts.append(value)

    def shutdown(self, how):
        self.shutdown_called.set()


class FakeResponse:
    def __init__(self, parts, *, status=200, on_read=None):
        self.parts = list(parts)
        self.status = status
        self.on_read = on_read

    def read1(self, size):
        if self.on_read:
            self.on_read()
        if not self.parts:
            return b""
        part = self.parts.pop(0)
        if len(part) > size:
            self.parts.insert(0, part[size:])
        return part[:size]


class FakeConnection:
    def __init__(self, response):
        self.response = response
        self.sock = FakeSocket()
        self.requests = []
        self.connected = False
        self.closed = False

    def connect(self):
        self.connected = True

    def request(self, method, path, body, headers):
        self.requests.append((method, path, body, headers))

    def getresponse(self):
        return self.response

    def close(self):
        self.closed = True


class Clock:
    def __init__(self):
        self.now = 1_000_000_000

    def __call__(self):
        self.now += 1_000_000
        return self.now


class StreamMetricsTest(unittest.TestCase):
    def call(self, parts, *, payload=None, response=None, **kwargs):
        connection = FakeConnection(response or FakeResponse(parts))
        with patch.object(metrics.http.client, "HTTPConnection", return_value=connection):
            result = metrics.stream_generate("http://127.0.0.1:11434/api/generate",
                                             deepcopy(PAYLOAD if payload is None else payload),
                                             expected_model="qwen3:4b", **kwargs)
        return result, connection

    def failure(self, parts, **kwargs):
        with self.assertRaises(metrics.StreamMetricsError) as caught:
            self.call(parts, **kwargs)
        value = caught.exception.result
        self.assertFalse(value["passed"])
        self.assertIsNotNone(value["timings"]["finish_ms"])
        return value

    def test_utf8_arbitrary_read_boundaries_thinking_empty_and_multitoken_events(self):
        encoded = wire(event(), event(thinking="先思考"), event(), event("多个字token"),
                       event("完成"), terminal())
        # Split in the middle of both UTF-8 codepoints and JSON tokens.
        parts = [encoded[i:i + 7] for i in range(0, len(encoded), 7)]
        with patch.object(metrics, "perf_counter_ns", new=Clock()):
            result, connection = self.call(parts)
        self.assertTrue(result["passed"])
        self.assertEqual(result["response_text"], "多个字token完成")
        self.assertEqual(result["thinking_text"], "先思考")
        times = result["timings"]
        self.assertLess(times["first_event_ms"], times["first_thinking_ms"])
        self.assertLess(times["first_thinking_ms"], times["first_response_ms"])
        self.assertLess(times["first_response_ms"], times["done_ms"])
        self.assertLess(times["done_ms"], times["finish_ms"])
        self.assertEqual(result["event_count"], 6)
        self.assertEqual(len(result["events"]), 6)
        self.assertEqual(result["events"][0]["event"]["response"], "")
        self.assertEqual(result["rates"]["server_generation_tps"], 10)
        self.assertEqual(result["rates"]["server_uncached_prompt_tps"], 20)
        self.assertEqual(result["contents"]["response"]["sha256"], hashlib.sha256("多个字token完成".encode()).hexdigest())
        self.assertTrue(connection.closed)
        self.assertEqual(len(connection.requests), 1)
        self.assertEqual(json.loads(connection.requests[0][2]), PAYLOAD)
        self.assertEqual(result["request"]["payload"], PAYLOAD)

    def test_true_thinking_payload_and_schema_are_forwarded_without_overrides(self):
        payload = deepcopy(PAYLOAD)
        payload["think"] = True
        result, connection = self.call([wire(event("回答"), terminal())], payload=payload)
        self.assertTrue(result["request"]["think"])
        self.assertEqual(json.loads(connection.requests[0][2]), payload)
        self.assertIsNone(result["timings"]["first_thinking_ms"])

    def test_t0_precedes_request_encoding(self):
        original = metrics.json.dumps
        observations = []
        clock = Clock()

        def encode(*args, **kwargs):
            observations.append(clock.now)
            return original(*args, **kwargs)

        data = wire(event("回答"), terminal())
        with patch.object(metrics, "perf_counter_ns", new=clock), patch.object(metrics.json, "dumps", side_effect=encode):
            result, _ = self.call([data])
        self.assertEqual(result["t0_ns"], observations[0])
        self.assertIn("before JSON encoding", result["timing_boundary"])

    def test_only_thinking_or_empty_content_never_forges_visible_ttft(self):
        for data in (wire(event(thinking="思考"), terminal()), wire(event(), terminal())):
            result = self.failure([data])
            self.assertIsNone(result["timings"]["first_response_ms"])
            self.assertIsNotNone(result["timings"]["done_ms"])
            self.assertEqual(result["response_text"], "")

    def test_length_truncation_and_missing_terminal_are_preserved_failures(self):
        done = terminal()
        done["done_reason"] = "length"
        result = self.failure([wire(event("部分回答"), done)])
        self.assertEqual(result["response_text"], "部分回答")
        self.assertEqual(result["final"]["done_reason"], "length")
        self.assertIsNotNone(result["timings"]["first_response_ms"])
        result = self.failure([wire(event("部分回答"))])
        self.assertIsNone(result["final"])
        self.assertEqual(result["response_text"], "部分回答")

    def test_error_tag_remote_invalid_content_and_extra_done_rejected(self):
        invalid = [event("answer", model="another"), event("answer", remote_host="cloud.example"),
                   event("answer", remote_model="other"), event("answer", done=1),
                   event(None), {"error": "runner failed"}]
        for value in invalid:
            with self.subTest(value=value):
                result = self.failure([wire(value)])
                self.assertEqual(result["events"][0]["event"], value)
        self.failure([wire(event("answer"), terminal(), terminal())])

    def test_invalid_json_utf8_duplicate_keys_nonfinite_and_unterminated_line_keep_evidence(self):
        for line in (b'{"model":', b'\xff', b'{"done":false,"done":true}', b'{"value":NaN}',
                     b'{"response":"\\ud800"}'):
            with self.subTest(line=line):
                result = self.failure([line + b"\n"])
                row = result["events"][0]
                self.assertTrue(row["raw_line"] is not None or row.get("raw_line_hex"))
        data = wire(event("answer"), terminal()).removesuffix(b"\n")
        result = self.failure([data])
        self.assertIn("unparsed_tail_hex", result)

    def test_whole_deadline_is_checked_even_when_reads_keep_producing_bytes(self):
        clock = Clock()

        def advance():
            clock.now += 100_000_000

        response = FakeResponse([wire(event("partial"))] * 50, on_read=advance)
        with patch.object(metrics, "perf_counter_ns", new=clock):
            result = self.failure([], response=response, total_timeout_seconds=0.25)
        self.assertLess(result["event_count"], 50)
        self.assertIn("deadline", result["error"]["message"])

    def test_watchdog_interrupts_a_blocked_read_without_network(self):
        connection = FakeConnection(FakeResponse([]))

        def blocked_read(size):
            if not connection.sock.shutdown_called.wait(1.0):
                raise AssertionError("Whole deadline watchdog did not shut down blocked socket")
            raise OSError("socket shut down")

        connection.response.read1 = blocked_read
        with patch.object(metrics.http.client, "HTTPConnection", return_value=connection):
            with self.assertRaises(metrics.StreamMetricsError) as caught:
                metrics.stream_generate("http://127.0.0.1:11434/api/generate", PAYLOAD,
                                        expected_model="qwen3:4b", total_timeout_seconds=0.02)
        self.assertTrue(caught.exception.result["error"]["deadline_expired"])
        self.assertTrue(connection.closed)

    def test_size_and_event_bounds_are_independent_of_done(self):
        with patch.object(metrics, "MAX_LINE_BYTES", 20):
            self.failure([b"x" * 21])
        with patch.object(metrics, "MAX_BODY_BYTES", 20):
            self.failure([b"x" * 21])
        with patch.object(metrics, "MAX_EVENTS", 1):
            self.failure([wire(event("a"), terminal())])
            self.failure([b"\n\n\n"])

    def test_http_errors_do_not_retry(self):
        connection = FakeConnection(FakeResponse([], status=400))
        with patch.object(metrics.http.client, "HTTPConnection", return_value=connection):
            with self.assertRaises(metrics.StreamMetricsError) as caught:
                metrics.stream_generate("http://127.0.0.1:11434/api/generate", PAYLOAD, expected_model="qwen3:4b")
        self.assertEqual(caught.exception.result["http_status"], 400)
        self.assertEqual(len(connection.requests), 1)

    def test_arguments_reject_remote_wrong_endpoint_and_nonexplicit_thinking_before_http(self):
        variants = []
        for key, value in (("think", None), ("think", 0), ("stream", False), ("model", "other"), ("remote_host", "cloud")):
            payload = deepcopy(PAYLOAD)
            payload[key] = value
            variants.append(payload)
        with patch.object(metrics.http.client, "HTTPConnection", side_effect=AssertionError("preflight must not construct HTTP")):
            for payload in variants:
                with self.assertRaises(ValueError):
                    metrics.stream_generate("http://127.0.0.1:11434/api/generate", payload, expected_model="qwen3:4b")
            for endpoint in ("http://localhost:11434/api/generate", "https://127.0.0.1/api/generate",
                             "http://127.0.0.1/api/chat", "http://127.0.0.1/api/generate?x=1"):
                with self.assertRaises(ValueError):
                    metrics.stream_generate(endpoint, PAYLOAD, expected_model="qwen3:4b")
            for timeout in (0, -1, True, float("nan"), 601):
                with self.assertRaises(ValueError):
                    metrics.stream_generate("http://127.0.0.1/api/generate", PAYLOAD,
                                            expected_model="qwen3:4b", total_timeout_seconds=timeout)


class StreamCounterTest(unittest.TestCase):
    def test_missing_cached_counter_is_not_assumed_zero(self):
        final = terminal()
        del final["prompt_eval_cached_count"]
        result = metrics.server_rates(final)
        self.assertFalse(result["prompt_cache_count_observed"])
        self.assertFalse(result["prompt_cache_count_valid"])
        self.assertIsNone(result["server_uncached_prompt_tps"])
        self.assertEqual(result["server_generation_tps"], 10)

    def test_invalid_counts_and_duration_are_never_rates(self):
        for bad in (True, -1, 1.0, float("nan"), float("inf"), 2**63):
            for key in ("eval_count", "eval_duration"):
                value = terminal()
                value[key] = bad
                result = metrics.server_rates(value)
                self.assertIsNone(result["server_generation_tps"])
                self.assertIn(key, result["invalid_fields"])
        for key in ("eval_count", "eval_duration"):
            value = terminal()
            value[key] = 0
            self.assertIsNone(metrics.server_rates(value)["server_generation_tps"])
        self.assertIsNone(metrics.server_rates({"done": False, "eval_count": 20, "eval_duration": 100})["server_generation_tps"])

    def test_invalid_cache_counter_cannot_forge_uncached_prompt_speed(self):
        for cached in (-1, True, 101, 2**63):
            value = terminal()
            value["prompt_eval_cached_count"] = cached
            result = metrics.server_rates(value)
            self.assertFalse(result["prompt_cache_count_valid"])
            self.assertIsNone(result["server_uncached_prompt_tps"])


if __name__ == "__main__":
    unittest.main()
