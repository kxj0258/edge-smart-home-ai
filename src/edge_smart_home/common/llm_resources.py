"""Read-only Linux process RAM evidence for the separately running Ollama service.

Uses procfs, not client RSS or Ollama /api/ps buffer-allocation estimates. See
Ollama v0.40.2 llm/llama_server.go::MemorySize and Linux proc_pid_status(5).
VmRSS is a kernel approximation; summing RSS can double count shared pages.
Sampling misses peaks shorter than the interval. VmHWM is a process-lifetime
high water mark and is deliberately never substituted for a request peak.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import shutil
import subprocess
import threading
import time
from typing import Any


def _failure(error: Exception) -> str:
    return f"{type(error).__name__}: {error}"


def parse_stat(text: str) -> tuple[int, int]:
    """Return PPID and starttime ticks, including comm values containing ')'."""
    fields = text[text.rindex(")") + 1:].split()
    return int(fields[1]), int(fields[19])  # fields 4 and 22 in proc_pid_stat(5)


def _memory_field(text: str, name: str) -> int | None:
    for line in text.splitlines():
        fields = line.split()
        if fields and fields[0] == name + ":":
            if len(fields) != 3 or fields[2] != "kB" or int(fields[1]) < 0:
                raise ValueError(f"invalid {name} field")
            return int(fields[1]) * 1024  # Linux proc 'kB' means KiB.
    return None


class ProcMemoryProbe:
    """Select one server by pinned argv path, and runners by path + parent tree.

    argv is observable evidence, not proof of the mapped executable inode when
    readlink(exe) is denied. Multiple matching servers make selection ambiguous.
    No process is started, signalled, stopped, or reconfigured by this helper.
    """

    def __init__(self, expected_binary: str | Path, *, proc_root: str | Path = "/proc"):
        self.expected_binary = Path(expected_binary)
        if not self.expected_binary.is_absolute():
            raise ValueError("expected_binary must be an absolute pinned path")
        self.proc_root = Path(proc_root)
        self.runner_binary = self.expected_binary.parent.parent / "lib/ollama/llama-server"
        self._server_pid: int | None = None

    def _identity(self, pid: int) -> dict[str, Any]:
        path = self.proc_root / str(pid)
        ppid, start = parse_stat((path / "stat").read_text())
        argv = (path / "cmdline").read_bytes().rstrip(b"\0").split(b"\0")
        argv = [v.decode("utf-8", errors="replace") for v in argv if v]
        return {"pid": pid, "ppid": ppid, "starttime_ticks": start, "argv": argv}

    def _server(self, row: dict[str, Any]) -> bool:
        return row["argv"][:2] == [str(self.expected_binary), "serve"]

    def _runner(self, row: dict[str, Any]) -> bool:
        argv = row["argv"]
        return bool(argv) and (argv[0] == str(self.runner_binary) or
                              argv[:2] == [str(self.expected_binary), "runner"])

    def _scan(self) -> list[dict[str, Any]]:
        rows = []
        for path in self.proc_root.iterdir():
            if path.name.isdecimal():
                try:
                    rows.append(self._identity(int(path.name)))
                except (OSError, ValueError, IndexError):
                    continue  # Unrelated inaccessible/disappearing processes.
        return rows

    def _selected(self) -> tuple[list[dict[str, Any]], list[str]]:
        errors: list[str] = []
        # Full discovery is only needed initially or following server exit.
        server = None
        if self._server_pid is not None:
            try:
                candidate = self._identity(self._server_pid)
                if self._server(candidate):
                    server = candidate
            except (OSError, ValueError, IndexError):
                pass
        scanned = None
        if server is None:
            scanned = self._scan()
            servers = [r for r in scanned if self._server(r)]
            if len(servers) != 1:
                self._server_pid = None
                return [], [f"expected_one_server_found_{len(servers)}"]
            server = servers[0]
            self._server_pid = server["pid"]
        server["role"] = "server"
        selected = [server]
        pending = [server]
        visited = {server["pid"]}
        while pending:
            parent = pending.pop()
            try:
                # children is per-thread: Go may fork its runner on a worker
                # thread rather than the leader. Walk every task, not only PID.
                task_root = self.proc_root / str(parent["pid"]) / "task"
                children = set()
                for task in task_root.iterdir():
                    if task.name.isdecimal():
                        children.update((task / "children").read_text().split())
                identities = []
                for child in sorted(children, key=int):
                    try:
                        identities.append(self._identity(int(child)))
                    except (OSError, ValueError, IndexError) as error:
                        errors.append(f"child_{child}_identity: {_failure(error)}")
            except (OSError, ValueError) as error:
                errors.append(f"children_{parent['pid']}: {_failure(error)}; scan_fallback")
                if scanned is None:
                    scanned = self._scan()
                identities = [r for r in scanned if r["ppid"] == parent["pid"]]
            for row in identities:
                if row["pid"] in visited or row["ppid"] != parent["pid"]:
                    continue
                visited.add(row["pid"])
                if self._runner(row):
                    row["role"] = "runner"
                    selected.append(row)
                    pending.append(row)
                else:
                    # Do not include unrelated children, even if their RSS is readable.
                    errors.append(f"unrecognized_child_excluded:{row['pid']}:{row['argv'][:1]}")
        return selected, errors

    def sample(self) -> dict[str, Any]:
        begin = time.monotonic_ns()
        utc = datetime.now(timezone.utc).isoformat()
        try:
            selected, errors = self._selected()
        except (OSError, ValueError, IndexError) as error:
            selected, errors = [], [f"discovery: {_failure(error)}"]
        processes = []
        for identity in selected:
            row = dict(identity)
            row.update(rss_bytes=None, pss_bytes=None, lifetime_hwm_bytes=None, errors=[])
            path = self.proc_root / str(row["pid"])
            try:
                status = (path / "status").read_text()
                row["rss_bytes"] = _memory_field(status, "VmRSS")
                row["lifetime_hwm_bytes"] = _memory_field(status, "VmHWM")
                row["rss_source"] = "proc_status_VmRSS_approximate"
                if row["rss_bytes"] is None:
                    row["errors"].append("VmRSS_missing")
            except (OSError, ValueError) as error:
                row["errors"].append(f"status: {_failure(error)}")
            try:
                row["pss_bytes"] = _memory_field((path / "smaps_rollup").read_text(), "Pss")
                if row["pss_bytes"] is None:
                    row["errors"].append("Pss_missing")
            except (OSError, ValueError) as error:
                row["errors"].append(f"smaps_rollup: {_failure(error)}")
            try:
                row["mapped_exe"] = str((path / "exe").readlink())
                if row["mapped_exe"] != row["argv"][0]:
                    row["errors"].append("mapped_exe_does_not_match_pinned_argv")
                    row["rss_bytes"] = row["pss_bytes"] = None
            except OSError as error:
                row["mapped_exe"] = None
                row["errors"].append(f"exe: {_failure(error)}")
            try:
                # Detect exit/PID reuse across the non-atomic group of proc reads.
                after = self._identity(row["pid"])
                if any(after[k] != row[k] for k in ("starttime_ticks", "ppid", "argv")):
                    raise ValueError("process_identity_changed_during_sample")
            except (OSError, ValueError, IndexError) as error:
                row["errors"].append(f"identity_recheck: {_failure(error)}")
                row["rss_bytes"] = row["pss_bytes"] = None
            processes.append(row)
        result = {"monotonic_ns": begin, "utc": utc, "processes": processes,
                  "discovery_errors": errors, "sample_duration_ns": time.monotonic_ns() - begin}
        discovery_complete = not any(e.startswith(("discovery:", "expected_one_server", "child_")) for e in errors)
        result["process_tree_discovery_complete"] = discovery_complete
        for metric in ("rss", "pss"):
            values = [p[metric + "_bytes"] for p in processes]
            # Never turn missing processes/read failures into an observed zero.
            complete = discovery_complete and bool(values) and all(v is not None for v in values)
            result["sum_" + metric + "_bytes"] = sum(values) if complete else None
        return result


def summarize_memory(samples: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {"sample_count": len(samples)}
    for metric in ("rss", "pss"):
        key = "sum_" + metric + "_bytes"
        observed = [s[key] for s in samples if s[key] is not None]
        summary["pre_" + key] = samples[0][key] if samples else None
        summary["post_" + key] = samples[-1][key] if samples else None
        summary["peak_" + key] = max(observed) if observed else None
        summary["incomplete_" + metric + "_samples"] = len(samples) - len(observed)
    identities = {}
    for sample in samples:
        for row in sample["processes"]:
            key = (row["pid"], row["starttime_ticks"])
            identities[key] = {k: row[k] for k in ("pid", "ppid", "starttime_ticks", "argv", "role")}
    summary["observed_process_identities"] = list(identities.values())
    stamps = [s["monotonic_ns"] for s in samples]
    summary["max_sample_gap_ms"] = max((b - a for a, b in zip(stamps, stamps[1:])), default=0) / 1e6
    return summary


class MemorySampler:
    """Context manager around one request; result() after exit is JSON evidence."""

    def __init__(self, *, interval_seconds: float = 0.05,
                 expected_binary: str | Path | None = None,
                 probe: ProcMemoryProbe | None = None):
        if not math.isfinite(interval_seconds) or interval_seconds <= 0:
            raise ValueError("interval_seconds must be finite and positive")
        if probe is None:
            if expected_binary is None:
                root = Path(__file__).resolve().parents[3]
                source = json.loads((root / "configs/llm-model-source-v1.json").read_text())
                expected_binary = source["runtime"]["installed_cli_binary"]["path"]
            probe = ProcMemoryProbe(expected_binary)
        self.probe = probe
        self.interval_seconds = interval_seconds
        self._samples: list[dict[str, Any]] = []
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._finished = False

    def _take(self) -> None:
        try:
            row = self.probe.sample()
        except Exception as error:
            # A sampling failure must neither disappear silently nor mask the
            # streaming exception being measured by the caller.
            row = {"monotonic_ns": time.monotonic_ns(),
                   "utc": datetime.now(timezone.utc).isoformat(),
                   "processes": [], "discovery_errors": [_failure(error)],
                   "sum_rss_bytes": None, "sum_pss_bytes": None}
        self._samples.append(row)

    def _run(self) -> None:
        while not self._stop_event.wait(self.interval_seconds):
            self._take()

    def start(self) -> "MemorySampler":
        if self._thread is not None or self._finished:
            raise RuntimeError("MemorySampler instances measure exactly one request")
        self._take()  # pre-request process residency, outside stream timing.
        self._thread = threading.Thread(target=self._run, name="ollama-ram-sampler", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> dict[str, Any]:
        if self._thread is None:
            raise RuntimeError("MemorySampler has not started")
        if not self._finished:
            self._stop_event.set()
            self._thread.join()
            self._take()  # post-request residency; not an allocation increment.
            self._finished = True
        return self.result()

    def result(self) -> dict[str, Any]:
        if not self._finished:
            raise RuntimeError("finish sampling before reading final evidence")
        return {"schema_version": 1, "interval_seconds": self.interval_seconds,
                "source": "linux_procfs_separate_ollama_server_and_verified_runner_tree",
                "selection": "one exact pinned server argv; exact runner binary and server parent chain",
                "samples": deepcopy(self._samples), "summary": summarize_memory(self._samples),
                "limitations": ["VmRSS is the kernel approximate resident-set counter",
                    "sum RSS can double count pages shared between processes; it excludes GPU memory",
                    "PSS is null when smaps_rollup is inaccessible; missing is never zero",
                    "sampled request peak can miss sub-interval peaks; VmHWM is lifetime only",
                    "pre/post RSS is process residency, not prefix-cache proof or allocation delta",
                    "argv/path matching is not mapped-inode proof when proc exe readlink is denied",
                    "proc files and process-tree traversal are not an atomic system snapshot"]}

    def __enter__(self) -> "MemorySampler":
        return self.start()

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.stop()


def gpu_snapshot(*, executable: str | None = None, run=subprocess.run) -> dict[str, Any]:
    """Optional pre/post NVIDIA diagnostic, deliberately outside stream timing.

    GPU-wide used memory includes other apps. Per-compute-process rows are raw
    diagnostic evidence, not an allocator peak or a host-RAM measurement.
    """
    executable = executable or shutil.which("nvidia-smi")
    result: dict[str, Any] = {"utc": datetime.now(timezone.utc).isoformat(),
                              "monotonic_ns": time.monotonic_ns(), "source": executable}
    if executable is None:
        result["error"] = "nvidia-smi_not_available"
        return result
    for key, query in (("gpus", "--query-gpu=index,uuid,name,memory.total,memory.used,temperature.gpu,utilization.gpu,pstate"),
                       ("compute_apps", "--query-compute-apps=pid,process_name,used_memory")):
        command = [executable, query, "--format=csv,noheader,nounits"]
        try:
            completed = run(command, capture_output=True, text=True, timeout=5, check=False)
            result[key] = {"command": command, "returncode": completed.returncode,
                           "stdout": completed.stdout, "stderr": completed.stderr}
        except (OSError, subprocess.SubprocessError) as error:
            result[key] = {"command": command, "error": _failure(error)}
    result["limitation"] = "pre/post diagnostic only; GPU-wide includes other apps; no sampled GPU peak"
    return result
