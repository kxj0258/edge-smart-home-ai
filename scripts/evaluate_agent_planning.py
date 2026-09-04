from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from time import perf_counter

from edge_smart_home.agent import (
    AgentPlanValidator,
    LocalLlmIntelligentPath,
    UrllibJsonTransport,
)
from edge_smart_home.app import AssistantOrchestrator
from edge_smart_home.app.pipeline import TextControlPipeline, _load_json


class CountingTransport:
    def __init__(self, delegate: UrllibJsonTransport) -> None:
        self.delegate = delegate
        self.calls = 0

    def post_json(self, url, payload, *, connect_timeout_seconds, total_timeout_seconds):
        self.calls += 1
        return self.delegate.post_json(
            url,
            payload,
            connect_timeout_seconds=connect_timeout_seconds,
            total_timeout_seconds=total_timeout_seconds,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate local Agent planning outputs")
    parser.add_argument("--config-dir", type=Path, default=Path("configs"))
    parser.add_argument(
        "--cases", type=Path, default=Path("tests/fixtures/agent_cases.json")
    )
    parser.add_argument("--endpoint", default="http://127.0.0.1:11434/api/generate")
    parser.add_argument("--model", default="qwen3:4b")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Evaluate only the first N fixed cases for a smoke run",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="JSON result path; defaults to evaluation/agent_runs/<timestamp>.json",
    )
    args = parser.parse_args()

    config_dir = args.config_dir.resolve()
    devices = _load_json(config_dir / "devices.json")
    agent_config = _load_json(config_dir / "agent.json")
    ollama = agent_config.get("ollama", {})
    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    if args.limit is not None:
        if args.limit <= 0:
            parser.error("--limit must be positive")
        cases = cases[: args.limit]
    records = []
    for case in cases:
        control = TextControlPipeline.from_config_dir(config_dir)
        transport = CountingTransport(UrllibJsonTransport())
        path = LocalLlmIntelligentPath(
            devices,
            transport,
            endpoint=args.endpoint,
            model=args.model,
            max_commands=int(agent_config.get("max_commands", 8)),
            max_output_tokens=int(ollama.get("max_output_tokens", 256)),
            connect_timeout_seconds=float(ollama.get("connect_timeout_seconds", 2.0)),
            total_timeout_seconds=float(ollama.get("total_timeout_seconds", 30.0)),
            temperature=0.0,
            seed=int(ollama.get("seed", 42)),
            think=False,
            state_provider=control.backend.list_states,
        )
        validator = AgentPlanValidator(
            devices, int(agent_config.get("max_commands", 8))
        )
        orchestrator = AssistantOrchestrator(control, path, validator)
        started = perf_counter()
        response = orchestrator.handle_text(case["text"])
        result = response.intelligent_result
        actual_commands = [
            {
                "device_id": command.device_id,
                "action": command.action,
                "arguments": dict(command.arguments),
            }
            for command in (result.commands if result is not None else ())
        ]
        actual_kind = result.kind.value if result is not None else None
        expected = case["expected"]
        expected_kind = expected.get("kind")
        expected_commands = expected.get("commands", [])
        actual_path = "intelligent" if transport.calls else "fast_path"
        kind_match = expected_kind is not None and actual_kind == expected_kind
        commands_match = actual_commands == expected_commands
        records.append(
            {
                "id": case["id"],
                "text": case["text"],
                "expected": expected,
                "expected_path": case["expected_path"],
                "actual": {
                    "path": actual_path,
                    "llm_calls": transport.calls,
                    "status": response.status,
                    "kind": actual_kind,
                    "message": result.message if result is not None else None,
                    "commands": actual_commands,
                    "reason": result.reason if result is not None else response.error,
                },
                "path_match": actual_path == case["expected_path"],
                "fast_path_zero_llm": actual_path != "fast_path" or transport.calls == 0,
                "kind_match": kind_match,
                "commands_match": commands_match,
                "validator_rejection": response.status == "plan_rejected",
                "latency_ms": round((perf_counter() - started) * 1000, 3),
            }
        )

    intelligent_records = [
        record for record in records if record["expected_path"] == "intelligent"
    ]
    kind_matches = sum(record["kind_match"] for record in intelligent_records)
    command_matches = sum(
        record["kind_match"] and record["commands_match"]
        for record in intelligent_records
    )
    output = {
        "run_type": "assistant_agent_planning",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "model": args.model,
        "endpoint": args.endpoint,
        "cases": len(records),
        "path_accuracy": (
            sum(record["path_match"] for record in records) / len(records)
            if records
            else None
        ),
        "intelligent_cases": len(intelligent_records),
        "intelligent_kind_accuracy": kind_matches / len(intelligent_records) if intelligent_records else None,
        "intelligent_exact_structured_match": command_matches / len(intelligent_records) if intelligent_records else None,
        "fast_path_llm_calls": sum(
            record["actual"]["llm_calls"]
            for record in records
            if record["expected_path"] == "fast_path"
        ),
        "fast_path_cases": sum(
            record["expected_path"] == "fast_path" for record in records
        ),
        "fast_path_zero_llm_violations": sum(
            record["expected_path"] == "fast_path"
            and record["actual"]["llm_calls"] != 0
            for record in records
        ),
        "validator_rejections": sum(record["validator_rejection"] for record in records),
        "records": records,
    }
    output_path = args.output or Path("evaluation/agent_runs") / (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ") + ".json"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({key: output[key] for key in output if key != "records"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
