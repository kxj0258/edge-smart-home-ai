from __future__ import annotations

from types import SimpleNamespace
import unittest

import numpy as np

from edge_smart_home.audio.feedback import (
    AssistantFeedback,
    _resample,
    _select_output_sample_rate,
    create_feedback,
)


class FakeSpeaker:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def speak(self, text: str) -> None:
        self.messages.append(text)


class FakeSoundDevice:
    @staticmethod
    def check_output_settings(*, samplerate, **_kwargs):
        if samplerate != 48000:
            raise ValueError("unsupported")


class AssistantFeedbackTest(unittest.TestCase):
    def test_feedback_acknowledges_and_reports_completed_command(self) -> None:
        speaker = FakeSpeaker()
        feedback = AssistantFeedback(speaker)
        feedback.received("打开客厅灯")
        feedback.response(
            SimpleNamespace(
                status="executed",
                outcome=SimpleNamespace(device_id="living_room_light"),
                intelligent_result=None,
            )
        )
        self.assertEqual(speaker.messages[0], "已收到指令：打开客厅灯")
        self.assertIn("已完成：客厅灯", speaker.messages[1])

    def test_feedback_reports_clarification_without_execution(self) -> None:
        speaker = FakeSpeaker()
        feedback = AssistantFeedback(speaker)
        feedback.response(
            SimpleNamespace(
                status="clarification_required",
                error=None,
                intelligent_result=SimpleNamespace(
                    message="请说明房间", commands=()
                ),
            )
        )
        self.assertIn("需要补充信息：请说明房间", speaker.messages[0])

    def test_feedback_uses_chinese_for_agent_statuses_and_device_names(self) -> None:
        speaker = FakeSpeaker()
        feedback = AssistantFeedback(speaker)
        feedback.response(
            SimpleNamespace(
                status="plan_rejected",
                error=None,
                intelligent_result=None,
            )
        )
        feedback.response(
            SimpleNamespace(
                status="executed",
                outcome=SimpleNamespace(device_id="living_room_set_top_box"),
                intelligent_result=None,
            )
        )
        self.assertIn("安全校验", speaker.messages[0])
        self.assertIn("客厅机顶盒", speaker.messages[1])
        self.assertNotIn("plan_rejected", speaker.messages[0])
        self.assertNotIn("living_room", speaker.messages[1])

    def test_none_feedback_has_no_runtime_dependency(self) -> None:
        self.assertIsNone(create_feedback("none"))

    def test_selects_hardware_supported_output_rate(self) -> None:
        self.assertEqual(
            _select_output_sample_rate(FakeSoundDevice(), 3, 22050),
            48000,
        )

    def test_resamples_tts_audio(self) -> None:
        samples = np.linspace(-1.0, 1.0, 22050, dtype=np.float32)
        converted = _resample(samples, 22050, 48000)
        self.assertEqual(converted.dtype, np.float32)
        self.assertEqual(len(converted), 48000)


if __name__ == "__main__":
    unittest.main()
