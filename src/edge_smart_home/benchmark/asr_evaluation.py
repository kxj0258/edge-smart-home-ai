from __future__ import annotations

from collections import defaultdict
import re
from typing import Any, Iterable, Mapping

from edge_smart_home.common import EventSource, PerceptionEvent


def normalize_text(text: str) -> str:
    return re.sub(r"[\s，。！？,.!?]", "", text)


def edit_distance(reference: str, hypothesis: str) -> int:
    previous = list(range(len(hypothesis) + 1))
    for reference_index, reference_character in enumerate(reference, start=1):
        current = [reference_index]
        for hypothesis_index, hypothesis_character in enumerate(hypothesis, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[hypothesis_index] + 1,
                    previous[hypothesis_index - 1]
                    + (reference_character != hypothesis_character),
                )
            )
        previous = current
    return previous[-1]


def text_metrics(expected: str, recognized: str) -> dict[str, Any]:
    reference = normalize_text(expected)
    hypothesis = normalize_text(recognized)
    edits = edit_distance(reference, hypothesis)
    return {
        "exact_match": reference == hypothesis,
        "character_edits": edits,
        "reference_characters": len(reference),
        "cer": round(edits / len(reference), 4) if reference else None,
    }


def route_signature(text: str, fast_router: Any, scene_router: Any) -> dict[str, Any]:
    event = PerceptionEvent(source=EventSource.VOICE, label=text)
    scene = scene_router.match(event)
    if scene:
        scene_id, commands = scene
        return {
            "kind": "scene",
            "scene_id": scene_id,
            "commands": [
                {
                    "device_id": command.device_id,
                    "action": command.action,
                    "arguments": dict(command.arguments),
                }
                for command in commands
            ],
        }
    command = fast_router.route(event)
    return {
        "kind": "device_command",
        "device_id": command.device_id,
        "action": command.action,
        "arguments": dict(command.arguments),
    }


def classify_route_mismatch(expected: Mapping[str, Any], recognized: Mapping[str, Any]) -> str:
    if expected.get("kind") != "device_command" or recognized.get("kind") != "device_command":
        return "other_semantic_mismatch"
    if (
        expected.get("device_id") == recognized.get("device_id")
        and expected.get("action") == recognized.get("action")
        and expected.get("arguments") != recognized.get("arguments")
    ):
        return "parameter_mismatch"
    inverse_actions = {
        frozenset(("turn_on", "turn_off")),
        frozenset(("open", "close")),
        frozenset(("play", "pause")),
        frozenset(("enable_privacy", "disable_privacy")),
        frozenset(("enable_guest_wifi", "disable_guest_wifi")),
        frozenset(("volume_up", "volume_down")),
        frozenset(("speed_up", "speed_down")),
    }
    if (
        expected.get("device_id") == recognized.get("device_id")
        and frozenset((expected.get("action"), recognized.get("action"))) in inverse_actions
    ):
        return "opposite_action_mismatch"
    return "other_semantic_mismatch"


def summarize(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    materialized = list(rows)
    return {
        "overall": _summarize_group(materialized),
        "by_speaker": _group_summary(materialized, "speaker_id"),
        "by_speed": _group_summary(materialized, "speed"),
        "by_command": _group_summary(materialized, "expected"),
    }


def _group_summary(rows: list[Mapping[str, Any]], key: str) -> dict[str, Any]:
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row[key])].append(row)
    return {name: _summarize_group(group) for name, group in groups.items()}


def _summarize_group(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    recordings = len(rows)
    exact = sum(bool(row["exact_match"]) for row in rows)
    edits = sum(int(row["character_edits"]) for row in rows)
    characters = sum(int(row["reference_characters"]) for row in rows)
    routed = [
        row
        for row in rows
        if row.get("router_match") is not None
        and row.get("router_outcome") != "expected_unroutable"
    ]
    route_matches = sum(bool(row["router_match"]) for row in routed)
    route_outcomes = {
        outcome: sum(row.get("router_outcome") == outcome for row in rows)
        for outcome in (
            "semantic_match",
            "semantic_mismatch",
            "recognized_unroutable",
            "expected_unroutable",
        )
    }
    mismatch_types = {
        mismatch_type: sum(row.get("semantic_mismatch_type") == mismatch_type for row in rows)
        for mismatch_type in (
            "parameter_mismatch",
            "opposite_action_mismatch",
            "other_semantic_mismatch",
        )
    }
    rtfs = [float(row["rtf"]) for row in rows if row.get("rtf") is not None]
    return {
        "recordings": recordings,
        "exact_matches": exact,
        "exact_match_accuracy": round(exact / recordings, 4) if recordings else None,
        "character_edits": edits,
        "reference_characters": characters,
        "cer": round(edits / characters, 4) if characters else None,
        "router_evaluated": len(routed),
        "expected_route_coverage": (
            round(len(routed) / recordings, 4) if recordings else None
        ),
        "router_matches": route_matches,
        "router_accuracy": round(route_matches / len(routed), 4) if routed else None,
        "router_outcomes": route_outcomes,
        "semantic_mismatch_types": mismatch_types,
        "mean_rtf": round(sum(rtfs) / len(rtfs), 4) if rtfs else None,
    }


def markdown_report(summary: Mapping[str, Any]) -> str:
    overall = summary["overall"]
    lines = [
        "# ASR Command Set Evaluation",
        "",
        "## Overall",
        "",
        "| Recordings | Exact accuracy | CER | Route coverage | Router accuracy | Mean RTF |",
        "|---:|---:|---:|---:|---:|---:|",
        (
            f"| {overall['recordings']} | {_percent(overall['exact_match_accuracy'])} | "
            f"{_percent(overall['cer'])} | {_percent(overall['expected_route_coverage'])} | "
            f"{_percent(overall['router_accuracy'])} | "
            f"{overall['mean_rtf']} |"
        ),
        "",
    ]
    for title, key in (("By speaker", "by_speaker"), ("By speed", "by_speed")):
        lines.extend(
            [
                f"## {title}",
                "",
                "| Group | Recordings | Exact accuracy | CER | Router accuracy | Mean RTF |",
                "|---|---:|---:|---:|---:|---:|",
            ]
        )
        for name, group in summary[key].items():
            lines.append(
                f"| {name} | {group['recordings']} | "
                f"{_percent(group['exact_match_accuracy'])} | {_percent(group['cer'])} | "
                f"{_percent(group['router_accuracy'])} | {group['mean_rtf']} |"
            )
        lines.append("")
    lines.extend(
        [
            "## Lowest router accuracy commands",
            "",
            "| Command | Recordings | Exact accuracy | CER | Router accuracy |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    worst_commands = sorted(
        summary["by_command"].items(),
        key=lambda item: (
            item[1]["router_accuracy"],
            item[1]["exact_match_accuracy"],
            -item[1]["cer"],
        ),
    )[:15]
    for name, group in worst_commands:
        lines.append(
            f"| {name} | {group['recordings']} | "
            f"{_percent(group['exact_match_accuracy'])} | {_percent(group['cer'])} | "
            f"{_percent(group['router_accuracy'])} |"
        )
    outcomes = overall["router_outcomes"]
    mismatch_types = overall["semantic_mismatch_types"]
    lines.extend(
        [
            "",
            "## Router outcomes",
            "",
            f"- Semantic match: {outcomes['semantic_match']}",
            f"- Routable but semantic mismatch: {outcomes['semantic_mismatch']}",
            f"- Recognized text unroutable: {outcomes['recognized_unroutable']}",
            f"- Expected text unroutable: {outcomes['expected_unroutable']}",
            "",
            "## Semantic mismatch safety categories",
            "",
            f"- Parameter mismatch: {mismatch_types['parameter_mismatch']}",
            f"- Opposite action mismatch: {mismatch_types['opposite_action_mismatch']}",
            f"- Other semantic mismatch: {mismatch_types['other_semantic_mismatch']}",
            "",
        ]
    )
    return "\n".join(lines)


def _percent(value: float | None) -> str:
    return "N/A" if value is None else f"{value * 100:.2f}%"
