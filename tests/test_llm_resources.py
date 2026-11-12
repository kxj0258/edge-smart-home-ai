from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import threading
import unittest
from unittest.mock import patch

from edge_smart_home.common.llm_resources import (
    MemorySampler, ProcMemoryProbe, gpu_snapshot, parse_stat, summarize_memory,
)


BINARY = "/snap/ollama/141/bin/ollama"
RUNNER = "/snap/ollama/141/lib/ollama/llama-server"


def stat(pid, ppid, start, comm="ollama ) worker"):
    fields = ["S", str(ppid)] + ["0"] * 17 + [str(start)] + ["0"] * 4
    return f"{pid} ({comm}) " + " ".join(fields)


def process(root, pid, ppid, argv, *, start=100, rss=100, pss=60, children=""):
    path = Path(root) / str(pid)
    task = path / "task" / str(pid)
    task.mkdir(parents=True)
    (task / "children").write_text(children)
    (path / "cmdline").write_bytes(b"\0".join(v.encode() for v in argv) + b"\0")
    (path / "stat").write_text(stat(pid, ppid, start))
    (path / "status").write_text(f"Name:\tollama\nVmRSS:\t{rss} kB\nVmHWM:\t9999 kB\n")
    (path / "smaps_rollup").write_text(f"Pss: {pss} kB\n")
    (path / "exe").symlink_to(argv[0])
    return path


class ProcMemoryTest(unittest.TestCase):
    def test_stat_start_time_with_parentheses_and_spaces(self):
        self.assertEqual(parse_stat(stat(12, 3, 987654321)), (3, 987654321))

    def test_server_and_worker_thread_child_runner_only(self):
        with TemporaryDirectory() as root:
            parent = process(root, 10, 1, [BINARY, "serve"], rss=200)
            worker = parent / "task" / "11"
            worker.mkdir()
            (worker / "children").write_text("20 21")
            process(root, 20, 10, [RUNNER, "--model", "/pinned/blob"], rss=400, start=101)
            process(root, 21, 10, ["/usr/bin/unrelated"], rss=90000)
            process(root, 30, 1, [RUNNER], rss=90000)  # Same name, wrong parent.
            result = ProcMemoryProbe(BINARY, proc_root=root).sample()
            self.assertEqual([p["pid"] for p in result["processes"]], [10, 20])
            self.assertEqual(result["sum_rss_bytes"], 600 * 1024)
            self.assertEqual(result["sum_pss_bytes"], 120 * 1024)
            self.assertEqual(result["processes"][1]["starttime_ticks"], 101)
            self.assertEqual(result["processes"][1]["role"], "runner")
            self.assertTrue(any("unrecognized_child_excluded" in s for s in result["discovery_errors"]))

    def test_no_server_and_ambiguous_servers_are_null_not_zero(self):
        with TemporaryDirectory() as root:
            probe = ProcMemoryProbe(BINARY, proc_root=root)
            self.assertIsNone(probe.sample()["sum_rss_bytes"])
            process(root, 10, 1, [BINARY, "serve"])
            process(root, 20, 1, [BINARY, "serve"])
            row = probe.sample()
            self.assertIsNone(row["sum_rss_bytes"])
            self.assertIn("expected_one_server_found_2", row["discovery_errors"])

    def test_denied_pss_exe_and_missing_rss_are_explicit(self):
        with TemporaryDirectory() as root:
            path = process(root, 10, 1, [BINARY, "serve"])
            read_text = Path.read_text

            def limited_read(value, *args, **kwargs):
                if value.name == "smaps_rollup":
                    raise PermissionError(13, "denied")
                return read_text(value, *args, **kwargs)

            with patch.object(Path, "read_text", limited_read), patch.object(
                    Path, "readlink", side_effect=PermissionError(13, "denied")):
                row = ProcMemoryProbe(BINARY, proc_root=root).sample()
            self.assertEqual(row["sum_rss_bytes"], 100 * 1024)
            self.assertIsNone(row["sum_pss_bytes"])
            self.assertTrue(any("PermissionError" in s for s in row["processes"][0]["errors"]))
            (path / "status").write_text("Name: ollama\n")
            row = ProcMemoryProbe(BINARY, proc_root=root).sample()
            self.assertIsNone(row["sum_rss_bytes"])
            self.assertIn("VmRSS_missing", row["processes"][0]["errors"])

    def test_pid_reuse_during_sample_discards_stale_memory(self):
        with TemporaryDirectory() as root:
            process(root, 10, 1, [BINARY, "serve"])
            probe = ProcMemoryProbe(BINARY, proc_root=root)
            identity = probe._identity(10)
            identity["role"] = "server"
            replacement = dict(identity, starttime_ticks=999)
            with patch.object(probe, "_selected", return_value=([identity], [])), patch.object(
                    probe, "_identity", return_value=replacement):
                row = probe.sample()
            self.assertIsNone(row["sum_rss_bytes"])
            self.assertIsNone(row["sum_pss_bytes"])
            self.assertTrue(any("identity_changed" in s for s in row["processes"][0]["errors"]))

    def test_children_read_failure_has_parent_scan_fallback(self):
        with TemporaryDirectory() as root:
            process(root, 10, 1, [BINARY, "serve"])
            process(root, 20, 10, [BINARY, "runner"], rss=200)
            read_text = Path.read_text

            def limited_read(value, *args, **kwargs):
                if value.name == "children":
                    raise PermissionError(13, "denied")
                return read_text(value, *args, **kwargs)

            with patch.object(Path, "read_text", limited_read):
                row = ProcMemoryProbe(BINARY, proc_root=root).sample()
            self.assertEqual(row["sum_rss_bytes"], 300 * 1024)
            self.assertTrue(any("scan_fallback" in s for s in row["discovery_errors"]))

    def test_vanished_child_does_not_become_complete_server_only_sum(self):
        with TemporaryDirectory() as root:
            process(root, 10, 1, [BINARY, "serve"], children="20")
            row = ProcMemoryProbe(BINARY, proc_root=root).sample()
            self.assertIsNone(row["sum_rss_bytes"])
            self.assertFalse(row["process_tree_discovery_complete"])
            self.assertTrue(any("child_20_identity" in s for s in row["discovery_errors"]))

    def test_readable_exe_that_contradicts_pinned_argv_invalidates_memory(self):
        with TemporaryDirectory() as root:
            path = process(root, 10, 1, [BINARY, "serve"])
            (path / "exe").unlink()
            (path / "exe").symlink_to("/unrelated/program")
            row = ProcMemoryProbe(BINARY, proc_root=root).sample()
            self.assertIsNone(row["sum_rss_bytes"])
            self.assertIn("mapped_exe_does_not_match_pinned_argv", row["processes"][0]["errors"])


def sample(value, stamp=1):
    return {"monotonic_ns": stamp, "utc": "fake", "sum_rss_bytes": value,
            "sum_pss_bytes": None, "processes": [], "discovery_errors": []}


class MemorySamplerTest(unittest.TestCase):
    def test_request_peak_uses_contemporaneous_sum_not_lifetime_high_water(self):
        rows = [sample(100, 1), sample(300, 50_000_001), sample(None, 100_000_001),
                sample(200, 150_000_001)]
        result = summarize_memory(rows)
        self.assertEqual(result["peak_sum_rss_bytes"], 300)
        self.assertEqual(result["pre_sum_rss_bytes"], 100)
        self.assertEqual(result["post_sum_rss_bytes"], 200)
        self.assertIsNone(result["peak_sum_pss_bytes"])
        self.assertEqual(result["incomplete_rss_samples"], 1)
        self.assertEqual(result["max_sample_gap_ms"], 50)

    def test_context_preserves_call_exception_and_stops_thread(self):
        probe = SimpleNamespace(sample=lambda: sample(100))
        sampler = MemorySampler(probe=probe)
        with self.assertRaisesRegex(ValueError, "request failure"):
            with sampler:
                raise ValueError("request failure")
        evidence = sampler.result()
        self.assertGreaterEqual(len(evidence["samples"]), 2)
        self.assertFalse(sampler._thread.is_alive())
        evidence["samples"][0]["sum_rss_bytes"] = 999
        self.assertEqual(sampler.result()["samples"][0]["sum_rss_bytes"], 100)
        with self.assertRaises(RuntimeError):
            sampler.start()

    def test_sampler_observes_during_request_and_reports_sampler_failures(self):
        observed = threading.Event()
        calls = []

        def take():
            calls.append(1)
            if len(calls) == 2:
                observed.set()
                raise PermissionError("probe denied")
            return sample(100)

        sampler = MemorySampler(probe=SimpleNamespace(sample=take), interval_seconds=0.001)
        with sampler:
            self.assertTrue(observed.wait(1))
        result = sampler.result()
        self.assertGreaterEqual(result["summary"]["sample_count"], 3)
        self.assertGreaterEqual(result["summary"]["incomplete_rss_samples"], 1)
        self.assertTrue(any("PermissionError" in str(row["discovery_errors"]) for row in result["samples"]))

    def test_invalid_interval_and_premature_result_rejected(self):
        for value in (0, -1, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                MemorySampler(interval_seconds=value)
        sampler = MemorySampler(probe=SimpleNamespace(sample=lambda: sample(1)))
        with self.assertRaises(RuntimeError):
            sampler.result()

    def test_gpu_diagnostic_is_injected_no_service_or_installs(self):
        calls = []

        def run(command, **kwargs):
            calls.append((command, kwargs))
            return SimpleNamespace(returncode=0, stdout="raw GPU diagnostic", stderr="")

        row = gpu_snapshot(executable="/fake/nvidia-smi", run=run)
        self.assertEqual(len(calls), 2)
        self.assertEqual(row["gpus"]["stdout"], "raw GPU diagnostic")
        self.assertTrue(all(call[1]["timeout"] == 5 for call in calls))


if __name__ == "__main__":
    unittest.main()
