from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from edge_smart_home.app.orchestrator import AssistantOrchestrator
from edge_smart_home.app.pipeline import ControlOutcome, TextControlPipeline
from edge_smart_home.common import DeviceState, EventSource, PerceptionEvent

from .interfaces import AudioSource, StreamingRecognition, StreamingSpeechRecognizer
from .diagnostics import AudioDiagnosticRecorder
from .feedback import AssistantFeedback


@dataclass(frozen=True, slots=True)
class VoiceControlResult:
    text: str
    state: ControlOutcome | None = None
    error: str | None = None
    status: str = "not_routed"
    audio_seconds: float = 0.0
    decode_seconds: float = 0.0
    rtf: float | None = None
    processing_ms: float = 0.0
    path: str = "fast_path"
    provider: str | None = None
    llm_calls: int = 0
    llm_latency_ms: float | None = None


class VoiceControlPipeline:
    def __init__(
        self,
        source: AudioSource,
        asr: StreamingSpeechRecognizer,
        control: TextControlPipeline | AssistantOrchestrator,
        diagnostics: AudioDiagnosticRecorder | None = None,
        feedback: AssistantFeedback | None = None,
    ) -> None:
        self.source = source
        self.asr = asr
        self.orchestrator = (
            control if isinstance(control, AssistantOrchestrator) else AssistantOrchestrator(control)
        )
        self.diagnostics = diagnostics
        self.feedback = feedback

    def run(self) -> Iterator[VoiceControlResult]:
        for chunk in self.source.chunks():
            if self.diagnostics:
                self.diagnostics.record_audio(chunk)
            for recognition in self.asr.accept(chunk):
                try:
                    result = self._handle_recognition(recognition)
                except Exception as error:
                    result = self._handle_processing_error(recognition, error)
                if result:
                    yield result

    def finish(self) -> Iterator[VoiceControlResult]:
        for recognition in self.asr.finish():
            try:
                result = self._handle_recognition(recognition)
            except Exception as error:
                result = self._handle_processing_error(recognition, error)
            if result:
                yield result

    def _handle_processing_error(
        self, recognition: StreamingRecognition, error: Exception
    ) -> VoiceControlResult | None:
        text = recognition.text.strip()
        if not text:
            return None
        if self.feedback:
            self.feedback.processing_error()
        result = VoiceControlResult(
            text=text,
            error=f"{type(error).__name__}: {error}",
            status="processing_error",
            audio_seconds=recognition.audio_seconds,
            decode_seconds=recognition.decode_seconds,
            rtf=recognition.rtf,
        )
        if self.diagnostics:
            self.diagnostics.record_result(
                text,
                result.status,
                {
                    "error": result.error,
                    "processing_ms": result.processing_ms,
                    "audio_seconds": result.audio_seconds,
                    "decode_seconds": result.decode_seconds,
                    "rtf": result.rtf,
                },
            )
        return result

    def _handle_recognition(
        self, recognition: StreamingRecognition
    ) -> VoiceControlResult | None:
        text = recognition.text.strip()
        if not text:
            return None
        if self.feedback:
            self.feedback.received(text)
        event = PerceptionEvent(source=EventSource.VOICE, label=text)
        metrics = {
            "audio_seconds": recognition.audio_seconds,
            "decode_seconds": recognition.decode_seconds,
            "rtf": recognition.rtf,
        }
        response = self.orchestrator.handle_event(event)
        if self.feedback:
            self.feedback.response(response)
        if response.outcome is None:
            result = VoiceControlResult(
                text=text,
                error=response.error,
                status=response.status,
                processing_ms=response.processing_ms,
                path=response.path,
                provider=response.provider,
                llm_calls=response.llm_calls,
                llm_latency_ms=response.llm_latency_ms,
                **metrics,
            )
            if self.diagnostics:
                self.diagnostics.record_result(
                    text,
                    response.status,
                    {
                        "error": response.error,
                        "processing_ms": response.processing_ms,
                        "path": response.path,
                        "provider": response.provider,
                        "llm_calls": response.llm_calls,
                        "llm_latency_ms": response.llm_latency_ms,
                        **metrics,
                    },
                )
            return result
        result = VoiceControlResult(
            text=text,
            state=response.outcome,
            status=response.status,
            processing_ms=response.processing_ms,
            path=response.path,
            provider=response.provider,
            llm_calls=response.llm_calls,
            llm_latency_ms=response.llm_latency_ms,
            **metrics,
        )
        if self.diagnostics:
            detail = {
                **_diagnostic_detail(response.outcome),
                "processing_ms": response.processing_ms,
                "path": response.path,
                "provider": response.provider,
                "llm_calls": response.llm_calls,
                "llm_latency_ms": response.llm_latency_ms,
                **metrics,
            }
            self.diagnostics.record_result(text, response.status, detail)
        return result


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
