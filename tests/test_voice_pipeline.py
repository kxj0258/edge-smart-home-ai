from __future__ import annotations

from pathlib import Path
import unittest

from edge_smart_home.app.pipeline import TextControlPipeline
from edge_smart_home.agent.interfaces import (
    IntelligentPathResult,
    IntelligentResultKind,
)
from edge_smart_home.app import AssistantOrchestrator
from edge_smart_home.audio.interfaces import (
    AudioChunk,
    AudioSource,
    StreamingRecognition,
    StreamingSpeechRecognizer,
)
from edge_smart_home.audio.pipeline import VoiceControlPipeline


class FakeSource(AudioSource):
    def chunks(self):
        yield AudioChunk([0.0], 16000)


class FakeAsr(StreamingSpeechRecognizer):
    def accept(self, chunk):
        return (StreamingRecognition("打开客厅灯", 1.0, 0.1),)

    def finish(self):
        return ()


class VoicePipelineTest(unittest.TestCase):
    def test_voice_event_reuses_control_pipeline(self) -> None:
        root = Path(__file__).resolve().parents[1]
        control = TextControlPipeline.from_config_dir(root / "configs")
        result = next(VoiceControlPipeline(FakeSource(), FakeAsr(), control).run())
        self.assertEqual(result.text, "打开客厅灯")
        self.assertIsNotNone(result.state)
        self.assertTrue(result.state.attributes["power"])

    def test_unroutable_transcript_is_reported_without_crashing(self) -> None:
        class UnroutableAsr(StreamingSpeechRecognizer):
            def accept(self, chunk):
                return (StreamingRecognition("今天天气怎么样", 1.0, 0.1),)

            def finish(self):
                return ()

        root = Path(__file__).resolve().parents[1]
        control = TextControlPipeline.from_config_dir(root / "configs")
        result = next(
            VoiceControlPipeline(FakeSource(), UnroutableAsr(), control).run()
        )
        self.assertEqual(result.text, "今天天气怎么样")
        self.assertIsNone(result.state)
        self.assertIsNotNone(result.error)

    def test_duplicate_voice_command_is_suppressed(self) -> None:
        root = Path(__file__).resolve().parents[1]
        control = TextControlPipeline.from_config_dir(root / "configs")
        pipeline = VoiceControlPipeline(FakeSource(), FakeAsr(), control)
        first = next(pipeline.run())
        second = next(pipeline.run())
        self.assertEqual(first.status, "executed")
        self.assertEqual(second.status, "duplicate_suppressed")

    def test_dialogue_message_is_exposed_to_voice_result(self) -> None:
        class DialogueAsr(StreamingSpeechRecognizer):
            def accept(self, chunk):
                return (StreamingRecognition("介绍一下", 1.0, 0.1),)

            def finish(self):
                return ()

        class DialoguePath:
            provider_name = "fake_dialogue"

            def route(self, event):
                return IntelligentPathResult(
                    kind=IntelligentResultKind.RESPONSE,
                    message="你好，我可以帮你控制家庭设备。",
                )

        root = Path(__file__).resolve().parents[1]
        control = TextControlPipeline.from_config_dir(root / "configs")
        orchestrator = AssistantOrchestrator(control, DialoguePath())
        result = next(
            VoiceControlPipeline(FakeSource(), DialogueAsr(), orchestrator).run()
        )
        self.assertEqual(result.status, "responded")
        self.assertEqual(result.message, "你好，我可以帮你控制家庭设备。")
        self.assertIsNone(result.state)

    def test_asr_consumption_can_be_separated_from_command_processing(self) -> None:
        root = Path(__file__).resolve().parents[1]
        control = TextControlPipeline.from_config_dir(root / "configs")
        pipeline = VoiceControlPipeline(FakeSource(), FakeAsr(), control)
        recognitions = tuple(pipeline.iter_recognitions())
        self.assertEqual(len(recognitions), 1)
        result = pipeline.process_recognition(recognitions[0])
        self.assertEqual(result.status, "executed")

    def test_voice_confirmation_message_and_saved_plan(self) -> None:
        root = Path(__file__).resolve().parents[1]
        orchestrator = AssistantOrchestrator.from_config_dir(root / "configs")
        pipeline = VoiceControlPipeline(FakeSource(), FakeAsr(), orchestrator)
        pending = pipeline.process_recognition(
            StreamingRecognition("安防模式", 1.0, 0.1)
        )
        self.assertEqual(pending.status, "confirmation_required")
        self.assertIn("确认或取消", pending.message)
        confirmed = pipeline.process_recognition(
            StreamingRecognition("确认", 1.0, 0.1)
        )
        self.assertEqual(confirmed.status, "executed")
        self.assertTrue(confirmed.state.succeeded)

    def test_conservative_asr_prefix_cleanup_keeps_sleep_scene(self) -> None:
        root = Path(__file__).resolve().parents[1]
        orchestrator = AssistantOrchestrator.from_config_dir(root / "configs")
        pipeline = VoiceControlPipeline(FakeSource(), FakeAsr(), orchestrator)
        result = pipeline.process_recognition(
            StreamingRecognition("我我想睡觉了", 1.0, 0.1)
        )
        self.assertEqual(result.status, "executed")
        self.assertEqual(result.state.scene_id, "sleep")


if __name__ == "__main__":
    unittest.main()
