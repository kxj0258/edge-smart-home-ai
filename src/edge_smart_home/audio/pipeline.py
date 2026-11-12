from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from time import monotonic_ns
from uuid import uuid4
from edge_smart_home.common.observability import activate, Trace
import re

from edge_smart_home.app.orchestrator import AssistantOrchestrator
from edge_smart_home.app.pipeline import ControlOutcome, TextControlPipeline
from edge_smart_home.agent.error_codes import IntentErrorCode
from edge_smart_home.common import DeviceState, EventSource, PerceptionEvent

from .interfaces import AudioSource, StreamingRecognition, StreamingSpeechRecognizer
from .diagnostics import AudioDiagnosticRecorder
from .feedback import AssistantFeedback, completion_message


@dataclass(frozen=True, slots=True)
class VoiceControlResult:
    text: str
    message: str | None = None
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
    trace: Trace | None = field(default=None, compare=False, repr=False)
    error_code: str | None = None
    request_accepted: bool | None = None
    observed_state: DeviceState | None = None


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
        if diagnostics:
            self.orchestrator.session_id = diagnostics.session_id
            self.orchestrator.trace_sink = diagnostics.record_trace
        self.feedback = feedback

    def run(self) -> Iterator[VoiceControlResult]:
        for recognition in self.iter_recognitions():
            result = self.process_recognition(recognition)
            if result:
                yield result

    def iter_recognitions(self) -> Iterator[StreamingRecognition]:
        """Continuously consume microphone audio and yield ASR endpoints.

        Command execution is intentionally not performed here. A live caller
        can put these lightweight recognition results on another queue so an
        Ollama request cannot block microphone/ASR consumption.
        """
        for chunk in self.source.chunks():
            if self.diagnostics:
                self.diagnostics.record_audio(chunk)
            for recognition in self.asr.accept(chunk):
                yield recognition
        for recognition in self.asr.finish():
            yield recognition

    def process_recognition(
        self, recognition: StreamingRecognition
    ) -> VoiceControlResult | None:
        """Route one already-decoded endpoint outside the audio consumer."""
        trace = recognition.trace
        trace.event_id = trace.event_id or str(uuid4())
        trace.session_id = self.orchestrator.session_id
        trace.sink = self.orchestrator.trace_sink
        now = monotonic_ns()
        if "recognition_ready" in trace.marks:
            trace.add("recognition_dispatch_wait", trace.marks["recognition_ready"], now)
        trace.mark("command_worker_start", now)
        with activate(trace):
            try:
                result = self._handle_recognition(recognition)
            except Exception as error:
                result = self._handle_processing_error(recognition, error)
            trace.emit("voice_result", status=result.status if result else "empty_transcript")
            return result

    def finish(self) -> Iterator[VoiceControlResult]:
        for recognition in self.asr.finish():
            result = self.process_recognition(recognition)
            if result:
                yield result

    def _handle_processing_error(
        self, recognition: StreamingRecognition, error: Exception,
        error_code: str | None = None,
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
            error_code=error_code or getattr(error, "error_code", None) or IntentErrorCode.INTENT_INTERNAL_ERROR.value,
            audio_seconds=recognition.audio_seconds,
            decode_seconds=recognition.decode_seconds,
            rtf=recognition.rtf,
            trace=recognition.trace,
        )
        if self.diagnostics:
            self.diagnostics.record_result(
                text,
                result.status,
                {
                    "error": result.error,
                    "error_code": result.error_code,
                    "trace": recognition.trace.snapshot(),
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
        text = _normalize_transcript(recognition.text)
        if not text:
            return None
        if len(text) < 2:
            return self._handle_processing_error(
                recognition, ValueError("recognized transcript is too short to route safely"),
                IntentErrorCode.REQUEST_INPUT_INVALID.value)
        event = PerceptionEvent(source=EventSource.VOICE, label=text, trace=recognition.trace,
                                event_id=recognition.trace.event_id)
        metrics = {
            "audio_seconds": recognition.audio_seconds,
            "decode_seconds": recognition.decode_seconds,
            "rtf": recognition.rtf,
            "trace": recognition.trace,
        }
        response = self.orchestrator.handle_event(event)
        if self.feedback:
            self.feedback.response(response)
        if response.outcome is None:
            result = VoiceControlResult(
                text=text,
                message=(
                    completion_message(response)
                    if response.status == "executed"
                    else response.confirmation_message
                    or (
                        response.intelligent_result.message
                        if response.intelligent_result
                        else None
                    )
                ),
                error=response.error,
                error_code=response.error_code,
                request_accepted=response.request_accepted,
                observed_state=response.observed_state,
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
                        "message": result.message,
                        "error": response.error,
                        "error_code": response.error_code,
                        "request_accepted": response.request_accepted,
                        "observed_state": _diagnostic_detail(response.observed_state) if response.observed_state else None,
                        "processing_ms": response.processing_ms,
                        "path": response.path,
                        "provider": response.provider,
                        "llm_calls": response.llm_calls,
                        "llm_latency_ms": response.llm_latency_ms,
                        **{**metrics, "trace": recognition.trace.snapshot()},
                    },
                )
            return result
        result = VoiceControlResult(
            text=text,
            message=(
                completion_message(response)
                if response.status == "executed"
                else response.confirmation_message
                or (
                    response.intelligent_result.message
                    if response.intelligent_result
                    else None
                )
            ),
            state=response.outcome,
            error=response.error,
            error_code=response.error_code,
            request_accepted=response.request_accepted,
            observed_state=response.observed_state,
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
                "message": result.message,
                "error": result.error,
                "error_code": result.error_code,
                "backend_kind": result.state.backend_kind if result.state and isinstance(result.state, DeviceState) else None,
                "processing_ms": response.processing_ms,
                "path": response.path,
                "provider": response.provider,
                "llm_calls": response.llm_calls,
                "llm_latency_ms": response.llm_latency_ms,
                **{**metrics, "trace": recognition.trace.snapshot()},
            }
            self.diagnostics.record_result(text, response.status, detail)
        return result


def _normalize_transcript(text: str) -> str:
    """Apply only a conservative correction for a repeated ASR prefix."""
    return re.sub(r"^(我){2,}", "我", text.strip())


def _diagnostic_detail(outcome: ControlOutcome) -> dict:
    if isinstance(outcome, DeviceState):
        return {
            "device_id": outcome.device_id,
            "attributes": dict(outcome.attributes),
            "backend_kind": outcome.backend_kind,
            "completion_scope": outcome.completion_scope,
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
                "error_code": result.error_code,
                "request_accepted": result.request_accepted,
                "observed_state": _diagnostic_detail(result.observed_state) if result.observed_state else None,
            }
            for result in outcome.results
        ],
    }
