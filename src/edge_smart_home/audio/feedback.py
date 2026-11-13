from __future__ import annotations

from pathlib import Path
from queue import Empty, Queue
import re
from threading import Event, Thread
from typing import Any, Callable, Protocol

import numpy as np
from time import monotonic_ns
from edge_smart_home.common.observability import activate, current_trace, span


class FeedbackSpeaker(Protocol):
    def speak(self, text: str) -> None: ...


class ConsoleFeedbackSpeaker:
    """Dependency-free feedback sink for reproducible demos."""

    def speak(self, text: str) -> None:
        print(f"[语音反馈-文字] {text}", flush=True)


class NullFeedbackSpeaker:
    def speak(self, text: str) -> None:
        return None


class QueuedFeedbackSpeaker:
    """Serialize feedback playback without blocking the live control loop."""

    def __init__(
        self,
        delegate: FeedbackSpeaker,
        max_queue_size: int = 32,
        before_speak: Callable[[], None] | None = None,
        after_speak: Callable[[], None] | None = None,
    ) -> None:
        self.delegate = delegate
        self._queue: Queue = Queue(maxsize=max_queue_size)
        self._closed = Event()
        self._before_speak = before_speak
        self._after_speak = after_speak
        self._worker = Thread(target=self._run, name="feedback-speaker", daemon=True)
        self._worker.start()

    def speak(self, text: str) -> None:
        if self._closed.is_set():
            return
        self._queue.put((text, current_trace(), monotonic_ns()))

    def metrics(self) -> dict:
        return {"queue_depth": self._queue.qsize(), "worker_alive": self._worker.is_alive()}

    def close(self, timeout: float | None = 3.0) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        # Do not wait for queued speech during Ctrl+C. Finish only an
        # already-playing utterance, then let the daemon worker exit.
        while True:
            try:
                self._queue.get_nowait()
            except Empty:
                break
            else:
                self._queue.task_done()
        self._queue.put_nowait(None)
        self._worker.join(timeout=timeout)

    def _run(self) -> None:
        while True:
            text = self._queue.get()
            try:
                if text is None:
                    return
                text, trace, enqueued_ns = text
                with activate(trace):
                    if trace:
                        trace.add("feedback_queue_wait", enqueued_ns, monotonic_ns())
                    if self._before_speak:
                        self._before_speak()
                    try:
                        with span("feedback_speaker"):
                            self.delegate.speak(text)
                    finally:
                        if self._after_speak:
                            with span("echo_guard"):
                                self._after_speak()
                        if trace:
                            trace.emit("feedback_complete")
            finally:
                self._queue.task_done()


class SherpaOnnxFeedbackSpeaker:
    """Offline sherpa-onnx TTS speaker with blocking playback."""

    def __init__(
        self,
        model_dir: Path,
        *,
        speaker_id: int = 0,
        speed: float = 1.0,
        num_threads: int = 2,
        provider: str = "cpu",
        audio_device: int | None = None,
    ) -> None:
        from .tts import SherpaOnnxVitsTts

        self.tts = SherpaOnnxVitsTts(
            model_dir,
            num_threads=num_threads,
            provider=provider,
        )
        if not 0 <= speaker_id < self.tts.num_speakers:
            raise ValueError(
                f"speaker ID {speaker_id} is outside [0, {self.tts.num_speakers - 1}]"
            )
        if speed <= 0:
            raise ValueError("feedback speed must be positive")
        self.speaker_id = speaker_id
        self.speed = speed
        self.audio_device = audio_device

    def speak(self, text: str) -> None:
        try:
            import sounddevice as sd
        except ImportError as error:
            raise RuntimeError(
                "sounddevice is required for real-time voice feedback"
            ) from error
        with span("tts_synthesis"):
            audio = self.tts.synthesize(text, self.speaker_id, self.speed)
        output_rate = _select_output_sample_rate(
            sd, self.audio_device, audio.sample_rate
        )
        samples = _resample(audio.samples, audio.sample_rate, output_rate)
        with span("tts_playback"):
            sd.play(samples, samplerate=output_rate, device=self.audio_device, blocking=True)


class ResilientFeedbackSpeaker:
    """Keep feedback failures from affecting command execution."""

    def __init__(
        self,
        delegate: FeedbackSpeaker,
        fallback: FeedbackSpeaker | None = None,
    ) -> None:
        self.delegate = delegate
        self.fallback = fallback or ConsoleFeedbackSpeaker()

    def speak(self, text: str) -> None:
        try:
            self.delegate.speak(text)
        except Exception as error:
            self.fallback.speak(
                f"{text}（语音播放不可用，已降级为文字反馈：{type(error).__name__}）"
            )


class AssistantFeedback:
    """Turn an AssistantResponse into acknowledgement and completion speech."""

    def __init__(self, speaker: FeedbackSpeaker) -> None:
        self.speaker = speaker

    def close(self) -> None:
        close = getattr(self.speaker, "close", None)
        if callable(close):
            close()

    def metrics(self) -> dict:
        metrics = getattr(self.speaker, "metrics", None)
        return metrics() if callable(metrics) else {}

    def received(self, text: str) -> None:
        """Keep the legacy hook without speaking an acknowledgement.

        The input transcript is already visible in the terminal/window. Speaking
        it again increases latency and makes acoustic echo more likely. Result
        feedback is emitted by :meth:`response` instead.
        """
        return None

    def announce(self, text: str) -> None:
        """Speak a non-device status update, such as a gesture mode change."""
        self._speak(text)

    def response(self, assistant_response: Any) -> None:
        event = getattr(assistant_response, "event", None)
        with activate(getattr(event, "trace", None) or current_trace()), span("feedback_submit"):
            self._response(assistant_response)

    def _response(self, assistant_response: Any) -> None:
        status = assistant_response.status
        if status == "executed":
            self._speak(completion_message(assistant_response))
        elif status == "confirmation_required":
            self._speak(
                getattr(assistant_response, "confirmation_message", None)
                or "这是高风险操作，请说确认或取消。"
            )
        elif status == "clarification_required":
            message = self._result_message(assistant_response) or "请补充房间或设备信息。"
            self._speak(f"需要补充信息：{message}")
        elif status == "request_rejected":
            self._speak("指令不符合安全规则，已拒绝执行。")
        elif status == "plan_rejected":
            self._speak("控制计划未通过安全校验，已拒绝执行。")
        elif status == "device_error":
            self._speak("设备执行失败。")
        elif status == "partial_failure":
            self._speak("部分设备执行失败。")
        elif status == "needs_intelligence":
            result = assistant_response.intelligent_result
            self._speak(
                result.message if result is not None and result.message else "当前能力暂不支持。"
            )
        elif status == "responded":
            self._speak(
                self._result_message(assistant_response)
                or "我理解了这条请求，但不需要执行设备操作。"
            )
        elif status == "processing_error":
            self.processing_error()
        elif status == "empty_transcript":
            self._speak("未识别到明确指令，可切换文本输入。")
        elif status == "asr_error":
            self._speak("语音识别不可用，可切换文本输入。")
        elif status == "not_routed":
            self._speak("未识别到支持的家庭指令，可切换文本输入。")
        else:
            self._speak("处理结果无法确认，请再说一遍。")

    def processing_error(self) -> None:
        self._speak("语音处理失败，请先核对设备状态，可切换文本输入。")

    def _speak(self, text: str) -> None:
        """Keep TTS concise while preserving the full result for the UI/log."""
        self.speaker.speak(_compact_speech(text))

    def _completed_text(self, assistant_response: Any) -> str:
        return completion_message(assistant_response)

    @staticmethod
    def _result_message(assistant_response: Any) -> str | None:
        result = assistant_response.intelligent_result
        return result.message if result is not None else assistant_response.error


def completion_message(assistant_response: Any) -> str:
    """Describe the action that actually completed, not the model's intent text."""
    if getattr(assistant_response, "status", "executed") != "executed":
        return "设备执行未确认完成。"
    outcome = assistant_response.outcome
    if getattr(outcome, "backend_kind", None) == "home_assistant":
        return ("已从家庭平台回读到目标状态。" if outcome.completion_scope == "ha_target_observed"
                else "已从家庭平台读取设备状态。")
    if (outcome is not None and hasattr(outcome, "results") and
            any(getattr(item.state, "backend_kind", None) == "home_assistant" for item in outcome.results
                if getattr(item, "state", None) is not None)):
        return "已从家庭平台回读到目标状态。"
    if outcome is not None and hasattr(outcome, "results"):
        scene_message = _scene_completion_text(getattr(outcome, "scene_id", None))
        if scene_message:
            return scene_message
        completed = [
            _describe_command(item.command.device_id, item.command.action)
            for item in outcome.results
            if item.succeeded
        ]
        if completed:
            if len(completed) == 1:
                return "已完成，" + completed[0] + "。"
            if len(completed) <= 3:
                return "已完成，" + "、".join(completed) + "。"
            return "已完成，" + "、".join(completed[:2]) + "等设备控制。"
    result = assistant_response.intelligent_result
    if result is not None and result.commands:
        if len(result.commands) == 1:
            command = result.commands[0]
            return "已完成，" + _describe_command(command.device_id, command.action) + "。"
        descriptions = [
            _describe_command(command.device_id, command.action)
            for command in result.commands
        ]
        if len(descriptions) <= 3:
            return "已完成，" + "、".join(descriptions) + "。"
        return "已完成，" + "、".join(descriptions[:2]) + "等设备控制。"
    if outcome is not None and hasattr(outcome, "device_id"):
        event = getattr(assistant_response, "event", None)
        label = getattr(event, "label", None)
        if label:
            return f"已完成，{_completion_action(str(label))}。"
        return f"已完成，{_device_name(outcome.device_id)}。"
    return "指令已完成。"


def _describe_command(device_id: str, action: str) -> str:
    actions = {
        "turn_on": "打开",
        "turn_off": "关闭",
        "open": "打开",
        "close": "关闭",
        "open_app": "打开应用",
        "play": "开始播放",
        "volume_up": "调高音量",
        "volume_down": "调低音量",
        "set_volume": "设置音量",
        "set_brightness": "设置亮度",
        "set_position": "设置位置",
        "set_speed": "设置风速",
        "set_temperature": "设置温度",
        "set_hvac_mode": "设置空调模式",
        "restart": "重启",
        "next": "播放下一集",
        "pause": "暂停播放",
        "resume": "继续播放",
        "query_state": "查询状态",
        "enable_privacy": "开启隐私模式",
        "disable_privacy": "关闭隐私模式",
        "start_recording": "开始录像",
        "stop_recording": "停止录像",
        "lock": "锁上",
        "unlock": "解锁",
    }
    action_name = actions.get(action, "执行操作")
    device_name = _device_name(device_id)
    return f"{action_name}{device_name}"


def _scene_completion_text(scene_id: str | None) -> str | None:
    return {
        "home": "已完成，开启回家模式。",
        "away": "已完成，开启离家模式。",
        "movie": "已完成，开启观影模式。",
        "sleep": "已完成，开启睡眠模式。",
        "wake_up": "已完成，开启起床模式。",
        "dinner": "已完成，开启用餐模式。",
        "night_light": "已完成，开启夜灯模式。",
        "energy_saving": "已完成，开启节能模式。",
        "security": "已完成，开启安防模式。",
    }.get(scene_id)


def _completion_action(text: str) -> str:
    """Turn a voice transcript into a short action-oriented completion phrase."""
    compact = re.sub(r"[\s，。！？,.!?]", "", text).strip()
    if any(phrase in compact for phrase in ("我想听音乐", "我想听歌", "播放音乐", "播放歌曲", "来点音乐")):
        return "播放音乐"
    if "下一集" in compact:
        return "播放下一集"
    if "上一集" in compact:
        return "播放上一集"
    if "下一首" in compact or "下一曲" in compact:
        return "播放下一首"
    if "上一首" in compact or "上一曲" in compact:
        return "播放上一首"
    if "暂停" in compact:
        return "暂停播放"
    return compact or "设备控制"


def _compact_speech(text: str, max_chars: int = 48) -> str:
    """Bound model-provided speech without changing the stored response."""
    compact = re.sub(r"\s+", "", str(text)).strip()
    if len(compact) <= max_chars:
        return compact
    for index in range(max_chars, max(0, max_chars - 16), -1):
        if compact[index - 1] in "。！？；，,!?;":
            return compact[:index]
    return compact[:max_chars].rstrip("，,、") + "。"


def _device_name(device_id: str) -> str:
    return {
        "living_room_light": "客厅灯",
        "living_room_tv": "客厅电视",
        "living_room_set_top_box": "客厅机顶盒",
        "living_room_air_conditioner": "客厅空调",
        "living_room_curtain": "客厅窗帘",
        "living_room_camera": "客厅摄像头",
        "front_door_camera": "门口摄像头",
        "living_room_fan": "客厅风扇",
        "bedroom_air_purifier": "卧室空气净化器",
        "living_room_smart_plug": "客厅智能插座",
        "living_room_speaker": "客厅音箱",
        "bedroom_light": "卧室灯",
        "bedroom_air_conditioner": "卧室空调",
        "bedroom_curtain": "卧室窗帘",
        "kitchen_light": "厨房灯",
        "kitchen_smart_plug": "厨房智能插座",
        "kitchen_smoke_sensor": "厨房烟雾传感器",
        "bathroom_fan": "卫生间排气扇",
        "front_door_lock": "入户门锁",
        "front_door_sensor": "入户门磁",
        "living_room_presence_sensor": "客厅人体存在传感器",
        "water_leak_sensor": "卫生间漏水传感器",
        "home_gateway": "家庭网关",
    }.get(device_id, "设备")


def _select_output_sample_rate(sounddevice: Any, device: int | None, source_rate: int) -> int:
    """Choose a rate accepted by the selected PortAudio output device."""

    candidates = [source_rate, 48000, 44100, 32000, 16000, 22050, 8000]
    seen: set[int] = set()
    for rate in candidates:
        rate = int(rate)
        if rate in seen:
            continue
        seen.add(rate)
        try:
            sounddevice.check_output_settings(
                device=device,
                channels=1,
                dtype="float32",
                samplerate=rate,
            )
        except Exception:
            continue
        return rate
    raise RuntimeError(
        f"no supported output sample rate for audio device {device!r}; "
        f"TTS rate is {source_rate} Hz"
    )


def _resample(samples: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    if source_rate == target_rate or samples.size == 0:
        return np.asarray(samples, dtype=np.float32)
    target_length = max(1, round(samples.size * target_rate / source_rate))
    source_positions = np.arange(samples.size, dtype=np.float64)
    target_positions = np.linspace(
        0.0, max(0.0, samples.size - 1), target_length
    )
    return np.interp(target_positions, source_positions, samples).astype(np.float32)


def create_feedback(
    mode: str,
    *,
    tts_model_dir: Path | None = None,
    speaker_id: int = 0,
    speed: float = 1.0,
    audio_device: int | None = None,
    asynchronous: bool = False,
    before_speak: Callable[[], None] | None = None,
    after_speak: Callable[[], None] | None = None,
) -> AssistantFeedback | None:
    if mode == "none":
        return None
    if mode == "console":
        return AssistantFeedback(ConsoleFeedbackSpeaker())
    if mode != "tts":
        raise ValueError(f"unsupported feedback mode: {mode}")
    if tts_model_dir is None:
        raise ValueError("--tts-model-dir is required when --feedback tts is used")
    speaker: FeedbackSpeaker = ResilientFeedbackSpeaker(
        SherpaOnnxFeedbackSpeaker(
            tts_model_dir,
            speaker_id=speaker_id,
            speed=speed,
            audio_device=audio_device,
        )
    )
    if asynchronous:
        speaker = QueuedFeedbackSpeaker(
            speaker,
            before_speak=before_speak,
            after_speak=after_speak,
        )
    return AssistantFeedback(speaker)
