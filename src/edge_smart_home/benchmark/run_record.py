"""Reproducibility metadata; hashes local files without starting any service."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import subprocess
import sys


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def runtime_metadata() -> dict:
    return {"python": sys.version, "executable": sys.executable,
            "platform": platform.platform(), "machine": platform.machine(),
            "logical_cpus": os.cpu_count(),
            "packages": {d.metadata["Name"]: d.version for d in importlib.metadata.distributions()
                         if d.metadata.get("Name")}}


def resource_sample() -> dict:
    """Linux RSS, process CPU time and high-water RSS; unavailable stays null."""
    rss = peak = cpu = None
    try:
        import resource
        usage = resource.getrusage(resource.RUSAGE_SELF)
        cpu = usage.ru_utime + usage.ru_stime
        if sys.platform.startswith("linux"):
            peak = usage.ru_maxrss / 1024
            pages = int(Path("/proc/self/statm").read_text().split()[1])
            rss = pages * os.sysconf("SC_PAGE_SIZE") / 1024 ** 2
    except (ImportError, OSError, ValueError, IndexError):
        pass
    return {"cpu_seconds": cpu, "rss_mb": rss, "peak_rss_mb": peak}


def build_run_record(root: Path, run_id: str, metadata: dict | None = None,
                     artifacts: list[Path] | None = None) -> dict:
    root = root.resolve()
    def git(*args):
        try:
            return subprocess.check_output(["git", "-C", str(root), *args],
                                           stderr=subprocess.DEVNULL).decode().strip()
        except (OSError, subprocess.CalledProcessError):
            return None
    paths = []
    for directory in ("src", "scripts", "configs", "schemas", "tests"):
        paths.extend(p for p in (root / directory).rglob("*")
                     if p.is_file() and "__pycache__" not in p.parts)
    paths.extend(p for p in (root / "pyproject.toml",) if p.is_file())
    hashes = {str(p.relative_to(root)): sha256(p) for p in sorted(paths)}
    model_files = {}
    for path in artifacts or []:
        path = path.resolve()
        files = sorted(path.rglob("*")) if path.is_dir() else [path]
        for file in files:
            if file.is_file():
                model_files[str(file)] = {"sha256": sha256(file), "bytes": file.stat().st_size}
            elif file == path:
                model_files[str(file)] = {"sha256": None, "status": "missing"}
    return {"schema_version": 2, "run_id": run_id, "session_id": run_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "git_commit": git("rev-parse", "HEAD"), "git_status": git("status", "--porcelain"),
            "files_sha256": hashes,
            "config_sha256": hashlib.sha256(json.dumps(
                {k: v for k, v in hashes.items() if k.startswith("configs/")},
                sort_keys=True).encode()).hexdigest(),
            "artifacts": model_files, "runtime": runtime_metadata(),
            "run_parameters": metadata or {}, "metrics": None,
            "input_source": (metadata or {}).get("input_source"),
            "device_backend": (metadata or {}).get("backend"),
            "sample_count": None, "result": "pending",
            "command_set_version": hashes.get("configs/asr_test_commands.txt"),
            "experiment": {"baseline_run": None, "candidate_run": run_id,
                           "changed_variable": None, "decision": "pending"},
            "ollama_digest": None,
            "notes": "Local file inventory; no service queried. Model tag is not a digest. "
                     "Configured Mock initial state is in devices.json; live initial state is unmeasured."}


def metadata_artifacts(metadata: dict) -> list[Path]:
    """Only inspect explicit model/input paths, never arbitrary metadata strings."""
    paths = []
    for key in ("asr_model_dir", "gesture_model", "tts_model_dir", "hotwords", "config_dir"):
        if metadata.get(key):
            paths.append(Path(metadata[key]))
    model = metadata.get("model")
    if isinstance(model, dict):
        paths.extend(Path(v) for v in model.values() if isinstance(v, str))
    elif isinstance(model, str):
        paths.append(Path(model))
    return paths
