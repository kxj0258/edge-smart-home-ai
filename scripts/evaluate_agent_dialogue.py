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
    parser = argparse.ArgumentParser(
        description="Evaluate constrained local Agent single-turn dialogue"
    )
    parser.add_argument("--config-dir", type=Path, default=Path("configs"))
    parser.add_argument(
        "--cases", type=Path, default=Path("tests/fixtures/agent_dialogue_cases.json")
    )
    parser.add_argument("--endpoint", default="http://127.0.0.1:11434/api/generate")
    parser.add_argument("--model", default="qwen3:4b")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    config_dir = args.config_dir.resolve()
    devices = _load_json(config_dir / "devices.json")
    agent_config = _load_json(config_dir / "agent.json")
    ollama = agent_config.get("ollama", {})
    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    if not isinstance(cases, list) or not cases:
        parser.error("cases must be a non-empty JSON array")
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
        validator = AgentPlanValidator(devices, int(agent_config.get("max_commands", 8)))
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
        intelligent_case = case["expected_path"] == "intelligent"
        kind_match = actual_kind == expected_kind if intelligent_case else None
        commands_match = actual_commands == expected_commands if intelligent_case else None
        commands_order_insensitive_match = (
            _canonical_commands(actual_commands) == _canonical_commands(expected_commands)
            if intelligent_case
            else None
        )
        status_match = (
            response.status == case["expected_status"]
            if "expected_status" in case
            else True
        )
        false_plan = intelligent_case and expected_kind != "plan" and actual_kind == "plan"
        records.append(
            {
                "id": case["id"],
                "category": case.get("category", expected_kind),
                "text": case["text"],
                "expected": expected,
                "expected_path": case["expected_path"],
                "expected_status": case.get("expected_status"),
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
                "status_match": status_match,
                "kind_match": kind_match,
                "commands_match": commands_match,
                "commands_order_insensitive_match": commands_order_insensitive_match,
                "false_plan": false_plan,
                "validator_rejection": response.status == "plan_rejected",
                "latency_ms": round((perf_counter() - started) * 1000, 3),
            }
        )

    intelligent_records = [
        record for record in records if record["expected_path"] == "intelligent"
    ]
    category_names = sorted({record["category"] for record in intelligent_records})
    category_accuracy = {
        category: sum(
            record["kind_match"]
            for record in intelligent_records
            if record["category"] == category
        )
        / sum(record["category"] == category for record in intelligent_records)
        for category in category_names
    }
    output = {
        "run_type": "assistant_agent_dialogue",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "model": args.model,
        "endpoint": args.endpoint,
        "cases": len(records),
        "path_accuracy": sum(record["path_match"] for record in records) / len(records),
        "intelligent_cases": len(intelligent_records),
        "kind_accuracy": sum(record["kind_match"] for record in intelligent_records)
        / len(intelligent_records),
        "intelligent_kind_accuracy": (
            sum(record["kind_match"] for record in intelligent_records)
            / len(intelligent_records)
            if intelligent_records
            else None
        ),
        "exact_structured_match": sum(
            record["kind_match"] and record["commands_match"]
            for record in intelligent_records
        )
        / len(intelligent_records),
        "order_insensitive_structured_match": sum(
            record["kind_match"] and record["commands_order_insensitive_match"]
            for record in intelligent_records
        )
        / len(intelligent_records),
        "category_accuracy": category_accuracy,
        "dialogue_false_plan_count": sum(record["false_plan"] for record in records),
        "dialogue_nonempty_command_count": sum(
            record["expected"]["kind"] != "plan" and bool(record["actual"]["commands"])
            for record in records
        ),
        "fast_path_llm_calls": sum(
            record["actual"]["llm_calls"]
            for record in records
            if record["expected_path"] == "fast_path"
        ),
        "fast_path_zero_llm_violations": sum(
            record["expected_path"] == "fast_path"
            and record["actual"]["llm_calls"] != 0
            for record in records
        ),
        "expected_status_violations": sum(
            "expected_status" in record and not record["status_match"]
            for record in records
        ),
        "validator_rejections": sum(record["validator_rejection"] for record in records),
        "records": records,
    }
    output_path = args.output or Path("evaluation/agent_dialogue_runs") / (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ") + ".json"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {key: value for key, value in output.items() if key != "records"},
            ensure_ascii=False,
            indent=2,
        )
    )


def _canonical_commands(commands: list[dict]) -> list[dict]:
    return sorted(
        commands,
        key=lambda command: (
            command["device_id"],
            command["action"],
            json.dumps(command["arguments"], ensure_ascii=False, sort_keys=True),
        ),
    )


if __name__ == "__main__":
    main()
