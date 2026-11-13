from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field, replace
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
    text_fallback: str | None = None
    feedback_error: str | None = None
    diagnostic_error: str | None = None
    error_stage: str | None = None


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
        self, recognition: StreamingRecognition, *, report_empty: bool = False,
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
            if not recognition.text.strip() and report_empty:
                result = self._publish_result(VoiceControlResult(
                    text="", status="empty_transcript", error_code="ASR_EMPTY_TRANSCRIPT",
                    error_stage="asr_output",
                    message="未识别到明确指令，可切换文本输入。",
                    text_fallback="可切换文本输入，重新输入明确家庭指令。",
                    audio_seconds=recognition.audio_seconds, decode_seconds=recognition.decode_seconds,
                    rtf=recognition.rtf, trace=trace,
                ))
                trace.emit("voice_result", status=result.status)
                return result
            try:
                result = self._handle_recognition(recognition)
            except Exception as error:
                result = self._handle_processing_error(recognition, error)
            trace.emit("voice_result", status=result.status if result else "empty_transcript")
            return result

    def process_audio_error(self, error: Exception, *, stage: str = "asr_decode") -> VoiceControlResult:
        """Report an explicit utterance input/dependency/decode failure safely.

        Callers own file/model loading and select its observed stage. This does
        not retry ASR, route reference text, consume microphone input or execute
        a command. Live background silence keeps its legacy no-result behavior.
        """
        if stage not in {"audio_input", "asr_dependency", "asr_decode"}:
            raise ValueError("unsupported audio failure stage")
        code = getattr(error, "code", None) or {
            "audio_input": "AUDIO_INPUT_FAILED",
            "asr_dependency": "ASR_DEPENDENCY_UNAVAILABLE",
            "asr_decode": "ASR_DECODE_FAILED",
        }[stage]
        return self._publish_result(VoiceControlResult(
            text="", status="asr_error", error_code=code,
            error_stage=stage,
            error=f"{type(error).__name__}: {error}",
            message="语音输入或识别不可用，可切换文本输入。",
            text_fallback="可切换文本输入，重新输入明确家庭指令。",
        ))

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
        result = VoiceControlResult(
            text=text,
            error=f"{type(error).__name__}: {error}",
            status="processing_error",
            error_code=error_code or getattr(error, "error_code", None) or IntentErrorCode.INTENT_INTERNAL_ERROR.value,
            audio_seconds=recognition.audio_seconds,
            decode_seconds=recognition.decode_seconds,
            rtf=recognition.rtf,
            trace=recognition.trace,
            text_fallback="请先核对设备状态，再使用文本入口；不会自动重试。",
            error_stage="intent_pipeline",
        )
        return self._publish_result(result)

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
        result = VoiceControlResult(
            text=text,
            message=(
                "设备结果已记录，请查看状态回读。"
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
            error_stage={"device_error": "device", "partial_failure": "device", "plan_rejected": "plan_validation",
                         "request_rejected": "request", "clarification_required": "request", "not_routed": "intent",
                         "needs_intelligence": "intent", "unavailable": "intent"}.get(response.status),
            text_fallback=(None if response.status in {"executed", "responded"} else
                           "请在当前会话确认或取消；切换文本入口需重新说明请求。"
                           if response.status == "confirmation_required" else
                           "请先核对设备状态，再使用文本入口；不会自动重试。"
                           if response.status in {"device_error", "partial_failure"} else
                           "可切换文本输入，重新输入明确家庭指令。"),
            **metrics,
        )
        return self._publish_result(result, response)

    def _publish_result(self, result: VoiceControlResult, response=None) -> VoiceControlResult:
        """Observers cannot reclassify a command that already took effect."""
        if response is not None and result.status == "executed":
            try:
                result = replace(result, message=completion_message(response))
            except Exception as error:
                result = replace(result, feedback_error=f"{type(error).__name__}: {error}",
                                 text_fallback="动作结果已记录，请查看文字反馈，勿重复提交。")
        if self.feedback:
            try:
                self.feedback.response(response if response is not None else result)
            except Exception as error:
                detail = f"{type(error).__name__}: {error}"
                result = replace(result, feedback_error="; ".join(v for v in (result.feedback_error, detail) if v),
                                 text_fallback=("动作结果已记录，请查看文字反馈，勿重复提交。"
                                                if result.state is not None else result.text_fallback))
        if self.diagnostics:
            try:
                detail = {
                    **(_diagnostic_detail(result.state) if result.state is not None else {}),
                    "message": result.message,
                    "error": result.error,
                    "error_code": result.error_code,
                    "request_accepted": result.request_accepted,
                    "observed_state": _diagnostic_detail(result.observed_state) if result.observed_state else None,
                    "feedback_error": result.feedback_error,
                    "text_fallback": result.text_fallback,
                    "error_stage": result.error_stage,
                    "processing_ms": result.processing_ms,
                    "path": result.path,
                    "provider": result.provider,
                    "llm_calls": result.llm_calls,
                    "llm_latency_ms": result.llm_latency_ms,
                    "audio_seconds": result.audio_seconds,
                    "decode_seconds": result.decode_seconds,
                    "rtf": result.rtf,
                    "trace": result.trace.snapshot() if result.trace else None,
                }
                self.diagnostics.record_result(result.text, result.status, detail)
            except Exception as error:
                result = replace(result, diagnostic_error=f"{type(error).__name__}: {error}")
        return result


def _normalize_transcript(text: str) -> str:
    """Apply only a conservative correction for a repeated ASR prefix."""
    return re.sub(r"^(我){2,}", "我", text.strip())


def _diagnostic_detail(outcome: ControlOutcome) -> dict:
    if isinstance(outcome, DeviceState):
        return {
            "device_id": outcome.device_id,
            "available": outcome.available,
            "attributes": dict(outcome.attributes),
            "updated_at": outcome.updated_at.isoformat(),
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
