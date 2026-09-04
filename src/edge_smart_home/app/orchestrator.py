from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from threading import Lock
from time import monotonic, perf_counter

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
from edge_smart_home.common import DeviceCommand, EventSource, PerceptionEvent
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
    path: str = "fast_path"
    provider: str | None = None
    llm_calls: int = 0
    llm_latency_ms: float | None = None
    confirmation_message: str | None = None


@dataclass(frozen=True, slots=True)
class PendingConfirmation:
    event: PerceptionEvent
    commands: tuple[DeviceCommand, ...]
    scene_id: str | None
    path: str
    provider: str | None
    intelligent_result: IntelligentPathResult | None
    created_at: float


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
        self._confirmation_lock = Lock()
        self._event_lock = Lock()
        self._pending_confirmation: PendingConfirmation | None = None
        self.confirmation_timeout_seconds = 15.0

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
                        keep_alive=ollama.get("keep_alive", "10m"),
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

    def preload_model(self) -> float | None:
        """Warm up a configured local provider without routing a user event."""
        preload = getattr(self.intelligent_path, "preload", None)
        if not callable(preload):
            return None
        return float(preload())

    def handle_text(self, text: str, confirmed: bool = False) -> AssistantResponse:
        return self.handle_event(
            PerceptionEvent(source=EventSource.TEXT, label=text), confirmed=confirmed
        )

    def handle_event(
        self, event: PerceptionEvent, confirmed: bool = False
    ) -> AssistantResponse:
        # Camera and command-worker threads share one backend and one local
        # model provider. Serialize event handling to keep state transitions
        # and model requests deterministic.
        with self._event_lock:
            return self._handle_event(event, confirmed=confirmed)

    def _handle_event(
        self, event: PerceptionEvent, confirmed: bool = False
    ) -> AssistantResponse:
        started = perf_counter()
        if confirmed:
            with self._confirmation_lock:
                self._pending_confirmation = None
        else:
            confirmation_response = self.confirm_pending(event)
            if confirmation_response is not None:
                return confirmation_response
        local_dialogue = self._local_dialogue_response(event, started)
        if local_dialogue is not None:
            return local_dialogue
        try:
            outcome = self.control.handle_event(event, confirmed=confirmed)
        except ConfirmationRequiredError as error:
            scene_id, commands = self.control.resolve_event(event)
            self._store_pending(
                event,
                commands,
                scene_id,
                path="fast_path",
                provider=None,
                intelligent_result=None,
            )
            return self._error_response(
                event,
                "confirmation_required",
                error,
                started,
                confirmation_message=self._confirmation_message(commands),
            )
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
                commands = self.control.build_agent_commands(event, result.commands)
                self._store_pending(
                    event,
                    commands,
                    "agent_plan",
                    path="intelligent_path",
                    provider=self._provider_name(),
                    intelligent_result=result,
                )
                return self._intelligent_response(
                    event,
                    "confirmation_required",
                    result,
                    started,
                    str(error),
                    confirmation_message=self._confirmation_message(commands),
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
                path="intelligent_path",
                provider=self._provider_name(),
                llm_calls=self._llm_calls(),
                llm_latency_ms=self._llm_latency_ms(),
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

    def _intelligent_response(
        self,
        event: PerceptionEvent,
        status: str,
        result: IntelligentPathResult,
        started: float,
        error: str | None = None,
        confirmation_message: str | None = None,
    ) -> AssistantResponse:
        return AssistantResponse(
            event=event,
            status=status,
            error=error,
            processing_ms=round((perf_counter() - started) * 1000, 3),
            intelligent_result=result,
            path="intelligent_path",
            provider=self._provider_name(),
            llm_calls=self._llm_calls(),
            llm_latency_ms=self._llm_latency_ms(),
            confirmation_message=confirmation_message,
        )

    def confirm_pending(self, event: PerceptionEvent) -> AssistantResponse | None:
        """Handle an exact voice/text confirmation for one saved safe plan.

        A confirmation executes the already validated command set. It never
        re-routes the confirmation text through the LLM and never asks the
        model to generate a second plan.
        """
        if event.source not in {EventSource.TEXT, EventSource.VOICE}:
            return None
        decision = self._confirmation_decision(event.label)
        with self._confirmation_lock:
            pending = self._pending_confirmation
            if pending and monotonic() - pending.created_at > self.confirmation_timeout_seconds:
                self._pending_confirmation = None
                pending = None

        if pending is None:
            if decision is None:
                return None
            return self._confirmation_message_response(
                event, "当前没有待确认的操作。"
            )

        started = perf_counter()
        if decision is None:
            return self._confirmation_message_response(
                event, "刚才的操作需要确认，请说“确认”或“取消”。",
                status="clarification_required",
            )
        with self._confirmation_lock:
            self._pending_confirmation = None
        if decision == "cancel":
            return self._confirmation_message_response(event, "已取消。")

        try:
            outcome = self.control.execute_resolved(
                pending.event,
                pending.scene_id,
                pending.commands,
                confirmed=True,
            )
        except (ConfirmationRequiredError, DeviceBackendError, DuplicateCommandError) as error:
            return AssistantResponse(
                event=event,
                status="device_error",
                error=str(error),
                processing_ms=round((perf_counter() - started) * 1000, 3),
                path=pending.path,
                provider=pending.provider,
                intelligent_result=pending.intelligent_result,
                llm_calls=0,
                llm_latency_ms=None,
            )
        succeeded = (
            not hasattr(outcome, "succeeded") or bool(outcome.succeeded)
        )
        return AssistantResponse(
            event=event,
            status="executed" if succeeded else "partial_failure",
            outcome=outcome,
            processing_ms=round((perf_counter() - started) * 1000, 3),
            path=pending.path,
            provider=pending.provider,
            intelligent_result=pending.intelligent_result,
            llm_calls=0,
            llm_latency_ms=None,
        )

    def _store_pending(
        self,
        event: PerceptionEvent,
        commands: tuple[DeviceCommand, ...],
        scene_id: str | None,
        *,
        path: str,
        provider: str | None,
        intelligent_result: IntelligentPathResult | None,
    ) -> None:
        with self._confirmation_lock:
            self._pending_confirmation = PendingConfirmation(
                event=event,
                commands=commands,
                scene_id=scene_id,
                path=path,
                provider=provider,
                intelligent_result=intelligent_result,
                created_at=monotonic(),
            )

    @staticmethod
    def _confirmation_decision(text: str) -> str | None:
        normalized = re.sub(r"[\s，。！？,.!?]", "", text).lower()
        if normalized in {"确认", "确认执行", "执行", "可以", "好的", "好", "是的"}:
            return "confirm"
        if normalized in {"取消", "不要", "算了", "不用", "不用执行", "不执行"}:
            return "cancel"
        return None

    def _confirmation_message(self, commands: tuple[DeviceCommand, ...]) -> str:
        names = []
        action_names = {
            "lock": "锁上",
            "unlock": "解锁",
            "start_recording": "开始录像",
            "disable_privacy": "关闭隐私模式",
            "restart": "重启",
        }
        for command in commands:
            device = self.control.router.devices.get(command.device_id, {})
            device_name = device.get("display_name", command.device_id)
            if command.action == "disable_privacy":
                names.append(f"关闭{device_name}的隐私模式")
            elif command.action == "start_recording":
                names.append(f"开始{device_name}录像")
            else:
                names.append(f"{action_names.get(command.action, '执行')}{device_name}")
        details = "、".join(names) if names else "这项操作"
        return f"即将{details}，这是高风险操作，请说确认或取消。"

    def _confirmation_message_response(
        self,
        event: PerceptionEvent,
        message: str,
        *,
        status: str = "responded",
    ) -> AssistantResponse:
        started = perf_counter()
        return AssistantResponse(
            event=event,
            status=status,
            processing_ms=round((perf_counter() - started) * 1000, 3),
            intelligent_result=IntelligentPathResult(
                kind=(
                    IntelligentResultKind.RESPONSE
                    if status == "responded"
                    else IntelligentResultKind.CLARIFICATION
                ),
                message=message,
            ),
            path="fast_path",
            provider=None,
            llm_calls=0,
            llm_latency_ms=None,
        )

    def _local_dialogue_response(
        self, event: PerceptionEvent, started: float
    ) -> AssistantResponse | None:
        if event.source not in {EventSource.TEXT, EventSource.VOICE}:
            return None
        text = re.sub(r"[\s，。！？,.!?]", "", event.label).lower()
        if text in {"你好", "嗨", "小兴你好", "你好小兴", "晚上好", "早上好", "hello"}:
            message = "你好，我是小兴，可以帮你控制家里的设备。"
        elif text in {"你是谁", "你叫什么", "你叫什么名字"}:
            message = "我是小兴，负责理解你的家庭设备指令，并在安全校验后执行。"
        elif text in {
            "你能控制哪些设备",
            "你能控制什么设备",
            "你支持哪些设备",
            "你能控制什么",
            "可以控制哪些设备",
            "能控制哪些设备",
            "你会什么",
            "请介绍你能做什么",
            "怎么使用你",
            "我应该怎么使用你",
        }:
            names = [
                str(config.get("display_name", device_id))
                for device_id, config in self.control.router.devices.items()
            ]
            message = "我可以控制或查询：" + "、".join(names) + "。"
        elif text in {"谢谢", "感谢", "谢谢你"} or text.startswith("谢谢"):
            message = "不客气，有需要时叫我小兴。"
        elif text in {"你会把我的话直接执行吗", "你会直接执行我的话吗"}:
            message = "不会。设备命令必须先经过确定性路由、安全校验和必要确认。"
        else:
            return None
        result = IntelligentPathResult(
            kind=IntelligentResultKind.RESPONSE,
            message=message,
        )
        return AssistantResponse(
            event=event,
            status="responded",
            processing_ms=round((perf_counter() - started) * 1000, 3),
            intelligent_result=result,
            path="fast_path",
            provider=None,
            llm_calls=0,
            llm_latency_ms=None,
        )

    def _provider_name(self) -> str | None:
        if self.intelligent_path is None:
            return None
        return getattr(
            self.intelligent_path,
            "provider_name",
            type(self.intelligent_path).__name__,
        )

    def _llm_calls(self) -> int:
        if self.intelligent_path is None:
            return 0
        return int(getattr(self.intelligent_path, "last_call_count", 0))

    def _llm_latency_ms(self) -> float | None:
        if self.intelligent_path is None:
            return None
        return getattr(self.intelligent_path, "last_llm_latency_ms", None)

    @staticmethod
    def _error_response(
        event: PerceptionEvent,
        status: str,
        error: Exception,
        started: float,
        confirmation_message: str | None = None,
    ) -> AssistantResponse:
        return AssistantResponse(
            event=event,
            status=status,
            error=str(error),
            processing_ms=round((perf_counter() - started) * 1000, 3),
            confirmation_message=confirmation_message,
        )
