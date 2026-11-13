"""Frozen file audio -> native ASR -> existing Voice -> Mock readback.

Primary audio scores and independent reference-text controls stay separate.
Console feedback is captured, never played; no real HA, microphone or Ollama.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / "src"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from scripts import evaluate_w14_asr as ASR
from scripts import evaluate_w15_devices as DEV
from scripts.run_unit_tests import network_disabled
from edge_smart_home.audio.feedback import AssistantFeedback
from edge_smart_home.audio.interfaces import StreamingRecognition
from edge_smart_home.audio.pipeline import VoiceControlPipeline, _normalize_transcript
from edge_smart_home.audio.sherpa_onnx_adapters import VoiceDependencyError
from edge_smart_home.benchmark.asr_evaluation import text_metrics

CONFIG_NAMES = ("audio", *DEV.CONFIG_NAMES)
SOURCE_KINDS = ("human", "synthetic_audio", "synthetic_signal", "unknown")
TIMING_KEYS = ("pcm_read_convert_wall_ms", "asr_replay_wall_ms", "decode_only_ms", "voice_control_feedback_wall_ms",
               "explicit_final_readback_wall_ms", "console_feedback_capture_wall_ms", "file_audio_to_readback_wall_ms")
LIMITATIONS = [
    "Primary observations decode real WAV bytes with the frozen native ASR; Mock unit tests are separate evidence.",
    "Synthetic audio/signal, public or self-recorded human speech and synthetic transcripts are distinct; missing human home-command recordings stay pending.",
    "Recognizer output includes fixed decoder/hotword search bias; no output-driven retries, selection, reference edits or tuning.",
    "Reference text runs on a new independent Mock through handle_text; it is neither recognized audio nor an automatic retry/fallback of the primary command.",
    "Console feedback is captured synchronously; it is not an audible TTS, acoustic echo or playback latency measurement.",
    "Offline file audio-to-readback wall excludes model load and per-case fixture/setup; live E2E, endpoint ground truth and overflow are unmeasured.",
    "All device states/readbacks are Mock; request acceptance, HA observation and physical completion are not interchangeable.",
    "Observed development cases do not establish general ASR, production safety, real HA, ARM or personal-understanding acceptance.",
]


def now():
    return datetime.now(timezone.utc).isoformat()


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    with Path(path).open("x") as output:
        json.dump(value, output, ensure_ascii=False, indent=2, allow_nan=False)
        output.write("\n")


def policy():
    return {"minimum_distinct_normal_cases": 5, "minimum_distinct_abnormal_cases": 5,
            "one_asr_attempt_per_clip": True, "warmups": 0, "retries": 0,
            "accuracy_100_percent_required_for_evidence_integrity": False,
            "primary_semantics_and_abnormal_state_preservation_reported_separately": True,
            "real_ha_calls": 0, "ollama_calls": 0, "microphone_calls": 0,
            "feedback": "captured console text only"}


def validate_cases(data):
    if data.get("schema_version") != 1 or data.get("kind") != "w16_voice_home_cases":
        raise ValueError("Expected fixed W16 voice-home cases")
    cases = data.get("cases")
    if not isinstance(cases, list) or len(cases) < 10:
        raise ValueError("At least five normal and five abnormal cases required")
    ids = set()
    for c in cases:
        if not isinstance(c, dict) or not isinstance(c.get("id"), str) or not c["id"] or c["id"] in ids:
            raise ValueError("Unique literal case IDs required")
        ids.add(c["id"])
        if c.get("category") not in ("normal", "abnormal") or not isinstance(c.get("reference_text"), str):
            raise ValueError("Case category/reference text invalid")
        if not c["reference_text"] and c.get("audio_kind") != "silence":
            raise ValueError("Only explicit silence has an empty reference")
        e = c.get("expect", {})
        if (not isinstance(e.get("status"), str) or "error_code" not in e
                or not isinstance(e.get("attributes"), dict) or type(e.get("unchanged")) is not bool):
            raise ValueError("Every case must freeze status/code/state and unchanged oracle")
        for step in c.get("setup", []):
            if "availability" in step:
                if type(step["availability"].get("value")) is not bool:
                    raise ValueError("Fixture availability must be boolean")
            elif "command" not in step:
                raise ValueError("Unknown fixture operation")
    if any(sum(c["category"] == kind for c in cases) < 5 for kind in ("normal", "abnormal")):
        raise ValueError("At least five distinct cases in each category required")
    return cases


def manifest_clips(data, path, cases):
    if data.get("schema_version") != 1 or data.get("kind") != "w16_audio_manifest":
        raise ValueError("Expected W16 audio manifest")
    case_map = {c["id"]: c for c in cases}
    result, ids = [], set()
    for source in data.get("clips", []):
        if not isinstance(source, dict) or source.get("case_id") not in case_map:
            raise ValueError("Audio must identify a frozen case")
        clip = dict(source)
        clip.setdefault("id", clip["case_id"])
        if not isinstance(clip["id"], str) or clip["id"] in ids:
            raise ValueError("Unique clip IDs required")
        ids.add(clip["id"])
        if clip.get("source_kind") not in SOURCE_KINDS:
            raise ValueError("Primary input must be human, synthetic audio/signal or unknown WAV, never a transcript/Mock")
        for key in ("wav", "source_url", "license", "attribution", "provenance_note", "sha256"):
            if not isinstance(clip.get(key), str) or not clip[key].strip():
                raise ValueError("Missing audio provenance: " + key)
        if not re.fullmatch("[0-9a-f]{64}", clip["sha256"]):
            raise ValueError("Literal WAV SHA256 required")
        identity = ASR.file_identity(ASR.resolve_path(clip["wav"], path))
        if identity["sha256"] != clip["sha256"]:
            raise ValueError("Audio hash mismatch")
        if case_map[clip["case_id"]].get("audio_kind") == "silence" and clip["source_kind"] != "synthetic_signal":
            raise ValueError("Program silence must remain synthetic_signal, never human speech")
        result.append({**clip, "audio_identity": identity})
    if {r["case_id"] for r in result} != set(case_map):
        raise ValueError("Every frozen normal/abnormal case must have audio")
    if any(not any(c["case_id"] == case["id"] and c["source_kind"] != "unknown" for c in result) for case in cases):
        raise ValueError("Unknown-origin inputs cannot satisfy the required case coverage")
    return result


def prepare(args):
    args.outdir.mkdir(parents=True, exist_ok=False)
    source_git = DEV.git()
    if source_git["status"]:
        raise ValueError("Prepare requires clean committed child sources")
    cases = validate_cases(read(args.cases))
    manifest = read(args.manifest)
    if manifest.get("preparation_kind") != "native_tts_audio":
        raise ValueError("Formal synthetic audio preparation must be native TTS, never a Fake unit fixture")
    clips = manifest_clips(manifest, args.manifest.resolve(), cases)
    config_dir = args.config_dir.resolve()
    configs = {name: read(config_dir / (name + ".json")) for name in CONFIG_NAMES}
    models = {name: ASR.file_identity(path) for name, path in ASR.model_paths(args).items()}
    inventory = config_dir / "asr-model-source-v1.json"
    material_inventory = ASR.file_identity(config_dir / "w16-material-source-v1.json")
    if (manifest.get("cases_file", {}).get("sha256") != ASR.sha256(args.cases)
            or manifest.get("material_source_inventory", {}).get("sha256") != material_inventory["sha256"]):
        raise ValueError("Audio preparation must bind these exact frozen cases and audited material sources")
    p = {"schema_version": 1, "kind": "w16_frozen_voice_home_protocol", "state": "frozen", "created_at": now(),
         "source_git": source_git, "sources_sha256": DEV.sources(), "dependencies": ASR.dependency_identity(),
         "cases_file": ASR.file_identity(args.cases), "manifest": ASR.file_identity(args.manifest),
         "cases": cases, "clips": clips, "configs": configs,
         "configuration_files": [ASR.file_identity(config_dir / (name + ".json")) for name in CONFIG_NAMES],
         "models": models, "hotwords": ASR.file_identity(args.hotwords) if args.hotwords else None,
         "asr_source_inventory": ASR.file_identity(inventory), "asr_weight_license_verified": False,
         "material_source_inventory": material_inventory, "audio_preparation_kind": manifest["preparation_kind"],
         "backend_kind": "mock", "completion_scope": "mock_state_readback", "policy": policy(), "limitations": LIMITATIONS}
    write(args.outdir / "protocol.json", p)
    return {"passed": True, "clips": len(clips), "case_count": len(cases), "asr_calls": 0,
            "source_counts": dict(Counter(c["source_kind"] for c in clips))}


def validate_protocol(p, current=False):
    if (p.get("schema_version") != 1 or p.get("kind") != "w16_frozen_voice_home_protocol"
            or p.get("state") != "frozen" or p.get("policy") != policy() or p.get("backend_kind") != "mock"):
        raise ValueError("Expected unchanged frozen Mock-only W16 protocol")
    validate_cases({"schema_version": 1, "kind": "w16_voice_home_cases", "cases": p["cases"]})
    if p["source_git"]["status"] or "scripts/evaluate_w16_voice_home.py" not in p["sources_sha256"]:
        raise ValueError("Protocol must bind committed evaluator/product sources")
    clip_ids = [c["id"] for c in p["clips"]]
    if len(clip_ids) != len(set(clip_ids)) or {c["case_id"] for c in p["clips"]} != {c["id"] for c in p["cases"]}:
        raise ValueError("Frozen clip identity/coverage differs")
    for clip in p["clips"]:
        if (clip["source_kind"] not in SOURCE_KINDS or clip["audio_identity"]["sha256"] != clip["sha256"]
                or any(not isinstance(clip.get(k), str) or not clip[k].strip()
                       for k in ("id", "source_url", "license", "attribution", "provenance_note"))):
            raise ValueError("Frozen clip provenance differs")
    if p.get("audio_preparation_kind") != "native_tts_audio":
        raise ValueError("A unit Fake fixture cannot become formal audio evidence")
    if current:
        if DEV.git() != p["source_git"] or DEV.sources() != p["sources_sha256"] or ASR.dependency_identity() != p["dependencies"]:
            raise ValueError("Run requires exact clean prepared code/dependency identity")
        files = [p["cases_file"], p["manifest"], p["asr_source_inventory"], p["material_source_inventory"], *p["configuration_files"],
                 *p["models"].values(), *(c["audio_identity"] for c in p["clips"])]
        if p["hotwords"]:
            files.append(p["hotwords"])
        for item in files:
            ASR.check_file(item)


class NoInput:
    def __getattr__(self, name):
        raise AssertionError("File evaluator must not capture audio or use a second ASR: " + name)


class CapturedConsole:
    def __init__(self):
        self.messages, self.wall_ms = [], 0.0

    def speak(self, text):
        start = perf_counter()
        self.messages.append(text)
        self.wall_ms += (perf_counter() - start) * 1000


def fixture(case, config):
    backend = DEV.RecordingMock(config["devices"])
    for step in case.get("setup", []):
        if "availability" in step:
            value = step["availability"]
            backend.set_available(value["device_id"], value["value"])
        else:
            backend.execute(DEV.command(step["command"]))
    setup_records = backend.execution_records
    backend.execution_records = []
    speaker = CapturedConsole()
    orchestrator = DEV.make_orchestrator(backend, config)
    feedback = AssistantFeedback(speaker)
    voice = VoiceControlPipeline(NoInput(), NoInput(), orchestrator, feedback=feedback)
    return {"backend": backend, "before": DEV.snapshot(backend), "setup_executions": setup_records,
            "speaker": speaker, "orchestrator": orchestrator, "feedback": feedback, "voice": voice}


def finish_probe(context, result, *, wall_ms, outcome=None):
    started = perf_counter()
    after = DEV.snapshot(context["backend"])
    readback_ms = (perf_counter() - started) * 1000
    state = getattr(result, "state", None) if outcome is None else outcome
    trace = getattr(result, "trace", None) or getattr(getattr(result, "event", None), "trace", None)
    return {"status": result.status, "error_code": result.error_code, "error": result.error,
            "text": getattr(result, "text", None), "message": getattr(result, "message", None),
            "text_fallback": getattr(result, "text_fallback", None), "llm_calls": result.llm_calls,
            "feedback_error": getattr(result, "feedback_error", None),
            "diagnostic_error": getattr(result, "diagnostic_error", None),
            "before": context["before"], "after": after, "outcome": DEV.outcome_value(state),
            "execution_records": context["backend"].execution_records, "setup_executions": context["setup_executions"],
            "console_feedback": list(context["speaker"].messages), "feedback_capture_wall_ms": context["speaker"].wall_ms,
            "control_feedback_wall_ms": wall_ms, "final_snapshot_wall_ms": readback_ms,
            "trace": trace.snapshot() if trace is not None else None,
            "backend_kind": "mock", "completion_scope": "mock_state_readback"}


def reference_probe(case, config):
    if not case["reference_text"]:
        return None  # An empty ASR reference is not a text command or text success.
    context = fixture(case, config)
    started = perf_counter()
    result = context["orchestrator"].handle_text(case["reference_text"])
    context["feedback"].response(result)
    result_value = finish_probe(context, result, wall_ms=(perf_counter() - started) * 1000, outcome=result.outcome)
    result_value["input_kind"] = "authored_reference_text_on_independent_mock"
    result_value["checks"] = check_probe(result_value, case)
    return result_value


def check_probe(probe, case):
    expected = case["expect"]
    attributes_match = all(device in probe["after"] and all(
        key in probe["after"][device]["attributes"] and type(probe["after"][device]["attributes"][key]) is type(value)
        and probe["after"][device]["attributes"][key] == value for key, value in attrs.items())
        for device, attrs in expected["attributes"].items())
    touched = {r["command"]["device_id"] for r in probe["execution_records"]}
    checks = {"status": probe["status"] == expected["status"], "error_code": probe["error_code"] == expected["error_code"],
              "expected_attributes": attributes_match,
              "returned_matches_explicit_readback": all(r["returned"] is None or r["returned"] == r["readback"] for r in probe["execution_records"]),
              "failed_execution_has_no_success_snapshot": all(r["error_code"] is None or r["returned"] is None for r in probe["execution_records"]),
              "unrelated_devices_unchanged": all(probe["after"][d] == state for d, state in probe["before"].items() if d not in touched),
              "zero_llm_calls": probe["llm_calls"] == 0,
              "mock_scope": all(s["backend_kind"] == "mock" and s["completion_scope"] == "mock_state_snapshot" for s in probe["after"].values()),
              "feedback_recorded": bool(probe["console_feedback"]) and probe["feedback_error"] is None}
    if expected["unchanged"]:
        checks["whole_state_unchanged"] = probe["before"] == probe["after"]
    return checks


def recognize_case(clip, case, p, asr):
    context = fixture(case, p["configs"])
    row = {"id": clip["id"], "case": case, "clip": clip, "category": case["category"], "source_kind": clip["source_kind"],
           "input_kind": "wav_audio", "completed": False, "asr_attempted": False,
           "recognizer_output": None, "voice_normalized": None, "pcm": None, "asr_metrics": None,
           "text_metrics": None, "timing": {}, "primary": None, "reference_text_control": None, "errors": [],
           "live_e2e_ms": None, "live_overflow_count": None, "independent_vad_applied": False}
    started_all = perf_counter()
    stage, started = "pcm", perf_counter()
    try:
        samples, rate, row["pcm"] = ASR.read_pcm16_mono(Path(clip["audio_identity"]["path"]))
        row["timing"]["pcm_read_convert_wall_ms"] = (perf_counter() - started) * 1000
        stage, started = "asr_decode", perf_counter()
        row["asr_attempted"] = True
        recognition = asr.transcribe_waveform(samples, rate, int(p["configs"]["audio"]["chunk_duration_ms"]))
        row["timing"]["asr_replay_wall_ms"] = (perf_counter() - started) * 1000
        row["asr_metrics"] = {"audio_seconds": recognition.audio_seconds, "decode_seconds": recognition.decode_seconds,
                              "decode_only_rtf": recognition.rtf}
        row["timing"]["decode_only_ms"] = recognition.decode_seconds * 1000
        row["recognizer_output"] = recognition.text
        row["voice_normalized"] = _normalize_transcript(recognition.text)
        row["text_metrics"] = text_metrics(case["reference_text"], recognition.text)
        stage, started = "voice_control", perf_counter()
        result = context["voice"].process_recognition(recognition, report_empty=True)
        row["primary"] = finish_probe(context, result, wall_ms=(perf_counter() - started) * 1000)
        row["completed"] = True
    except Exception as error:
        row["timing"][stage + "_failed_wall_ms"] = (perf_counter() - started) * 1000
        row["errors"].append({"stage": getattr(error, "stage", stage),
                              "code": getattr(error, "code", None) or getattr(error, "error_code", None) or "VOICE_CHAIN_INTERNAL_ERROR",
                              "detail": f"{type(error).__name__}: {error}"})
        # Product error rendering supplies the same explicit text fallback.
        if stage in ("pcm", "asr_decode"):
            result = context["voice"].process_audio_error(error, stage="audio_input" if stage == "pcm" else "asr_decode")
            row["primary"] = finish_probe(context, result, wall_ms=0.0)
    row["timing"]["file_audio_to_readback_wall_ms"] = (perf_counter() - started_all) * 1000
    try:
        row["reference_text_control"] = reference_probe(case, p["configs"])
    except Exception as error:
        row["errors"].append({"stage": "reference_text_control", "code": "REFERENCE_TEXT_CONTROL_FAILED",
                              "detail": f"{type(error).__name__}: {error}"})
    if row["primary"]:
        row["timing"]["voice_control_feedback_wall_ms"] = row["primary"]["control_feedback_wall_ms"]
        row["timing"]["explicit_final_readback_wall_ms"] = row["primary"]["final_snapshot_wall_ms"]
        row["timing"]["console_feedback_capture_wall_ms"] = row["primary"]["feedback_capture_wall_ms"]
        row["primary"]["checks"] = check_probe(row["primary"], case)
        row["primary_semantics_passed"] = all(row["primary"]["checks"].values())
        if row["primary"]["error_code"]:
            row["errors"].append({"stage": "voice_or_intent_device", "code": row["primary"]["error_code"], "detail": row["primary"]["status"]})
        if not row["primary_semantics_passed"]:
            row["errors"].append({"stage": "semantic", "code": "AUDIO_TO_STATE_ORACLE_MISMATCH", "detail": "Frozen primary case oracle did not match"})
    else:
        row["primary_semantics_passed"] = False
    if row["text_metrics"] and not row["text_metrics"]["exact_match"]:
        row["errors"].append({"stage": "transcript", "code": "ASR_TRANSCRIPT_MISMATCH", "detail": "Frozen normalization/reference mismatch"})
    return row


def aggregate(p, rows, execution):
    def group(values):
        metrics = [r["text_metrics"] for r in values if r["text_metrics"]]
        speech_metrics = [r["text_metrics"] for r in values if r["text_metrics"] and r["case"]["reference_text"]]
        characters = sum(m["reference_characters"] for m in metrics)
        edits = sum(m["character_edits"] for m in metrics)
        latency = {}
        for key in TIMING_KEYS:
            samples = [r["timing"][key] for r in values if key in r["timing"]]
            latency[key] = {"n": len(samples), "p50": ASR.percentile(samples, .5), "p95": ASR.percentile(samples, .95)}
        return {"observations": len(values), "completed": sum(r["completed"] for r in values),
                "primary_semantics_passed": sum(r["primary_semantics_passed"] for r in values),
                "text_exact_including_empty_signal": sum(m["exact_match"] for m in metrics),
                "speech_recognizer_exact": sum(m["exact_match"] for m in speech_metrics),
                "speech_scored": len(speech_metrics), "character_edits": edits,
                "reference_characters": characters, "cer": edits / characters if characters else None,
                "latency_ms": latency,
                "error_counts": dict(Counter(e["stage"] + ":" + e["code"] for r in values for e in r["errors"]))}
    primary = [r["primary"] for r in rows if r["primary"]]
    reference = [r["reference_text_control"] for r in rows if r["reference_text_control"]]
    case_map = {c["id"]: c for c in p["cases"]}
    expected_reference_count = sum(bool(case_map[c["case_id"]]["reference_text"]) for c in p["clips"])
    gates = {"frozen_clip_order_and_count": [r["id"] for r in rows] == [c["id"] for c in p["clips"]],
             "all_audio_and_asr_attempts_complete": len(rows) == len(p["clips"]) and all(r["completed"] for r in rows),
             "all_primary_returns_match_explicit_readback": all(v["checks"]["returned_matches_explicit_readback"] for v in primary),
             "all_reference_text_controls_match_frozen_oracles": len(reference) == expected_reference_count and all(all(v["checks"].values()) for v in reference),
             "zero_network_attempts": execution["network_attempts"] == [], "no_global_failure": execution.get("error") is None}
    return {"schema_version": 1, "kind": "w16_file_audio_mock_readback_results", "passed": all(gates.values()),
            "engineering_gates": gates, "runtime_source_commit": p["source_git"]["commit"],
            "primary_all_semantics_passed": len(rows) == len(p["clips"]) and all(r["primary_semantics_passed"] for r in rows),
            "abnormal_primary_state_preserved": all(r["primary"] and r["primary"]["before"] == r["primary"]["after"] for r in rows if r["category"] == "abnormal"),
            "overall": group(rows), "by_category": {k: group([r for r in rows if r["category"] == k]) for k in ("normal", "abnormal")},
            "by_source_kind": {k: group([r for r in rows if r["source_kind"] == k]) for k in SOURCE_KINDS},
            "reference_text_controls": len(reference), "reference_text_oracle_matches": sum(all(v["checks"].values()) for v in reference),
            "asr_attempts": sum(r["asr_attempted"] for r in rows), "model_load_wall_ms": execution["model_load_wall_ms"],
            "primary_mock_execute_attempts": sum(len(v["execution_records"]) for v in primary),
            "reference_mock_execute_attempts": sum(len(v["execution_records"]) for v in reference),
            "network_attempts": len(execution["network_attempts"]), "real_ha_calls": 0, "ollama_calls": 0, "microphone_calls": 0,
            "physical_device_calls": 0, "audible_feedback_calls": 0, "warmups": 0, "retries": 0,
            "human_home_recording_accepted": False, "asr_weight_license_verified": p["asr_weight_license_verified"],
            "overflow_resolution_accepted": False, "personal_understanding_accepted": False,
            "live_e2e_ms": None,
            "latency_policy": {"mode": "offline file replay without real-time pacing",
                               "model_load_and_fixture_setup_excluded": True,
                               "file_audio_to_readback_wall_ms": "PCM read/convert, ASR, software metric/dispatch work, Voice/control/console feedback and explicit final Mock snapshot; excludes independent reference probe",
                               "decode_only_ms": "nested inside ASR replay, excludes accept/resample",
                               "voice_control_feedback_wall_ms": "Voice normalization, intent/Mock execution/immediate readback and synchronous console feedback",
                               "explicit_final_readback_wall_ms": "final list_states snapshot of all Mock devices; additional per-execution get_state is recorded inside control",
                               "console_feedback_capture_wall_ms": "nested inside Voice/control; text capture only, not TTS/acoustic playback",
                               "percentiles": "linear interpolation across these different clips once each, not a repeated benchmark"},
            "policy": p["policy"], "limitations": p["limitations"]}


def markdown(summary, rows):
    lines = ["# W16 audio to Mock readback", "", "Engineering evidence passed: " + str(summary["passed"]),
             "Primary all semantic oracles passed: " + str(summary["primary_all_semantics_passed"]), "",
             "| ID | Source | Category | Recognizer | Status | Code | Primary oracle |", "|---|---|---|---|---|---|---|"]
    for r in rows:
        primary = r["primary"] or {}
        cells = [r["id"], r["source_kind"], r["category"], r["recognizer_output"], primary.get("status"), primary.get("error_code"), r["primary_semantics_passed"]]
        lines.append("| " + " | ".join(str(v or "").replace("|", "\\|").replace("\n", " ") for v in cells) + " |")
    lines += ["", "## Boundaries", ""] + ["- " + note for note in summary["limitations"]]
    return "\n".join(lines) + "\n"


def run(args, asr_factory=None):
    args.outdir.mkdir(parents=True, exist_ok=False)
    p = read(args.protocol)
    validate_protocol(p, current=True)
    write(args.outdir / "protocol.json", p)
    (args.outdir / "records").mkdir()
    execution = {"started_at": now(), "finished_at": None, "source_git": DEV.git(), "network_attempts": [],
                 "model_load_wall_ms": None, "error": None}
    rows = []
    cases = {c["id"]: c for c in p["cases"]}
    stage = "evidence_output"
    with network_disabled() as attempts:
        try:
            (args.outdir / "ledger.jsonl").touch(exist_ok=False)
            stage = "asr_initialization"
            started = perf_counter()
            asr = (asr_factory or ASR.native_asr)(p)
            execution["model_load_wall_ms"] = (perf_counter() - started) * 1000
            validation = getattr(asr, "hotword_validation", None)
            execution["hotword_validation"] = ({"valid": list(validation.valid), "invalid": list(validation.invalid)} if validation is not None else None)
            stage = "file_audio_loop"
            with (args.outdir / "ledger.jsonl").open("a") as ledger:
                for index, clip in enumerate(p["clips"]):
                    ledger.write(json.dumps({"event": "clip_started", "index": index, "id": clip["id"]}) + "\n"); ledger.flush()
                    row = recognize_case(clip, cases[clip["case_id"]], p, asr)
                    rows.append(row)
                    write(args.outdir / "records" / f"{index:04d}.json", row)
                    ledger.write(json.dumps({"event": "clip_recorded", "index": index, "id": clip["id"], "completed": row["completed"]}) + "\n"); ledger.flush()
        except Exception as error:
            execution["error"] = {"stage": stage, "type": type(error).__name__,
                                  "code": ("ASR_DEPENDENCY_UNAVAILABLE" if isinstance(error, (VoiceDependencyError, ImportError))
                                           else "ASR_INITIALIZATION_FAILED" if stage == "asr_initialization" else "VOICE_EVIDENCE_INTERNAL_ERROR"),
                                  "detail": str(error)}
        execution["network_attempts"] = list(attempts)
    execution["finished_at"] = now()
    write(args.outdir / "execution.json", execution)
    paths = [args.outdir / "protocol.json", args.outdir / "execution.json", *sorted((args.outdir / "records").glob("*.json"))]
    if (args.outdir / "ledger.jsonl").exists():
        paths.append(args.outdir / "ledger.jsonl")
    write(args.outdir / "raw-index.json", {"artifacts": [{"path": str(path.relative_to(args.outdir)), "bytes": path.stat().st_size,
                                                         "sha256": ASR.sha256(path)} for path in paths]})
    summary = aggregate(p, rows, execution)
    write(args.outdir / "summary.json", summary)
    (args.outdir / "report.md").write_text(markdown(summary, rows))
    return summary


def report(args):
    args.outdir.mkdir(parents=True, exist_ok=False)
    artifacts = read(args.run / "raw-index.json")["artifacts"]
    files = {"protocol.json", "execution.json", "ledger.jsonl", *(str(f.relative_to(args.run)) for f in (args.run / "records").glob("*.json"))}
    if len(artifacts) != len(files) or {a["path"] for a in artifacts} != files:
        raise ValueError("Raw index must cover exact protocol/execution/ledger/record set")
    for a in artifacts:
        f = args.run / a["path"]
        if f.stat().st_size != a["bytes"] or ASR.sha256(f) != a["sha256"]:
            raise ValueError("Raw evidence differs")
    p = read(args.run / "protocol.json")
    validate_protocol(p)
    rows = [read(f) for f in sorted((args.run / "records").glob("*.json"))]
    cases = {c["id"]: c for c in p["cases"]}
    for index, row in enumerate(rows):
        if (index >= len(p["clips"]) or row["clip"] != p["clips"][index]
                or row["id"] != row["clip"]["id"] or row["case"] != cases[row["clip"]["case_id"]]
                or row["source_kind"] != row["clip"]["source_kind"] or row["category"] != row["case"]["category"]
                or row["input_kind"] != "wav_audio"):
            raise ValueError("Recorded source/category/case differs from protocol")
        if row["recognizer_output"] is not None:
            if (row["voice_normalized"] != _normalize_transcript(row["recognizer_output"])
                    or row["text_metrics"] != text_metrics(row["case"]["reference_text"], row["recognizer_output"])):
                raise ValueError("Recorded transcript metrics differ")
        for name in ("primary", "reference_text_control"):
            probe = row[name]
            if probe is not None and probe["checks"] != check_probe(probe, row["case"]):
                raise ValueError("Recorded state/feedback oracle checks differ")
        if row["primary_semantics_passed"] != (row["primary"] is not None and all(check_probe(row["primary"], row["case"]).values())):
            raise ValueError("Recorded primary score differs")
    expected_ledger = [e for i, r in enumerate(rows) for e in (
        {"event": "clip_started", "index": i, "id": r["id"]},
        {"event": "clip_recorded", "index": i, "id": r["id"], "completed": r["completed"]})]
    ledger = [json.loads(line) for line in (args.run / "ledger.jsonl").read_text().splitlines()]
    if ledger != expected_ledger:
        raise ValueError("Recorded ledger differs")
    summary = aggregate(p, rows, read(args.run / "execution.json"))
    write(args.outdir / "summary.json", summary)
    (args.outdir / "report.md").write_text(markdown(summary, rows))
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="operation", required=True)
    prep = sub.add_parser("prepare")
    prep.add_argument("--cases", type=Path, default=ROOT / "configs/evaluation/w16-voice-home-v1.json")
    prep.add_argument("--manifest", type=Path, required=True)
    prep.add_argument("--config-dir", type=Path, default=ROOT / "configs")
    prep.add_argument("--model-dir", type=Path)
    for name in ("tokens", "encoder", "decoder", "joiner"):
        prep.add_argument("--" + name, type=Path)
    prep.add_argument("--hotwords", type=Path, default=ROOT / "configs/hotwords_v2.txt")
    for command in (prep, sub.add_parser("run"), sub.add_parser("report")):
        command.add_argument("--outdir", type=Path, required=True)
    sub.choices["run"].add_argument("--protocol", type=Path, required=True)
    sub.choices["report"].add_argument("--run", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = {"prepare": prepare, "run": run, "report": report}[args.operation](args)
    except Exception as error:
        print(json.dumps({"passed": False, "error": f"{type(error).__name__}: {error}"}), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
