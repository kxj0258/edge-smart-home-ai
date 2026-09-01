from __future__ import annotations

import re
from typing import Any, Mapping

from edge_smart_home.common import PerceptionEvent

from .interfaces import (
    IntelligentPathResult,
    IntelligentResultKind,
    PlannedDeviceCommand,
)


class RuleBasedIntelligentPath:
    """Reproducible planner used to validate the Agent execution boundary."""

    def __init__(self, rules: list[Mapping[str, Any]]) -> None:
        self.rules = tuple(dict(rule) for rule in rules)

    def route(self, event: PerceptionEvent) -> IntelligentPathResult:
        text = re.sub(r"[\s，。！？,.!?]", "", event.label)
        for rule in self.rules:
            exact = rule.get("exact")
            contains_all = rule.get("contains_all")
            if exact is not None and text not in exact:
                continue
            if contains_all is not None and not all(item in text for item in contains_all):
                continue
            kind = IntelligentResultKind(rule["kind"])
            commands = tuple(
                PlannedDeviceCommand(
                    item["device_id"], item["action"], item.get("arguments", {})
                )
                for item in rule.get("commands", [])
            )
            return IntelligentPathResult(
                kind=kind,
                commands=commands,
                message=rule.get("message"),
                reason=rule.get("reason"),
            )
        return IntelligentPathResult(
            kind=IntelligentResultKind.UNAVAILABLE,
            reason=f"no reproducible intelligent rule matched: {event.label}",
        )
