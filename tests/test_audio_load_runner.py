"""Exercise all five real runner branches with fake sensors/models, never hardware."""
from pathlib import Path
from threading import Event
from time import monotonic_ns, sleep
from types import SimpleNamespace
from unittest.mock import patch
from contextlib import ExitStack, redirect_stdout
import importlib.util
import io
import json
import tempfile
import unittest

import numpy as np
from edge_smart_home.app.orchestrator import AssistantOrchestrator
from edge_smart_home.app.pipeline import TextControlPipeline
from edge_smart_home.agent.interfaces import IntelligentPathResult, IntelligentResultKind
from edge_smart_home.audio.interfaces import AudioChunk, StreamingRecognition
from edge_smart_home.benchmark.audio_load import STAGES, capabilities, compare

ROOT = Path(__file__).resolve().parents[1]


class FakeMicrophone:
    def __init__(self, *args):
        self.stop_event = Event()
        self.count = 0
    def chunks(self):
        while not self.stop_event.wait(.005):
            self.count += 1
            now = monotonic_ns()
            yield AudioChunk(np.zeros(160), 16000, self.count, now, now)
    def stop(self): self.stop_event.set()
    def metrics(self):
        return dict(callback_count=self.count, actual_sample_rate=16000,
                    portaudio_overflow_count=0, queue_drop_count=0, max_queue_wait_ms=0)


class FakeAsr:
    def __init__(self, *args): pass
    def accept(self, chunk):
        result = StreamingRecognition("客厅有点暗，帮我处理一下", .01, .001)
        result.trace.mark("recognition_ready")
        return (result,)
    def finish(self): return ()


class FakeRecorder:
    serial = 0
    def __init__(self, root, metadata):
        FakeRecorder.serial += 1
        self.session_id = str(FakeRecorder.serial)
        self.session_dir = root / self.session_id
        self.session_dir.mkdir(parents=True)
        self.metadata = metadata
        record = dict(files_sha256={}, artifacts={}, runtime={}, config_sha256="test")
        (self.session_dir / "run_record.json").write_text(json.dumps(record))
    def record_audio(self, chunk): pass
    def record_trace(self, record): pass
    def record_result(self, *args): pass
    def update_summary(self, data): pass
    def close(self): (self.session_dir / "summary.json").write_text('{}')


class FakeFeedback:
    def response(self, response):
        trace = response.event.trace
        now = monotonic_ns()
        trace.add("tts_playback", now, now, error_type=None)
        trace.emit("feedback_complete")
    def close(self): pass


class FakeVision:
    def __init__(self, *args): pass
    def recognize_rgb(self, *args): return ()
    def close(self): pass


class FakeCamera:
    def __init__(self, *args): self.released = False
    def isOpened(self): return True
    def set(self, *args): pass
    def read(self):
        sleep(.005)
        return True, np.zeros((1, 1, 3))
    def release(self): self.released = True


class FakePlanner:
    provider_name = "ollama"
    last_call_count = 1
    def route(self, event):
        return IntelligentPathResult(kind=IntelligentResultKind.CLARIFICATION,
                                     message="请补充房间")


class AudioLoadRunnerTest(unittest.TestCase):
    def test_all_stages_close_and_report_only_exercised_work(self):
        spec = importlib.util.spec_from_file_location("probe", ROOT / "scripts/run_audio_load_probe.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        fake_cv = SimpleNamespace(VideoCapture=FakeCamera, CAP_PROP_FRAME_WIDTH=1,
                                  CAP_PROP_FRAME_HEIGHT=2, CAP_PROP_FPS=3, CAP_PROP_BUFFERSIZE=4,
                                  COLOR_BGR2RGB=5, cvtColor=lambda image, kind: image)
        def app(config):
            return AssistantOrchestrator(TextControlPipeline.from_config_dir(config), FakePlanner())
        reports = []
        with tempfile.TemporaryDirectory() as folder, ExitStack() as stack:
            stack.enter_context(patch.dict("os.environ", {"EDGE_SMART_HOME_BACKEND": "mock"}))
            stack.enter_context(patch.dict("sys.modules", {"cv2": fake_cv}))
            for target, value in [
                ("edge_smart_home.audio.microphone.SoundDeviceMicrophone", FakeMicrophone),
                ("edge_smart_home.audio.sherpa_onnx_adapters.SherpaOnnxStreamingAsr", FakeAsr),
                ("edge_smart_home.audio.diagnostics.AudioDiagnosticRecorder", FakeRecorder),
                ("edge_smart_home.audio.feedback.create_feedback", lambda *a, **kw: FakeFeedback()),
                ("edge_smart_home.vision.mediapipe_adapter.MediaPipeGestureRecognizer", FakeVision),
                ("edge_smart_home.app.orchestrator.AssistantOrchestrator.from_config_dir", app),
            ]:
                stack.enter_context(patch(target, value))
            stack.enter_context(patch("urllib.request.urlopen", side_effect=lambda *a, **kw:
                io.StringIO('{"models":[{"name":"qwen3:4b","digest":"fake-digest"}]}')))
            for stage in STAGES:
                args = module.parser().parse_args(["--stage", stage, "--condition", "synthetic_test"])
                args.duration = .25
                args.output_dir = Path(folder)
                plan = dict(stage=stage, enabled=capabilities(stage), prompts=[])
                with redirect_stdout(io.StringIO()):
                    module.run(args, plan, input_source="synthetic_test")
            for path in sorted(Path(folder).glob('*/load_summary.json')):
                reports.append(json.loads(path.read_text()))
            self.assertEqual(len(reports), 5)
            self.assertTrue(all(r["status"] == "completed" for r in reports), reports)
            self.assertTrue(all(not r["dispatcher"]["worker_alive"] for r in reports))
            self.assertTrue(compare(reports)["comparable"])
            self.assertFalse(compare(reports)["sufficient_repeats"])
            self.assertTrue(all(r["accuracy"] is None for r in reports))


if __name__ == "__main__":
    unittest.main()
