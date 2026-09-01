from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from edge_smart_home.app.pipeline import _load_json
from edge_smart_home.benchmark.asr_evaluation import (
    classify_route_mismatch,
    markdown_report,
    route_signature,
    summarize,
)
from edge_smart_home.router import FastPathRouter, SceneRouter


def score_routes(row: dict, fast_router: FastPathRouter, scene_router: SceneRouter) -> None:
    try:
        expected_route = route_signature(row["expected"], fast_router, scene_router)
    except (ValueError, KeyError) as error:
        row.update(
            router_match=False,
            expected_route=None,
            recognized_route=None,
            router_outcome="expected_unroutable",
            semantic_mismatch_type=None,
            router_error=str(error),
        )
        return
    try:
        recognized_route = route_signature(row["recognized"], fast_router, scene_router)
    except (ValueError, KeyError) as error:
        row.update(
            router_match=False,
            expected_route=expected_route,
            recognized_route=None,
            router_outcome="recognized_unroutable",
            semantic_mismatch_type=None,
            router_error=str(error),
        )
        return
    matches = expected_route == recognized_route
    row.update(
        router_match=matches,
        expected_route=expected_route,
        recognized_route=recognized_route,
        router_outcome="semantic_match" if matches else "semantic_mismatch",
        semantic_mismatch_type=(
            None if matches else classify_route_mismatch(expected_route, recognized_route)
        ),
        router_error=None,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Re-score an ASR run with current Router")
    parser.add_argument("run", type=Path)
    parser.add_argument("--config-dir", type=Path, default=Path("configs"))
    parser.add_argument("--output-dir", type=Path, default=Path("evaluation/asr_rescored"))
    args = parser.parse_args()

    devices = _load_json(args.config_dir / "devices.json")
    intents = _load_json(args.config_dir / "intents.json")
    routing = _load_json(args.config_dir / "routing.json")
    scenes = _load_json(args.config_dir / "scenes.json")
    fast_router = FastPathRouter(devices, intents, routing.get("default_devices", {}))
    scene_router = SceneRouter(scenes)

    rows = []
    with (args.run / "results.jsonl").open(encoding="utf-8") as source:
        for line in source:
            if not line.strip():
                continue
            row = json.loads(line)
            score_routes(row, fast_router, scene_router)
            rows.append(row)
    if not rows:
        parser.error("source run has no results")

    session_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    destination = args.output_dir / session_id
    destination.mkdir(parents=True, exist_ok=False)
    source_session = json.loads((args.run / "session.json").read_text(encoding="utf-8"))
    source_session.update(
        {
            "rescored_at": datetime.now(timezone.utc).isoformat(),
            "derived_from_run": str(args.run.resolve()),
            "router_config_dir": str(args.config_dir.resolve()),
        }
    )
    (destination / "session.json").write_text(
        json.dumps(source_session, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    with (destination / "results.jsonl").open("w", encoding="utf-8") as output:
        for row in rows:
            output.write(json.dumps(row, ensure_ascii=False) + "\n")
    summary = summarize(rows)
    (destination / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (destination / "report.md").write_text(markdown_report(summary), encoding="utf-8")
    print(f"Re-scored run: {destination}")
    print(json.dumps(summary["overall"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
