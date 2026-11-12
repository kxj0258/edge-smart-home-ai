from pathlib import Path
import importlib.util
import json
import tempfile
from threading import Event, Thread
from time import monotonic_ns
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from edge_smart_home.app.orchestrator import AssistantOrchestrator
from edge_smart_home.app.pipeline import TextControlPipeline
from edge_smart_home.app.cli import serialize_response
from edge_smart_home.common import EventSource, PerceptionEvent
from edge_smart_home.common.observability import Trace, activate
from edge_smart_home.audio.feedback import QueuedFeedbackSpeaker
from edge_smart_home.audio.diagnostics import AudioDiagnosticRecorder
from edge_smart_home.audio.interfaces import AudioChunk, StreamingRecognition
from edge_smart_home.audio.microphone import SoundDeviceMicrophone
from edge_smart_home.audio.pipeline import VoiceControlPipeline
from edge_smart_home.benchmark.run_record import build_run_record
from edge_smart_home.device import DeviceBackendError
from edge_smart_home.vision.interfaces import GestureObservation
from edge_smart_home.vision.pipeline import GestureControlPipeline
from edge_smart_home.vision.temporal_filter import GestureTemporalFilter

ROOT = Path(__file__).resolve().parents[1]


def orchestrator():
    # Deliberately omit the configured LLM; tests cannot contact a model service.
    return AssistantOrchestrator(TextControlPipeline.from_config_dir(ROOT / "configs"))


class ObservabilityTests(unittest.TestCase):
    def test_gesture_inference_excludes_device_execution(self):
        clock = [0]
        class Recognizer:
            def recognize_rgb(self, frame, timestamp):
                clock[0] += 10_000_000
                return (GestureObservation("Open_Palm", .99, timestamp),)
        app = orchestrator()
        original = app.control.backend.execute
        def backend(command):
            clock[0] += 90_000_000
            return original(command)
        pipeline = GestureControlPipeline(
            Recognizer(), GestureTemporalFilter(.6, 1, 1, 0),
            {"Open_Palm": "打开客厅灯"}, app)
        with patch("edge_smart_home.vision.pipeline.monotonic_ns", side_effect=lambda: clock[0]), \
                patch.object(app.control.backend, "execute", side_effect=backend):
            rows = pipeline.process_frame(None, 1)
        self.assertEqual(rows[0][1].status, "executed")
        self.assertEqual(pipeline.last_inference_ms, 10)
        self.assertEqual(clock[0], 100_000_000)

    def test_failing_trace_sink_does_not_repeat_a_successful_action(self):
        app = orchestrator()
        app.trace_sink = lambda record: (_ for _ in ()).throw(OSError("disk full"))
        with patch.object(app.control.backend, "execute", wraps=app.control.backend.execute) as execute:
            response = app.handle_text("打开客厅灯")
        self.assertEqual(response.status, "executed")
        self.assertEqual(execute.call_count, 1)
        self.assertEqual(response.event.trace.sink_errors, 1)

    def test_safety_failure_has_no_backend_span(self):
        app = orchestrator()
        response = app.handle_text("重启网关")
        self.assertEqual(response.status, "confirmation_required")
        spans = response.event.trace.snapshot()["spans"]
        self.assertTrue(any(s["name"] == "safety" and s["error_type"] for s in spans))
        self.assertFalse(any(s["name"] == "backend" for s in spans))
        confirmed = app.handle_text("确认")
        self.assertEqual(confirmed.status, "executed")
        self.assertNotEqual(confirmed.event.trace.trace_id, response.event.trace.trace_id)

    def test_backend_failure_and_command_identity_survive_serialization(self):
        app = orchestrator()
        with patch.object(app.control.backend, "execute", side_effect=DeviceBackendError("offline")):
            response = app.handle_text("打开客厅灯")
        payload = json.loads(json.dumps(serialize_response(response)))
        self.assertEqual(payload["status"], "device_error")
        backend = next(s for s in payload["trace"]["spans"] if s["name"] == "backend")
        self.assertEqual(backend["error_type"], "DeviceBackendError")
        self.assertTrue(backend["request_id"])
        self.assertIsNone(payload["trace"]["latency_ms"]["speech_end_to_device_confirmed"])

    def test_lock_wait_is_separate_from_execution(self):
        app = orchestrator()
        rows = []
        app._event_lock.acquire()
        event = PerceptionEvent(EventSource.TEXT, "打开客厅灯")
        worker = Thread(target=lambda: rows.append(app.handle_event(event)))
        worker.start()
        app._event_lock.release()
        worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        spans = rows[0].event.trace.snapshot()["spans"]
        wait = next(s for s in spans if s["name"] == "orchestrator_lock_wait")
        backend = next(s for s in spans if s["name"] == "backend")
        self.assertLessEqual(wait["end_ns"], backend["start_ns"])

    def test_microphone_drop_and_overflow_are_distinct(self):
        mic = SoundDeviceMicrophone(100, microphone_sample_rate=16000)
        class InputStream:
            def __init__(self, **kw): self.callback = kw["callback"]
            def __enter__(self):
                for i in range(52):
                    self.callback(np.zeros((1600, 1), dtype=np.float32), 1600, None,
                                  SimpleNamespace(input_overflow=i == 0))
                return self
            def __exit__(self, *args): pass
        fake = SimpleNamespace(InputStream=InputStream, PortAudioError=RuntimeError)
        with patch.dict("sys.modules", {"sounddevice": fake}):
            stream = mic.chunks()
            chunk = next(stream)
            mic.stop()
            stream.close()
        self.assertEqual(mic.queue_drop_count, 2)
        self.assertEqual(mic.portaudio_overflow_count, 1)
        self.assertEqual(chunk.sequence, 3)
        self.assertTrue(chunk.discontinuity)
        self.assertGreaterEqual(chunk.dequeued_ns, chunk.callback_ns)

    def test_voice_recorder_round_trip_and_idempotent_close(self):
        with tempfile.TemporaryDirectory() as folder:
            recorder = AudioDiagnosticRecorder(Path(folder), {"input_source": "test_fake"})
            recorder.record_audio(AudioChunk(np.zeros(160), 16000, sequence=1,
                                            callback_ns=1, dequeued_ns=2))
            pipeline = VoiceControlPipeline(None, None, orchestrator(), diagnostics=recorder)
            recognition = StreamingRecognition("打开客厅灯", .1, .01)
            recognition.trace.mark("recognition_ready", monotonic_ns() - 1_000_000)
            result = pipeline.process_recognition(recognition)
            recorder.close()
            recorder.close()
            self.assertEqual(result.status, "executed")
            row = json.loads((recorder.session_dir / "events.jsonl").read_text())
            self.assertEqual(row["session_id"], recorder.session_id)
            self.assertEqual(row["trace_id"], recognition.trace.trace_id)
            self.assertIsNone(row["end_to_end_ms"])
            self.assertTrue((recorder.session_dir / "run_record.json").exists())

    def test_async_feedback_retains_trace(self):
        done = Event()
        trace = Trace()
        records = []
        trace.sink = records.append
        speaker = QueuedFeedbackSpeaker(SimpleNamespace(speak=lambda text: done.set()))
        with activate(trace):
            speaker.speak("已完成")
        self.assertTrue(done.wait(2))
        speaker.close()
        self.assertTrue(any(r["kind"] == "feedback_complete" and r["trace_id"] == trace.trace_id
                            for r in records))

    def test_freeze_detects_config_change(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "configs").mkdir()
            config = root / "configs/test.json"
            config.write_text('{"threshold": 0.6}')
            first = build_run_record(root, "first")
            config.write_text('{"threshold": 0.7}')
            second = build_run_record(root, "second")
            self.assertNotEqual(first["config_sha256"], second["config_sha256"])
            self.assertIsNone(first["metrics"])

    def test_summary_does_not_count_feedback_as_new_request(self):
        spec = importlib.util.spec_from_file_location("summary", ROOT / "scripts/summarize_traces.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        trace = Trace()
        trace.add("backend", 0, 2_000_000)
        result = module.summarize([trace.snapshot(), trace.snapshot()])
        self.assertEqual(result["trace_count"], 1)
        metric = next(m for m in result["metrics"] if m["metric"] == "stage_total.backend")
        self.assertEqual(metric["sample_count"], 1)
        self.assertEqual(metric["p95_ms"], 2)


if __name__ == "__main__":
    unittest.main()
