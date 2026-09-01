from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from time import perf_counter

from edge_smart_home.app import AssistantOrchestrator
from edge_smart_home.benchmark.asr_command_set import load_commands
from edge_smart_home.benchmark.e2e import summarize_e2e
from edge_smart_home.benchmark.events import BenchmarkEventRecord
from edge_smart_home.common import DeviceState


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run reproducible Text -> Router -> Backend end-to-end benchmark"
    )
    parser.add_argument(
        "--commands", type=Path, default=Path("configs/asr_test_commands.txt")
    )
    parser.add_argument("--config-dir", type=Path, default=Path("configs"))
    parser.add_argument("--output-dir", type=Path, default=Path("evaluation/e2e_runs"))
    args = parser.parse_args()

    orchestrator = AssistantOrchestrator.from_config_dir(args.config_dir)
    rows = []
    for index, command_text in enumerate(load_commands(args.commands), start=1):
        started = perf_counter()
        response = orchestrator.handle_text(command_text)
        end_to_end_ms = round((perf_counter() - started) * 1000, 3)
        attempts, successes = _device_counts(response.outcome)
        row = BenchmarkEventRecord(
            source="text",
            input=command_text,
            status=response.status,
            processing_ms=response.processing_ms,
            end_to_end_ms=end_to_end_ms,
            device_attempts=attempts,
            device_successes=successes,
            metrics={"index": index},
            error=response.error,
        ).to_dict()
        rows.append(row)
        print(f"[{index}] {command_text}: {response.status} ({end_to_end_ms} ms)")

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    run_dir = args.output_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    summary = summarize_e2e(rows)
    metadata = {
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": "text",
        "commands": str(args.commands.resolve()),
        "config_dir": str(args.config_dir.resolve()),
        "note": "Metrics are from this local execution; no voice or gesture metrics are inferred.",
    }
    _write_json(run_dir / "metadata.json", metadata)
    _write_json(run_dir / "summary.json", summary)
    with (run_dir / "results.jsonl").open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"Benchmark complete: {run_dir}")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def _device_counts(outcome) -> tuple[int, int]:
    if outcome is None:
        return 0, 0
    if isinstance(outcome, DeviceState):
        return 1, 1
    return len(outcome.results), sum(result.succeeded for result in outcome.results)


def _write_json(path: Path, value) -> None:
    with path.open("w", encoding="utf-8") as file:
        json.dump(value, file, ensure_ascii=False, indent=2)
        file.write("\n")


if __name__ == "__main__":
    main()
