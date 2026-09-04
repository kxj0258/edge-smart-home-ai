from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from collections.abc import Sequence

from edge_smart_home.agent import PlannedDeviceCommand
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
)


ControlOutcome = DeviceState | SceneExecutionResult


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
        scene = self.scene_router.match(event)
        if scene:
            scene_id, commands = scene
            # Check the complete scene before the first side effect. A high-risk
            # command late in the scene must not leave a partially applied scene.
            for command in commands:
                self.safety.check(command, confirmed=confirmed)
            results = []
            for command in commands:
                try:
                    self.deduplicator.check(command)
                    state = self.backend.execute(command)
                except (
                    ConfirmationRequiredError,
                    DeviceBackendError,
                    DuplicateCommandError,
                ) as error:
                    results.append(CommandExecutionResult(command, error=str(error)))
                else:
                    results.append(CommandExecutionResult(command, state=state))
            return SceneExecutionResult(scene_id, tuple(results))

        command = self.router.route(event)
        self.safety.check(command, confirmed=confirmed)
        self.deduplicator.check(command)
        return self.backend.execute(command)

    def execute_agent_plan(
        self,
        event: PerceptionEvent,
        planned_commands: Sequence[PlannedDeviceCommand],
        confirmed: bool = False,
    ) -> SceneExecutionResult:
        commands = tuple(
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
        # Preflight the complete plan before the first side effect.
        for command in commands:
            self.safety.check(command, confirmed=confirmed)
        results = []
        for command in commands:
            try:
                self.deduplicator.check(command)
                state = self.backend.execute(command)
            except (DeviceBackendError, DuplicateCommandError) as error:
                results.append(CommandExecutionResult(command, error=str(error)))
            else:
                results.append(CommandExecutionResult(command, state=state))
        return SceneExecutionResult("agent_plan", tuple(results))


def _load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as file:
        value = json.load(file)
    if not isinstance(value, dict):
        raise ValueError(f"configuration root must be an object: {path}")
    return value
