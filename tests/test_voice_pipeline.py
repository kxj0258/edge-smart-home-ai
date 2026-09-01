from __future__ import annotations

from pathlib import Path
import unittest

from edge_smart_home.app.pipeline import TextControlPipeline
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


if __name__ == "__main__":
    unittest.main()
