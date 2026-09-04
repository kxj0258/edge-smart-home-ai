from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

from edge_smart_home.agent import (
    AgentPlanValidationError,
    AgentPlanValidator,
    IntelligentPath,
    IntelligentPathResult,
    IntelligentResultKind,
    LocalLlmIntelligentPath,
    RuleBasedIntelligentPath,
    StubIntelligentPath,
    UrllibJsonTransport,
)
from edge_smart_home.common import EventSource, PerceptionEvent
from edge_smart_home.device import ConfirmationRequiredError, DeviceBackendError
from edge_smart_home.router import DuplicateCommandError, UnsupportedCommandError

from .pipeline import ControlOutcome, TextControlPipeline


@dataclass(frozen=True, slots=True)
class AssistantResponse:
    event: PerceptionEvent
    status: str
    outcome: ControlOutcome | None = None
    error: str | None = None
    processing_ms: float = 0.0
    intelligent_result: IntelligentPathResult | None = None


class AssistantOrchestrator:
    """Shared application boundary for text, voice, and gesture events."""

    def __init__(
        self,
        control: TextControlPipeline,
        intelligent_path: IntelligentPath | None = None,
        plan_validator: AgentPlanValidator | None = None,
    ) -> None:
        self.control = control
        self.intelligent_path = intelligent_path
        self.plan_validator = plan_validator

    @classmethod
    def from_config_dir(cls, config_dir: Path) -> "AssistantOrchestrator":
        control = TextControlPipeline.from_config_dir(config_dir)
        intelligent_path = None
        plan_validator = None
        agent_config_path = config_dir / "agent.json"
        if agent_config_path.is_file():
            from .pipeline import _load_json

            agent_config = _load_json(agent_config_path)
            if agent_config.get("enabled"):
                provider = agent_config.get("provider")
                if provider == "stub":
                    intelligent_path = StubIntelligentPath()
                elif provider == "rule_based":
                    intelligent_path = RuleBasedIntelligentPath(
                        agent_config.get("rules", [])
                    )
                elif provider == "ollama":
                    ollama = agent_config.get("ollama", {})
                    schema_path = config_dir.parent / "schemas" / "agent_response.schema.json"
                    schema = _load_json(schema_path) if schema_path.is_file() else None
                    intelligent_path = LocalLlmIntelligentPath(
                        devices=_load_json(config_dir / "devices.json"),
                        transport=UrllibJsonTransport(),
                        endpoint=ollama.get("endpoint", "http://127.0.0.1:11434/api/generate"),
                        model=ollama.get("model", "qwen3:4b"),
                        max_commands=int(agent_config.get("max_commands", 8)),
                        max_output_tokens=int(ollama.get("max_output_tokens", 256)),
                        connect_timeout_seconds=float(ollama.get("connect_timeout_seconds", 2.0)),
                        total_timeout_seconds=float(ollama.get("total_timeout_seconds", 30.0)),
                        temperature=float(ollama.get("temperature", 0.0)),
                        seed=int(ollama.get("seed", 42)),
                        think=bool(ollama.get("think", False)),
                        state_provider=control.backend.list_states,
                        response_schema=schema,
                    )
                else:
                    raise ValueError(f"unsupported intelligent path provider: {provider}")
                devices = _load_json(config_dir / "devices.json")
                plan_validator = AgentPlanValidator(
                    devices, int(agent_config.get("max_commands", 8))
                )
        return cls(
            control,
            intelligent_path,
            plan_validator,
        )

    def handle_text(self, text: str, confirmed: bool = False) -> AssistantResponse:
        return self.handle_event(
            PerceptionEvent(source=EventSource.TEXT, label=text), confirmed=confirmed
        )

    def handle_event(
        self, event: PerceptionEvent, confirmed: bool = False
    ) -> AssistantResponse:
        started = perf_counter()
        try:
            outcome = self.control.handle_event(event, confirmed=confirmed)
        except ConfirmationRequiredError as error:
            return self._error_response(event, "confirmation_required", error, started)
        except DuplicateCommandError as error:
            return self._error_response(event, "duplicate_suppressed", error, started)
        except UnsupportedCommandError as error:
            if self.intelligent_path is not None:
                result = self.intelligent_path.route(event)
                return self._handle_intelligent_result(
                    event, result, confirmed, started
                )
            return self._error_response(event, "not_routed", error, started)
        except DeviceBackendError as error:
            return self._error_response(event, "device_error", error, started)
        status = (
            "partial_failure"
            if hasattr(outcome, "succeeded") and not outcome.succeeded
            else "executed"
        )
        return AssistantResponse(
            event=event,
            status=status,
            outcome=outcome,
            processing_ms=round((perf_counter() - started) * 1000, 3),
        )

    def _handle_intelligent_result(
        self,
        event: PerceptionEvent,
        result: IntelligentPathResult,
        confirmed: bool,
        started: float,
    ) -> AssistantResponse:
        if result.kind == IntelligentResultKind.PLAN:
            if self.plan_validator is None:
                return self._intelligent_response(
                    event, "plan_rejected", result, started, "plan validator is unavailable"
                )
            try:
                self.plan_validator.validate(result.commands)
                outcome = self.control.execute_agent_plan(
                    event, result.commands, confirmed=confirmed
                )
            except AgentPlanValidationError as error:
                return self._intelligent_response(
                    event, "plan_rejected", result, started, str(error)
                )
            except ConfirmationRequiredError as error:
                return self._intelligent_response(
                    event, "confirmation_required", result, started, str(error)
                )
            except DeviceBackendError as error:
                return self._intelligent_response(
                    event, "device_error", result, started, str(error)
                )
            status = "executed" if outcome.succeeded else "partial_failure"
            return AssistantResponse(
                event=event,
                status=status,
                outcome=outcome,
                processing_ms=round((perf_counter() - started) * 1000, 3),
                intelligent_result=result,
            )
        statuses = {
            IntelligentResultKind.RESPONSE: "responded",
            IntelligentResultKind.CLARIFICATION: "clarification_required",
            IntelligentResultKind.REJECTED: "request_rejected",
            IntelligentResultKind.UNAVAILABLE: "needs_intelligence",
        }
        return self._intelligent_response(
            event,
            statuses[result.kind],
            result,
            started,
            result.reason,
        )

    @staticmethod
    def _intelligent_response(
        event: PerceptionEvent,
        status: str,
        result: IntelligentPathResult,
        started: float,
        error: str | None = None,
    ) -> AssistantResponse:
        return AssistantResponse(
            event=event,
            status=status,
            error=error,
            processing_ms=round((perf_counter() - started) * 1000, 3),
            intelligent_result=result,
        )

    @staticmethod
    def _error_response(
        event: PerceptionEvent, status: str, error: Exception, started: float
    ) -> AssistantResponse:
        return AssistantResponse(
            event=event,
            status=status,
            error=str(error),
            processing_ms=round((perf_counter() - started) * 1000, 3),
        )
