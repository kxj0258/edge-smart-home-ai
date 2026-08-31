from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from edge_smart_home.app.pipeline import ControlOutcome, TextControlPipeline
from edge_smart_home.common import DeviceState, EventSource, PerceptionEvent, SceneExecutionResult
from edge_smart_home.device import ConfirmationRequiredError, DeviceBackendError
from edge_smart_home.router import UnsupportedCommandError

from .interfaces import AudioSource, StreamingSpeechRecognizer
from .diagnostics import AudioDiagnosticRecorder


@dataclass(frozen=True, slots=True)
class VoiceControlResult:
    text: str
    state: ControlOutcome | None = None
    error: str | None = None


class VoiceControlPipeline:
    def __init__(
        self,
        source: AudioSource,
        asr: StreamingSpeechRecognizer,
        control: TextControlPipeline,
        diagnostics: AudioDiagnosticRecorder | None = None,
    ) -> None:
        self.source = source
        self.asr = asr
        self.control = control
        self.diagnostics = diagnostics

    def run(self) -> Iterator[VoiceControlResult]:
        for chunk in self.source.chunks():
            if self.diagnostics:
                self.diagnostics.record_audio(chunk)
            for final_text in self.asr.accept(chunk):
                text = final_text.strip()
                if text:
                    event = PerceptionEvent(source=EventSource.VOICE, label=text)
                    try:
                        state = self.control.handle_event(event)
                    except (
                        ConfirmationRequiredError,
                        UnsupportedCommandError,
                        DeviceBackendError,
                    ) as error:
                        result = VoiceControlResult(text=text, error=str(error))
                        if self.diagnostics:
                            self.diagnostics.record_result(
                                text, "not_routed", {"error": str(error)}
                            )
                        yield result
                    else:
                        result = VoiceControlResult(text=text, state=state)
                        if self.diagnostics:
                            detail = _diagnostic_detail(state)
                            self.diagnostics.record_result(text, "executed", detail)
                        yield result


def _diagnostic_detail(outcome: ControlOutcome) -> dict:
    if isinstance(outcome, DeviceState):
        return {
            "device_id": outcome.device_id,
            "attributes": dict(outcome.attributes),
        }
    return {
        "scene_id": outcome.scene_id,
        "succeeded": outcome.succeeded,
        "commands": [
            {
                "device_id": result.command.device_id,
                "action": result.command.action,
                "succeeded": result.succeeded,
                "error": result.error,
            }
            for result in outcome.results
        ],
    }
