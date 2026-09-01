from __future__ import annotations

import argparse
import json
from pathlib import Path


def load_run(path: Path) -> dict:
    session = json.loads((path / "session.json").read_text(encoding="utf-8"))
    summary = json.loads((path / "summary.json").read_text(encoding="utf-8"))
    overall = summary["overall"]
    hotwords = session.get("hotwords", {})
    return {
        "run": path.name,
        "decoding_method": session.get(
            "decoding_method",
            session.get("audio_config", {}).get("asr", {}).get("decoding_method"),
        ),
        "hotwords_enabled": hotwords.get("enabled", bool(hotwords.get("valid"))),
        "hotwords_score": hotwords.get(
            "score", session.get("audio_config", {}).get("asr", {}).get("hotwords_score")
        ),
        **overall,
    }


def render_comparison(runs: list[dict]) -> str:
    lines = [
        "# ASR Run Comparison",
        "",
        "| Run | Decoder | Hotwords | Score | Exact | CER | Router | Mean RTF |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for run in runs:
        lines.append(
            f"| {run['run']} | {run['decoding_method']} | "
            f"{'yes' if run['hotwords_enabled'] else 'no'} | {run['hotwords_score']} | "
            f"{_percent(run['exact_match_accuracy'])} | {_percent(run['cer'])} | "
            f"{_percent(run['router_accuracy'])} | {run['mean_rtf']} |"
        )
    return "\n".join(lines) + "\n"


def _percent(value: float | None) -> str:
    return "N/A" if value is None else f"{value * 100:.2f}%"


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare completed ASR evaluation runs")
    parser.add_argument("runs", nargs="+", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = render_comparison([load_run(path) for path in args.runs])
    if args.output:
        args.output.write_text(report, encoding="utf-8")
        print(f"Comparison written to: {args.output}")
    else:
        print(report, end="")


if __name__ == "__main__":
    main()
