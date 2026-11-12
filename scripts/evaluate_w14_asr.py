"""Frozen W14 file-replay ASR evidence; never capture audio or execute devices.

Reuses the product streaming waveform adapter, conservative voice normalization,
and text resolver. File replay is neither live latency nor an overflow test.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import platform
import re
import subprocess
import sys
from time import perf_counter
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from edge_smart_home.agent.error_codes import input_error_code
from edge_smart_home.app.pipeline import TextControlPipeline, RequestPlanConsistencyError
from edge_smart_home.audio.pipeline import _normalize_transcript
from edge_smart_home.benchmark.asr_evaluation import text_metrics
from edge_smart_home.common import EventSource, PerceptionEvent
from edge_smart_home.device import MockDeviceBackend, SafetyPolicy
from edge_smart_home.router import CommandDeduplicator, FastPathRouter, SceneRouter, UnsupportedCommandError

SOURCE_KINDS = ("human", "synthetic", "mock", "unknown")
LIMITATIONS = [
    "Previously selected small file-replay development sample; not a held-out home-command or speaker benchmark.",
    "Source kind and license attribution come from the frozen provenance manifest, not automatic voice authenticity detection.",
    "Recognizer output includes the frozen decoder/hotword search bias; it is not an unconditioned model baseline.",
    "Voice normalization and text resolution are separate observations; no Orchestrator, Ollama, backend.execute or TTS is called.",
    "Independent Silero VAD is not in this ASR path; recognizer endpoint detection is not a VAD accuracy measurement.",
    "Offline decode RTF and replay wall time do not prove live input overflow/drop fixed; live E2E and overflow remain unmeasured.",
    "No accuracy threshold was frozen; transcript errors are reported and do not alone fail engineering evidence gates.",
    "No real device, microphone, ARM, personal understanding or comprehensive production safety acceptance.",
]


def now():
    return datetime.now(timezone.utc).isoformat()


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, value):
    with Path(path).open("x", encoding="utf-8") as target:
        json.dump(value, target, ensure_ascii=False, indent=2, allow_nan=False)
        target.write("\n")


def git_info():
    return {"commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
            "status": subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True)}


def dependency_identity():
    packages = {}
    for name in ("numpy", "sherpa-onnx", "sherpa-onnx-core", "sounddevice"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    import sherpa_onnx
    native_binaries, bundled_platform_libraries = [], []
    for name in ("sherpa-onnx", "sherpa-onnx-core"):
        distribution = importlib.metadata.distribution(name)
        for member in distribution.files or ():
            text = str(member)
            if ".so" not in text and not text.endswith((".dll", ".dylib")):
                continue
            identity = file_identity(distribution.locate_file(member))
            if text.startswith("sherpa_onnx/lib/"):
                native_binaries.append(identity)
            else:
                bundled_platform_libraries.append(identity)
    if not native_binaries:
        raise ValueError("No sherpa-onnx native binaries found in the installed distributions")
    return {"python": platform.python_version(), "executable": str(Path(sys.executable).resolve()),
            "platform": platform.platform(), "packages": packages,
            "sherpa_onnx_python_version": sherpa_onnx.__version__,
            "embedded_onnxruntime_version": sherpa_onnx.onnxruntime_version,
            "sherpa_native_binaries": sorted(native_binaries, key=lambda item: item["path"]),
            "bundled_platform_libraries": sorted(bundled_platform_libraries, key=lambda item: item["path"])}


def file_identity(path):
    path = Path(path).resolve()
    return {"path": str(path), "bytes": path.stat().st_size, "sha256": sha256(path)}


def check_file(item):
    path = Path(item["path"])
    if path.stat().st_size != item["bytes"] or sha256(path) != item["sha256"]:
        raise ValueError(f"Frozen file differs: {path}")


def source_hashes():
    names = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0")
    return {name: sha256(ROOT / name) for name in sorted(names) if name and (
        name.startswith(("src/", "scripts/", "configs/", "schemas/")) or name == "pyproject.toml")}


def resolve_path(value, manifest):
    path = Path(value)
    if path.is_absolute():
        return path.resolve()
    for candidate in (ROOT / path, manifest.parent / path):
        if candidate.is_file():
            return candidate.resolve()
    raise FileNotFoundError(f"Audio does not exist: {value}")


def validate_manifest(value, manifest):
    if value.get("schema_version") != 1 or value.get("kind") != "w14_short_speech_manifest":
        raise ValueError("Expected w14_short_speech_manifest schema_version 1")
    cases = value.get("cases")
    if not isinstance(cases, list) or len(cases) < 5:
        raise ValueError("Select at least five fixed, licensed speech recordings")
    ids, paths, digests, frozen = set(), set(), set(), []
    for row in cases:
        if not isinstance(row, dict):
            raise ValueError("Every case must be an object")
        for name in ("id", "wav", "sha256", "expected", "source_url", "license", "attribution", "provenance_note"):
            if not isinstance(row.get(name), str) or not row[name].strip():
                raise ValueError(f"Missing case field: {name}")
        if row.get("source_kind") not in SOURCE_KINDS:
            raise ValueError("source_kind must distinguish human/synthetic/mock/unknown")
        if not re.fullmatch("[0-9a-f]{64}", row["sha256"]):
            raise ValueError("Expected a literal SHA256 audio digest")
        path = resolve_path(row["wav"], manifest)
        if row["id"] in ids or str(path) in paths or row["sha256"] in digests:
            raise ValueError("Case IDs, audio paths and audio digests must be unique")
        ids.add(row["id"]); paths.add(str(path)); digests.add(row["sha256"])
        identity = file_identity(path)
        if identity["sha256"] != row["sha256"]:
            raise ValueError(f"Audio hash mismatch: {row['id']}")
        frozen.append({**row, "audio_identity": identity})
    if sum(row["source_kind"] in ("human", "synthetic") for row in frozen) < 5:
        raise ValueError("Mock and unknown-origin inputs cannot satisfy five licensed speech recordings")
    return frozen


def model_paths(args):
    result = {}
    for name in ("tokens", "encoder", "decoder", "joiner"):
        explicit = getattr(args, name, None)
        if explicit:
            result[name] = Path(explicit)
            continue
        if args.model_dir is None:
            raise ValueError("Provide --model-dir or all four explicit model paths")
        matches = list(args.model_dir.glob("tokens.txt" if name == "tokens" else name + "*.onnx"))
        quantized = [p for p in matches if "int8" in p.name]
        matches = quantized or matches
        if len(matches) != 1:
            raise ValueError(f"Ambiguous/missing {name}; provide its explicit path")
        result[name] = matches[0]
    return result


def acceptance_policy():
    return {"minimum_licensed_recordings": 5, "require_unique_id_path_and_sha256": True,
            "every_frozen_case_recorded_once": True, "all_audio_and_asr_runs_complete": True,
            "classify_every_error": True, "accuracy_100_percent_required": False,
            "retries": 0, "warmups": 0, "real_device_executions": 0, "model_service_calls": 0}


def prepare(args):
    args.outdir.mkdir(parents=True, exist_ok=False)
    git = git_info()
    if git["status"]:
        raise ValueError("Prepare requires a clean committed child repository")
    manifest = args.manifest.resolve()
    cases = validate_manifest(read_json(manifest), manifest)
    config_dir = args.config_dir.resolve()
    configs = {name: read_json(config_dir / (name + ".json"))
               for name in ("audio", "devices", "intents", "routing", "scenes", "safety")}
    models = {name: file_identity(path) for name, path in model_paths(args).items()}
    hotwords = file_identity(args.hotwords) if args.hotwords else None
    identities = {str((config_dir / (name + ".json")).resolve()): file_identity(config_dir / (name + ".json"))
                  for name in configs}
    p = {"schema_version": 1, "kind": "w14_frozen_asr_protocol", "state": "frozen", "created_at": now(),
         "prepared_git": git, "manifest": file_identity(manifest), "cases": cases, "configs": configs,
         "configuration_files": identities, "models": models, "hotwords": hotwords,
         "dependencies": dependency_identity(), "sources_sha256": source_hashes(),
         "acceptance_policy": acceptance_policy(), "limitations": LIMITATIONS,
         "asr_calls": 0, "model_service_calls": 0, "real_device_executions": 0}
    write_json(args.outdir / "protocol.json", p)
    return {"passed": True, "protocol": str(args.outdir / "protocol.json"), "cases": len(cases),
            "source_kind_counts": dict(Counter(c["source_kind"] for c in cases)), "asr_calls": 0}


def validate_protocol(p, check_current=True):
    if (p.get("schema_version") != 1 or p.get("kind") != "w14_frozen_asr_protocol"
            or p.get("state") != "frozen" or p.get("acceptance_policy") != acceptance_policy()):
        raise ValueError("Expected the frozen W14 ASR protocol and unchanged acceptance policy")
    cases = p["cases"]
    if (len(cases) < 5 or any(len({c[key] for c in cases}) != len(cases) for key in ("id", "sha256"))
            or len({c["audio_identity"]["path"] for c in cases}) != len(cases)):
        raise ValueError("Protocol case count/identity differs")
    if (sum(c["source_kind"] in ("human", "synthetic") for c in cases) < 5
            or any(c["source_kind"] not in SOURCE_KINDS or c["audio_identity"]["sha256"] != c["sha256"] for c in cases)
            or any(not isinstance(c.get(key), str) or not c[key].strip() for c in cases
                   for key in ("id", "expected", "source_url", "license", "attribution", "provenance_note"))):
        raise ValueError("Frozen recording provenance or identity differs")
    if p["prepared_git"]["status"] or "scripts/evaluate_w14_asr.py" not in p["sources_sha256"]:
        raise ValueError("Protocol must bind committed product and evaluation sources")
    if check_current:
        git = git_info()
        if git["status"] or git["commit"] != p["prepared_git"]["commit"]:
            raise ValueError("Run requires the clean exact prepared child commit")
        if dependency_identity() != p["dependencies"] or source_hashes() != p["sources_sha256"]:
            raise ValueError("Frozen dependencies or product/evaluation sources differ")
        for item in [p["manifest"], *p["models"].values(), *p["configuration_files"].values(),
                     *(c["audio_identity"] for c in p["cases"])]:
            check_file(item)
        if p["hotwords"]:
            check_file(p["hotwords"])


class PcmInputError(ValueError):
    stage = "pcm"

    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def read_pcm16_mono(path):
    import numpy as np
    from edge_smart_home.audio.validation import validate_audio_samples

    try:
        with wave.open(str(path), "rb") as source:
            channels, width, rate, frames = (source.getnchannels(), source.getsampwidth(),
                                             source.getframerate(), source.getnframes())
            if source.getcomptype() != "NONE" or channels != 1 or width != 2:
                raise PcmInputError("PCM_FORMAT_UNSUPPORTED", "Expected uncompressed mono PCM16 WAV")
            raw = source.readframes(frames)
    except (wave.Error, EOFError) as error:
        raise PcmInputError("PCM_WAV_INVALID", str(error)) from error
    if len(raw) != frames * channels * width:
        raise PcmInputError("PCM_TRUNCATED", "WAV declared frames exceed available PCM samples")
    pcm = np.frombuffer(raw, dtype="<i2")
    samples = validate_audio_samples(pcm.astype(np.float32) / 32768.0, rate)
    return samples, rate, {"channels": channels, "sample_width_bytes": width, "sample_rate": rate,
                          "frames": frames, "duration_seconds": frames / rate,
                          "pcm_conversion": "signed little-endian PCM16 / 32768 -> mono float32",
                          "peak_abs": float(np.max(np.abs(samples))),
                          "rms": float(np.sqrt(np.mean(samples.astype(np.float64) ** 2))),
                          "full_scale_sample_count": int(np.count_nonzero((pcm == -32768) | (pcm == 32767)))}


def text_probe(text, configs):
    """Resolve only: constructing Mock is explicit; backend.execute is forbidden."""
    started = perf_counter()
    code = input_error_code(text)
    if code is not None:
        return {"status": "input_rejected", "error_code": str(code.value if hasattr(code, "value") else code),
                "commands": [], "scene_id": None, "error": None, "wall_ms": (perf_counter() - started) * 1000,
                "device_executions": 0, "model_service_calls": 0, "safety_execution_check_performed": False}
    devices, routing = configs["devices"], configs["routing"]

    class NoExecuteMock(MockDeviceBackend):
        def execute(self, command):
            raise AssertionError("W14 resolver must never execute a device")

    pipeline = TextControlPipeline(
        FastPathRouter(devices, configs["intents"], routing.get("default_devices", {})),
        SceneRouter(configs["scenes"]), NoExecuteMock(devices),
        SafetyPolicy(configs["safety"].get("action_risks", {})), CommandDeduplicator(0))
    event = PerceptionEvent(source=EventSource.VOICE, label=text)
    try:
        scene_id, commands = pipeline.resolve_event(event)
        result = {"status": "resolved_unexecuted", "scene_id": scene_id, "error_code": None,
                  "commands": [{"device_id": c.device_id, "action": c.action, "arguments": dict(c.arguments)} for c in commands],
                  "error": None}
    except RequestPlanConsistencyError as error:
        result = {"status": error.result.kind.value, "scene_id": None, "commands": [],
                  "error_code": error.error_code, "error": str(error)}
    except UnsupportedCommandError as error:
        result = {"status": "unroutable", "scene_id": None, "commands": [],
                  "error_code": "CAPABILITY_UNAVAILABLE", "error": str(error)}
    except Exception as error:
        result = {"status": "error", "scene_id": None, "commands": [],
                  "error_code": getattr(error, "error_code", None) or "TEXT_PIPELINE_ERROR",
                  "error": f"{type(error).__name__}: {error}"}
    result.update(wall_ms=(perf_counter() - started) * 1000, device_executions=0,
                  model_service_calls=0, safety_execution_check_performed=False)
    return result


def recognize_case(case, p, asr):
    row = {"id": case["id"], "source_kind": case["source_kind"], "expected": case["expected"],
           "source": case, "completed": False, "recognizer_output": None, "voice_normalized": None,
           "errors": [], "timing": {}, "pcm": None, "recognizer_metrics": None,
           "text_probe": None, "reference_text_probe": None, "metrics_recognizer": None, "metrics_voice": None,
           "live_e2e_ms": None, "live_overflow_count": None, "independent_vad_applied": False,
           "asr_call_attempted": False}
    stage, started = "pcm", perf_counter()
    try:
        samples, rate, row["pcm"] = read_pcm16_mono(Path(case["audio_identity"]["path"]))
        row["timing"]["pcm_read_convert_wall_ms"] = (perf_counter() - started) * 1000
        stage, started = "asr", perf_counter()
        chunk_ms = int(p["configs"]["audio"]["chunk_duration_ms"])
        row["asr_call_attempted"] = True
        recognition = asr.transcribe_waveform(samples, rate, chunk_ms)
        row["timing"]["asr_replay_wall_ms"] = (perf_counter() - started) * 1000
        row["recognizer_metrics"] = {"audio_seconds": recognition.audio_seconds,
                                     "decode_seconds": recognition.decode_seconds, "decode_only_rtf": recognition.rtf}
        row["timing"]["decode_only_ms"] = recognition.decode_seconds * 1000
        row["recognizer_output"] = recognition.text
        row["voice_normalized"] = _normalize_transcript(recognition.text)
        row["metrics_recognizer"] = text_metrics(case["expected"], recognition.text)
        row["metrics_voice"] = text_metrics(case["expected"], row["voice_normalized"])
        if not row["voice_normalized"]:
            row["errors"].append({"stage": "asr_output", "code": "ASR_EMPTY_TRANSCRIPT", "detail": "No recognized text"})
        elif not row["metrics_recognizer"]["exact_match"]:
            row["errors"].append({"stage": "transcript", "code": "ASR_TRANSCRIPT_MISMATCH",
                                  "detail": "Normalized recognizer output differs from frozen reference"})
        stage = "text_resolver"
        row["reference_text_probe"] = text_probe(_normalize_transcript(case["expected"]), p["configs"])
        row["text_probe"] = text_probe(row["voice_normalized"], p["configs"])
        row["timing"]["text_resolver_wall_ms"] = row["text_probe"]["wall_ms"]
        if row["text_probe"]["error_code"]:
            row["errors"].append({"stage": "text_resolver", "code": row["text_probe"]["error_code"],
                                  "detail": row["text_probe"]["status"]})
        row["completed"] = True
    except Exception as error:
        row["timing"][stage + "_failed_wall_ms"] = (perf_counter() - started) * 1000
        row["errors"].append({"stage": getattr(error, "stage", stage),
                              "code": getattr(error, "code", None) or ("PCM_READ_FAILED" if stage == "pcm" else "ASR_DECODE_FAILED" if stage == "asr" else "TEXT_PIPELINE_ERROR"),
                              "detail": f"{type(error).__name__}: {error}"})
    return row


def percentile(values, q):
    values = sorted(values)
    if not values:
        return None
    index = (len(values) - 1) * q
    left = math.floor(index)
    return values[left] + (values[math.ceil(index)] - values[left]) * (index - left)


def summarize_group(rows):
    completed = [r for r in rows if r["completed"]]
    result = {"recordings": len(rows), "completed": len(completed),
              "errors_by_stage_and_code": dict(sorted(Counter(e["stage"] + ":" + e["code"] for r in rows for e in r["errors"]).items()))}
    for kind in ("recognizer", "voice"):
        scored = [r["metrics_" + kind] for r in rows if r["metrics_" + kind] is not None]
        edits = sum(r["character_edits"] for r in scored)
        chars = sum(r["reference_characters"] for r in scored)
        result[kind] = {"scored": len(scored), "exact_matches": sum(r["exact_match"] for r in scored),
                        "character_edits": edits, "reference_characters": chars, "cer": edits / chars if chars else None}
    result["latency_ms"] = {key: {"n": len(values), "p50": percentile(values, .5), "p95": percentile(values, .95)}
                            for key in ("pcm_read_convert_wall_ms", "asr_replay_wall_ms", "decode_only_ms", "text_resolver_wall_ms")
                            for values in [[r["timing"][key] for r in rows if key in r["timing"]]]}
    rtfs = [r["recognizer_metrics"]["decode_only_rtf"] for r in rows if r["recognizer_metrics"] and r["recognizer_metrics"]["decode_only_rtf"] is not None]
    result["decode_only_rtf"] = {"n": len(rtfs), "p50": percentile(rtfs, .5), "p95": percentile(rtfs, .95)}
    result["text_resolver_status_counts"] = dict(Counter(r["text_probe"]["status"] for r in rows if r["text_probe"]))
    return result


def aggregate(p, rows, execution):
    case_ids = [c["id"] for c in p["cases"]]
    licensed = [r for r in rows if r["completed"] and r["source_kind"] in ("human", "synthetic")]
    gates = {"all_frozen_cases_recorded_once": [r["id"] for r in rows] == case_ids,
             "all_audio_and_asr_runs_complete": all(r["completed"] for r in rows) and len(rows) == len(case_ids),
             "at_least_five_licensed_recordings_complete": len(licensed) >= 5,
             "no_internal_text_resolver_errors": all(r["text_probe"] is None or r["text_probe"]["status"] != "error" for r in rows),
             "no_global_runtime_failure": execution.get("error") is None,
             "waveform_attempt_count_matches": execution["asr_calls"] == sum(r["asr_call_attempted"] for r in rows),
             "zero_real_device_and_model_service_calls": execution["real_device_executions"] == execution["model_service_calls"] == 0}
    return {"schema_version": 1, "kind": "w14_asr_file_replay_results", "passed": all(gates.values()),
            "engineering_gates": gates, "runtime_source_commit": p["prepared_git"]["commit"],
            "overall": summarize_group(rows),
            "by_source_kind": {kind: summarize_group([r for r in rows if r["source_kind"] == kind]) for kind in SOURCE_KINDS},
            "model_load_wall_ms": execution["model_load_wall_ms"], "asr_calls": execution["asr_calls"],
            "asr_calls_definition": "Independent transcribe_waveform invocations attempted, not native decode_stream iterations or network requests",
            "warmups": 0, "retries": 0, "real_device_executions": 0, "mock_device_executions": 0,
            "model_service_calls": 0, "independent_vad_applied": False,
            "live_e2e_ms": None, "live_overflow_count": None, "personal_understanding_accepted": False,
            "latency_policy": {"mode": "offline file replay, no real-time pacing", "model_load_excluded_from_per_clip": True,
                               "pcm_read_convert_wall_ms": "WAV parse/read/PCM16-to-float validation", "asr_replay_wall_ms": "fresh stream + chunk accept/resample/decode + endpoint/reset + tail padding/EOF",
                               "decode_only_ms": "existing adapter decode-ready loops; excludes accept/resampling and is nested within replay wall",
                               "text_resolver_wall_ms": "voice-normalized text through fresh Mock-backed resolver; no execution",
                               "percentile": "linear interpolation at (n-1)*q; only five or few different clips, not repeated performance benchmark"},
            "acceptance_policy": p["acceptance_policy"], "limitations": p["limitations"]}


def markdown_report(summary, rows):
    lines = ["# W14 ASR file replay", "", "Engineering evidence passed: " + str(summary["passed"]), "",
             "| Source kind | Recordings | Completed | Recognizer exact | CER |", "|---|---:|---:|---:|---:|"]
    for kind, group in summary["by_source_kind"].items():
        score = group["recognizer"]
        lines.append(f"| {kind} | {group['recordings']} | {group['completed']} | {score['exact_matches']}/{score['scored']} | {score['cer']} |")
    lines += ["", "| ID | Source | Reference | Recognizer output (hotword-biased) | Voice normalized | Errors |", "|---|---|---|---|---|---|"]
    for r in rows:
        cells = [r["id"], r["source_kind"], r["expected"], r["recognizer_output"] or "", r["voice_normalized"] or "",
                 ", ".join(e["stage"] + ":" + e["code"] for e in r["errors"])]
        lines.append("| " + " | ".join(str(c).replace("|", "\\|").replace("\n", " ") for c in cells) + " |")
    lines += ["", "| Timing (ms) | n | P50 | P95 |", "|---|---:|---:|---:|"]
    for key, timing in summary["overall"]["latency_ms"].items():
        lines.append(f"| {key} | {timing['n']} | {timing['p50']} | {timing['p95']} |")
    lines += ["", "## Measurement boundaries", ""] + ["- " + note for note in summary["limitations"]]
    return "\n".join(lines) + "\n"


def native_asr(p):
    from edge_smart_home.audio.sherpa_onnx_adapters import SherpaOnnxStreamingAsr
    model = p["models"]
    return SherpaOnnxStreamingAsr(*(Path(model[name]["path"]) for name in ("tokens", "encoder", "decoder", "joiner")),
                                  int(p["configs"]["audio"]["sample_rate"]), p["configs"]["audio"]["asr"],
                                  Path(p["hotwords"]["path"]) if p["hotwords"] else None)


def run(args, asr_factory=None):
    args.outdir.mkdir(parents=True, exist_ok=False)
    p = read_json(args.protocol)
    validate_protocol(p)
    write_json(args.outdir / "protocol.json", p)
    (args.outdir / "records").mkdir()
    execution = {"started_at": now(), "finished_at": None, "runtime_git": git_info(), "error": None,
                 "model_load_wall_ms": None, "asr_calls": 0, "model_service_calls": 0, "real_device_executions": 0}
    rows = []
    try:
        start = perf_counter()
        asr = (asr_factory or native_asr)(p)
        execution["model_load_wall_ms"] = (perf_counter() - start) * 1000
        validation = getattr(asr, "hotword_validation", None)
        execution["hotword_validation"] = ({"valid": list(validation.valid), "invalid": list(validation.invalid)}
                                            if validation is not None else None)
        with (args.outdir / "ledger.jsonl").open("x", encoding="utf-8") as ledger:
            for index, case in enumerate(p["cases"]):
                ledger.write(json.dumps({"event": "case_started", "index": index, "id": case["id"], "at": now()}) + "\n"); ledger.flush()
                row = recognize_case(case, p, asr)
                execution["asr_calls"] += int(row["asr_call_attempted"])
                rows.append(row)
                write_json(args.outdir / "records" / f"{index:04d}.json", row)
                ledger.write(json.dumps({"event": "case_recorded", "index": index, "id": case["id"], "completed": row["completed"], "at": now()}) + "\n"); ledger.flush()
                print(f"[{index + 1}/{len(p['cases'])}] {case['id']} completed={row['completed']}", flush=True)
    except Exception as error:
        execution["error"] = {"code": "ASR_RUNTIME_FAILED", "detail": f"{type(error).__name__}: {error}"}
    execution["finished_at"] = now()
    write_json(args.outdir / "execution.json", execution)
    raw_paths = [args.outdir / "protocol.json", args.outdir / "execution.json", *sorted((args.outdir / "records").glob("*.json"))]
    if (args.outdir / "ledger.jsonl").is_file():
        raw_paths.append(args.outdir / "ledger.jsonl")
    write_json(args.outdir / "raw-index.json", {"artifacts": [{"path": str(path.relative_to(args.outdir)), "bytes": path.stat().st_size,
                                                               "sha256": sha256(path)} for path in raw_paths]})
    summary = aggregate(p, rows, execution)
    write_json(args.outdir / "summary.json", summary)
    (args.outdir / "report.md").write_text(markdown_report(summary, rows), encoding="utf-8")
    return summary


def report(args):
    args.outdir.mkdir(parents=True, exist_ok=False)
    artifacts = read_json(args.run / "raw-index.json")["artifacts"]
    expected = {"protocol.json", "execution.json", *(str(path.relative_to(args.run)) for path in (args.run / "records").glob("*.json"))}
    if (args.run / "ledger.jsonl").is_file():
        expected.add("ledger.jsonl")
    if len(artifacts) != len(expected) or {a["path"] for a in artifacts} != expected:
        raise ValueError("Raw evidence index does not cover the exact protocol/execution/ledger/record set")
    for item in artifacts:
        path = args.run / item["path"]
        if path.stat().st_size != item["bytes"] or sha256(path) != item["sha256"]:
            raise ValueError(f"Raw evidence differs: {path}")
    p = read_json(args.run / "protocol.json")
    validate_protocol(p, check_current=False)
    rows = [read_json(path) for path in sorted((args.run / "records").glob("*.json"))]
    for index, row in enumerate(rows):
        if (index >= len(p["cases"]) or row["source"] != p["cases"][index]
                or row["id"] != p["cases"][index]["id"]
                or row["expected"] != row["source"]["expected"]
                or row["source_kind"] != row["source"]["source_kind"]):
            raise ValueError("Recorded source differs from the frozen case order")
        if row["recognizer_output"] is not None:
            if (row["voice_normalized"] != _normalize_transcript(row["recognizer_output"])
                    or row["metrics_recognizer"] != text_metrics(row["expected"], row["recognizer_output"])
                    or row["metrics_voice"] != text_metrics(row["expected"], row["voice_normalized"])):
                raise ValueError("Stored transcription metrics or voice normalization differ from recomputation")
        elif row["metrics_recognizer"] is not None or row["metrics_voice"] is not None:
            raise ValueError("Missing recognizer output must not have scored transcript metrics")
    ledger = ([json.loads(line) for line in (args.run / "ledger.jsonl").read_text().splitlines()]
              if (args.run / "ledger.jsonl").is_file() else [])
    if len(ledger) != 2 * len(rows):
        raise ValueError("Ledger count differs from recorded waveform attempts")
    for index, row in enumerate(rows):
        for offset, event in enumerate(("case_started", "case_recorded")):
            item = ledger[2 * index + offset]
            if (item.get("event"), item.get("index"), item.get("id")) != (event, index, row["id"]):
                raise ValueError("Ledger order/ID differs from frozen cases")
            datetime.fromisoformat(item["at"])
        if ledger[2 * index + 1].get("completed") != row["completed"]:
            raise ValueError("Ledger completion differs from the recorded result")
    summary = aggregate(p, rows, read_json(args.run / "execution.json"))
    write_json(args.outdir / "summary.json", summary)
    (args.outdir / "report.md").write_text(markdown_report(summary, rows), encoding="utf-8")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare_parser = sub.add_parser("prepare")
    prepare_parser.add_argument("--manifest", type=Path, required=True)
    prepare_parser.add_argument("--config-dir", type=Path, default=ROOT / "configs")
    prepare_parser.add_argument("--model-dir", type=Path)
    for name in ("tokens", "encoder", "decoder", "joiner"):
        prepare_parser.add_argument("--" + name, type=Path)
    prepare_parser.add_argument("--hotwords", type=Path, default=ROOT / "configs/hotwords_v2.txt")
    prepare_parser.add_argument("--no-hotwords", action="store_true")
    for command in (prepare_parser, sub.add_parser("run"), sub.add_parser("report")):
        command.add_argument("--outdir", type=Path, required=True)
    sub.choices["run"].add_argument("--protocol", type=Path, required=True)
    sub.choices["report"].add_argument("--run", type=Path, required=True)
    args = parser.parse_args()
    if getattr(args, "no_hotwords", False):
        args.hotwords = None
    try:
        value = {"prepare": prepare, "run": run, "report": report}[args.command](args)
    except Exception as error:
        print(json.dumps({"passed": False, "error": f"{type(error).__name__}: {error}"}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0 if value.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
