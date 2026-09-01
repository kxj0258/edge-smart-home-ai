from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence


GUIDED_GESTURE_LABELS = (
    "Open_Palm",
    "Closed_Fist",
    "Pointing_Up",
    "Victory",
    "Thumb_Up",
    "Thumb_Down",
)


@dataclass(frozen=True, slots=True)
class GuidedPhase:
    duration_ms: int
    label: str | None
    prompt: str
    note: str
    overlay_prompt: str = ""
    hand: str | None = None


@dataclass(frozen=True, slots=True)
class GuidedPhaseState:
    index: int
    prompt: str
    overlay_prompt: str
    label: str | None
    hand: str | None
    remaining_ms: int


def build_default_guided_phases() -> tuple[GuidedPhase, ...]:
    chinese_names = {
        "Open_Palm": "张开手掌（继续播放）",
        "Closed_Fist": "握拳（暂停播放）",
        "Pointing_Up": "食指向上（播放下一集）",
        "Victory": "V字手势（开启观影模式）",
        "Thumb_Up": "竖起大拇指（音量调高）",
        "Thumb_Down": "大拇指向下（音量调低）",
    }
    phases = [
        GuidedPhase(15_000, None, "保持自然状态，不做控制手势", "initial neutral", "NEUTRAL")
    ]
    hand_names = (("right", "右手", "RIGHT"), ("left", "左手", "LEFT"))
    for label in GUIDED_GESTURE_LABELS:
        chinese_name = chinese_names[label]
        for hand, chinese_hand, overlay_hand in hand_names:
            for hand_repetition in range(1, 3):
                phases.extend(
                    (
                        GuidedPhase(
                            4_000, None,
                            f"准备：{chinese_hand} {chinese_name}，第{hand_repetition}遍",
                            f"prepare {hand} {label} repetition {hand_repetition}",
                            f"READY {overlay_hand} {label} #{hand_repetition}",
                            hand,
                        ),
                        GuidedPhase(
                            5_000, label,
                            f"开始并保持：{chinese_hand} {chinese_name}，第{hand_repetition}遍",
                            f"perform {hand} {label} repetition {hand_repetition}",
                            f"DO {overlay_hand} {label} #{hand_repetition}",
                            hand,
                        ),
                        GuidedPhase(
                            4_000, None, "请完全放下手，恢复自然状态",
                            f"release after {hand} {label} repetition {hand_repetition}",
                            "RELEASE / NEUTRAL",
                            hand,
                        ),
                    )
                )
    phases.append(
        GuidedPhase(
            30_000, None,
            "自然活动，但不要做六种控制手势",
            "final natural neutral",
            "NATURAL NEUTRAL",
        )
    )
    return tuple(phases)


class GuidedGestureProtocol:
    def __init__(
        self, start_ms: int, phases: Sequence[GuidedPhase] | None = None
    ) -> None:
        self.start_ms = start_ms
        self.phases = tuple(
            phases if phases is not None else build_default_guided_phases()
        )
        if not self.phases or any(phase.duration_ms <= 0 for phase in self.phases):
            raise ValueError("guided phases require positive durations")
        self._boundaries = []
        elapsed = 0
        for phase in self.phases:
            elapsed += phase.duration_ms
            self._boundaries.append(elapsed)
        self.duration_ms = elapsed

    def state(self, timestamp_ms: int) -> GuidedPhaseState | None:
        elapsed = timestamp_ms - self.start_ms
        if elapsed < 0:
            raise ValueError("timestamp precedes guided protocol start")
        for index, boundary in enumerate(self._boundaries):
            if elapsed < boundary:
                phase = self.phases[index]
                return GuidedPhaseState(
                    index=index,
                    prompt=phase.prompt,
                    overlay_prompt=phase.overlay_prompt or phase.prompt,
                    label=phase.label,
                    hand=phase.hand,
                    remaining_ms=boundary - elapsed,
                )
        return None

    def annotation(self, session_dir: Path, end_ms: int, complete: bool) -> dict[str, Any]:
        timeline_end = min(end_ms, self.start_ms + self.duration_ms)
        segments = []
        phase_start = self.start_ms
        for phase, boundary in zip(self.phases, self._boundaries):
            phase_end = min(self.start_ms + boundary, timeline_end)
            if phase_end <= phase_start:
                break
            segments.append(
                {
                    "start_ms": phase_start,
                    "end_ms": phase_end,
                    "label": phase.label,
                    "hand": phase.hand,
                    "note": phase.note,
                }
            )
            phase_start = phase_end
        return {
            "schema_version": 1,
            "session_dir": str(session_dir.resolve()),
            "timeline_start_ms": self.start_ms,
            "timeline_end_ms": timeline_end,
            "annotation_source": "guided_protocol_prompt",
            "protocol_complete": complete,
            "latency_note": "Prompt-to-trigger latency includes human reaction time.",
            "segments": segments,
        }
