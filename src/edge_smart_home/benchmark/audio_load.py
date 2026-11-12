"""Incremental-load experiment definitions and honest cross-run comparison."""
from __future__ import annotations

from collections import defaultdict
import hashlib
import json
from math import isfinite
from statistics import median


STAGES = ("capture", "asr", "vision", "tts", "llm")
COMMANDS = ("打开客厅灯", "把客厅灯亮度设置为50", "下一集", "继续播放",
            "开启观影模式", "客厅有点暗，帮我处理一下")
PROTOCOL_VERSION = "audio-load-v1"


def capabilities(stage: str) -> dict:
    if stage not in STAGES:
        raise ValueError(f"unknown stage: {stage}")
    level = STAGES.index(stage)
    return dict(asr=level >= 1, vision=level >= 2, tts=level >= 3, llm=level >= 4)


def prompts(duration: float) -> list[dict]:
    if not isfinite(duration) or duration < 1:
        raise ValueError("duration must be finite and at least one second")
    # Same timed prompts in every stage; prompts are not transcript ground truth.
    return [{"at_seconds": at, "text": COMMANDS[i % len(COMMANDS)]}
            for i, at in enumerate(range(3, max(3, int(duration) - 5), 8))]


def compatibility_key(record: dict, conditions: dict) -> str:
    # All stages freeze the same artifact inventory, including inactive models.
    identity = {"protocol": PROTOCOL_VERSION, "conditions": conditions,
                "files": record["files_sha256"], "artifacts": record["artifacts"],
                "runtime": record["runtime"], "config": record["config_sha256"]}
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


def finalize(stage: str, elapsed: float, microphone: dict, counts: dict,
             dispatcher: dict, errors: list, interrupted: bool = False) -> dict:
    enabled = capabilities(stage)
    missing = []
    if not microphone.get("callback_count"):
        missing.append("audio_callbacks")
    for feature, key in (("asr", "recognitions"), ("vision", "camera_frames"),
                         ("tts", "tts_completed"), ("llm", "llm_calls")):
        if enabled[feature] and not counts.get(key):
            missing.append(key)
    unfinished = bool(counts.get("asr_worker_alive") or dispatcher.get("worker_alive")
                      or counts.get("feedback_worker_alive"))
    status = "failed" if errors or unfinished else (
        "interrupted" if interrupted else "not_exercised" if missing else "completed")
    return {"schema_version": 1, "protocol_version": PROTOCOL_VERSION,
            "stage": stage, "enabled": enabled, "status": status,
            "elapsed_seconds": elapsed, "microphone": microphone,
            "counts": counts, "dispatcher": dispatcher, "errors": errors,
            "unexercised": missing,
            "overflow_per_min": microphone.get("portaudio_overflow_count", 0) * 60 / elapsed
                                if elapsed > 0 else None,
            "capture_drop_per_min": microphone.get("queue_drop_count", 0) * 60 / elapsed
                                    if elapsed > 0 else None,
            "accuracy": None,
            "note": "Prompts are not ground truth. Completion means exercised, not accurate. "
                    "Overflow counts are status occurrences, not lost sample counts."}


def compare(runs: list[dict]) -> dict:
    keys = {r.get("compatibility_key") for r in runs}
    rates = {r.get("microphone", {}).get("actual_sample_rate") for r in runs}
    sources = {r.get("input_source") for r in runs}
    ids = [r.get("run_id") for r in runs]
    duplicates = len(ids) != len(set(ids))
    comparable = (bool(runs) and len(sources) == 1 and None not in sources and not duplicates and None not in ids and len(keys) == 1
                  and None not in keys and len(rates) == 1 and None not in rates)
    groups = defaultdict(list)
    excluded = []
    for run in runs:
        if run.get("status") != "completed":
            excluded.append({"run_id": run.get("run_id"), "status": run.get("status")})
        else:
            groups[run["stage"]].append(run)
    rows = []
    for stage in STAGES:
        items = groups[stage]
        values = [r["overflow_per_min"] for r in items if r.get("overflow_per_min") is not None]
        rows.append({"stage": stage, "valid_runs": len(items),
                     "median_overflow_per_min": median(values) if values else None,
                     "median_queue_wait_max_ms": median([r["microphone"]["max_queue_wait_ms"]
                        for r in items]) if items else None,
                     "capture_drops": sum(r["microphone"].get("queue_drop_count", 0) for r in items),
                     "endpoint_drops": sum(r.get("dispatcher", {}).get("dropped", 0) for r in items),
                     "cancelled_at_shutdown": sum(r.get("dispatcher", {}).get("cancelled", 0) for r in items),
                     "median_rtf": median([r["asr_mean_rtf"] for r in items if r.get("asr_mean_rtf") is not None])
                         if any(r.get("asr_mean_rtf") is not None for r in items) else None})
    sufficient = comparable and all(row["valid_runs"] >= 3 for row in rows)
    return {"schema_version": 1, "comparable": comparable, "sufficient_repeats": sufficient, "duplicate_runs": duplicates,
            "rows": rows, "excluded": excluded,
            "conclusion": "Ready for manual stage-by-stage analysis; not proof of causation."
                if sufficient else "No root-cause conclusion: check conditions, exercised stages and repeats.",
            "note": "Rates use each run's measured duration. Incompatible rows are descriptive only."}
