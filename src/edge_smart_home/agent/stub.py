from __future__ import annotations

from edge_smart_home.common import PerceptionEvent

from .interfaces import IntelligentPathResult, IntelligentResultKind


class StubIntelligentPath:
    """Explicit placeholder that never fabricates an LLM answer or command."""

    provider_name = "stub"

    def route(self, event: PerceptionEvent) -> IntelligentPathResult:
        return IntelligentPathResult(
            kind=IntelligentResultKind.UNAVAILABLE,
            reason=f"intelligent path is not implemented for: {event.label}",
        )
