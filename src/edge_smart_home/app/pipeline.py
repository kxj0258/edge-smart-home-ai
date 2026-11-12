from __future__ import annotations

from edge_smart_home.common.observability import observed, span

import json
import os
from pathlib import Path
from typing import Any
from collections.abc import Sequence

from edge_smart_home.agent import PlannedDeviceCommand, IntelligentPathResult, IntelligentResultKind
from edge_smart_home.agent.local_llm import LocalLlmRequestGuard
from edge_smart_home.agent.validation import AgentPlanValidator
from edge_smart_home.common import (
    CommandExecutionResult,
    DeviceCommand,
    DeviceState,
    EventSource,
    PerceptionEvent,
    SceneExecutionResult,
)
from edge_smart_home.device import (
    ConfirmationRequiredError,
    DeviceBackend,
    DeviceBackendError,
    HomeAssistantBackend,
    MockDeviceBackend,
    SafetyPolicy,
)
from edge_smart_home.router import (
    CommandDeduplicator,
    DuplicateCommandError,
    FastPathRouter,
    SceneRouter,
    UnsupportedCommandError,
)


ControlOutcome = DeviceState | SceneExecutionResult


class RequestPlanConsistencyError(UnsupportedCommandError):
    """A classified preflight/fidelity failure; never requests model fallback."""

    def __init__(self, result: IntelligentPathResult):
        super().__init__(result.message or result.reason or "request/plan consistency rejected")
        self.result = result
        self.error_code = result.error_code


class TextControlPipeline:
    def __init__(
        self,
        router: FastPathRouter,
        scene_router: SceneRouter,
        backend: DeviceBackend,
        safety: SafetyPolicy,
        deduplicator: CommandDeduplicator,
    ) -> None:
        self.router = router
        self.scene_router = scene_router
        self.backend = backend
        self.safety = safety
        self.deduplicator = deduplicator
        self.request_guard = LocalLlmRequestGuard(router.devices)
        self.plan_validator = AgentPlanValidator(router.devices)

    def preflight_event(self, event: PerceptionEvent) -> IntelligentPathResult | None:
        return self.request_guard.preflight(event)

    def _validate_request_plan(self, event: PerceptionEvent, commands) -> None:
        self.plan_validator.validate(commands)
        proposed = IntelligentPathResult(kind=IntelligentResultKind.PLAN, commands=tuple(commands))
        decision = self.request_guard.consistency.check(event, proposed)
        if decision.applied:
            # This execution boundary does not silently substitute a different
            # plan. Query reconstruction belongs to the planning guard.
            from dataclasses import replace
            from edge_smart_home.agent.error_codes import IntentErrorCode
            failure = decision.result
            if failure.kind == IntelligentResultKind.PLAN:
                failure = IntelligentPathResult(
                    kind=IntelligentResultKind.REJECTED, message="计划与查询请求不一致，已拒绝。",
                    error_code=IntentErrorCode.REQUEST_PLAN_MISMATCH,
                )
            else:
                failure = replace(failure, error_code=decision.code)
            raise RequestPlanConsistencyError(failure)

    @classmethod
    def from_config_dir(cls, config_dir: Path) -> "TextControlPipeline":
        devices = _load_json(config_dir / "devices.json")
        intents = _load_json(config_dir / "intents.json")
        routing = _load_json(config_dir / "routing.json")
        scenes = _load_json(config_dir / "scenes.json")
        safety = _load_json(config_dir / "safety.json")
        backend: DeviceBackend = MockDeviceBackend(devices)
        ha_config_path = config_dir / "home_assistant.json"
        if ha_config_path.is_file():
            ha_config = _load_json(ha_config_path)
            backend_name = os.environ.get(
                "EDGE_SMART_HOME_BACKEND", ha_config.get("backend", "mock")
            )
            if backend_name == "home_assistant":
                backend = HomeAssistantBackend.from_config(ha_config)
            elif backend_name != "mock":
                raise ValueError(f"unsupported device backend: {backend_name}")
        return cls(
            FastPathRouter(devices, intents, routing.get("default_devices", {})),
            SceneRouter(scenes),
            backend,
            SafetyPolicy(safety.get("action_risks", {})),
            CommandDeduplicator(float(routing.get("voice_dedup_window_seconds", 1.5))),
        )

    def handle_text(self, text: str, confirmed: bool = False) -> ControlOutcome:
        event = PerceptionEvent(source=EventSource.TEXT, label=text)
        return self.handle_event(event, confirmed=confirmed)

    def handle_event(
        self, event: PerceptionEvent, confirmed: bool = False
    ) -> ControlOutcome:
        scene_id, commands = self.resolve_event(event)
        return self.execute_resolved(
            event, scene_id, commands, confirmed=confirmed
        )

    @observed("router")
    def resolve_event(
        self, event: PerceptionEvent
    ) -> tuple[str | None, tuple[DeviceCommand, ...]]:
        """Resolve an event without validating safety or causing side effects."""
        preflight = self.preflight_event(event)
        if preflight is not None:
            raise RequestPlanConsistencyError(preflight)
        scene = self.scene_router.match(event)
        if scene:
            return scene
        command = self.router.route(event)
        self._validate_request_plan(event, (PlannedDeviceCommand(command.device_id, command.action, command.arguments),))
        return None, (command,)

    def execute_resolved(
        self,
        event: PerceptionEvent,
        scene_id: str | None,
        commands: Sequence[DeviceCommand],
        confirmed: bool = False,
    ) -> ControlOutcome:
        """Safety-check and execute a previously resolved command set."""
        if scene_id is None:
            if len(commands) != 1:
                raise ValueError("a direct event must resolve to one command")
            command = commands[0]
            self.safety.check(command, confirmed=confirmed)
            self.deduplicator.check(command)
            return self._execute_backend(command)

        # Check the complete scene before the first side effect. A high-risk
        # command late in the scene must not leave a partially applied scene.
        for command in commands:
            self.safety.check(command, confirmed=confirmed)
        results = []
        for command in commands:
            try:
                self.deduplicator.check(command)
                state = self._execute_backend(command)
            except (
                ConfirmationRequiredError,
                DeviceBackendError,
                DuplicateCommandError,
            ) as error:
                results.append(CommandExecutionResult(command, error=str(error), error_code=getattr(error, "error_code", None),
                                                     request_accepted=getattr(error, "request_accepted", None),
                                                     observed_state=getattr(error, "observed_state", None)))
            else:
                results.append(CommandExecutionResult(command, state=state))
        return SceneExecutionResult(scene_id, tuple(results))

    def _execute_backend(self, command: DeviceCommand) -> DeviceState:
        with span("backend", request_id=command.request_id,
                  device_id=command.device_id, action=command.action,
                  backend=type(self.backend).__name__,
                  completion_semantics="backend_return_not_physical_confirmation"):
            state = self.backend.execute(command)
            if not isinstance(state, DeviceState) or state.device_id != command.device_id:
                raise DeviceBackendError("backend returned a different or invalid device state", error_code="DEVICE_RESPONSE_INVALID")
            if not state.available:
                raise DeviceBackendError("backend returned an unavailable device", error_code="DEVICE_OFFLINE", observed_state=state)
            return state

    def build_agent_commands(
        self,
        event: PerceptionEvent,
        planned_commands: Sequence[PlannedDeviceCommand],
    ) -> tuple[DeviceCommand, ...]:
        return tuple(
            DeviceCommand(
                device_id=item.device_id,
                action=item.action,
                arguments=item.arguments,
                source=event.source,
                confidence=event.confidence,
                request_id=event.event_id,
            )
            for item in planned_commands
        )

    def execute_agent_plan(
        self,
        event: PerceptionEvent,
        planned_commands: Sequence[PlannedDeviceCommand],
        confirmed: bool = False,
    ) -> SceneExecutionResult:
        preflight = self.preflight_event(event)
        if preflight is not None:
            raise RequestPlanConsistencyError(preflight)
        self._validate_request_plan(event, planned_commands)
        commands = self.build_agent_commands(event, planned_commands)
        # Preflight the complete plan before the first side effect.
        for command in commands:
            self.safety.check(command, confirmed=confirmed)
        results = []
        for command in commands:
            try:
                self.deduplicator.check(command)
                state = self._execute_backend(command)
            except (DeviceBackendError, DuplicateCommandError) as error:
                results.append(CommandExecutionResult(command, error=str(error), error_code=getattr(error, "error_code", None),
                                                     request_accepted=getattr(error, "request_accepted", None),
                                                     observed_state=getattr(error, "observed_state", None)))
            else:
                results.append(CommandExecutionResult(command, state=state))
        return SceneExecutionResult("agent_plan", tuple(results))


def _load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as file:
        value = json.load(file)
    if not isinstance(value, dict):
        raise ValueError(f"configuration root must be an object: {path}")
    return value
