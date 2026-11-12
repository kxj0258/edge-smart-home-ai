from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from threading import Event, Thread
import subprocess
import sys
import unittest

from edge_smart_home.audio.dispatch import EndpointDispatcher
from edge_smart_home.benchmark.audio_load import capabilities, finalize, prompts, compare, STAGES

ROOT = Path(__file__).resolve().parents[1]


class AudioLoadTests(unittest.TestCase):
    def test_full_queue_does_not_block_producer_or_shutdown(self):
        entered, release, submitted = Event(), Event(), Event()
        def handler(item):
            entered.set()
            release.wait(2)
        dispatch = EndpointDispatcher(handler, capacity=1)
        try:
            self.assertTrue(dispatch.submit(1))
            self.assertTrue(entered.wait(1))
            self.assertTrue(dispatch.submit(2))
            result = []
            def produce():
                result.append(dispatch.submit(3))
                submitted.set()
            producer = Thread(target=produce)
            producer.start()
            self.assertTrue(submitted.wait(.2), "audio producer waited on full command queue")
            producer.join(1)
            self.assertEqual(result, [False])
            dispatch.close(timeout=0)
            self.assertTrue(dispatch.metrics()["worker_alive"])
            self.assertEqual(dispatch.metrics()["cancelled"], 1)
            self.assertEqual(dispatch.metrics()["dropped"], 1)
            self.assertFalse(dispatch.submit(4))
            self.assertEqual(dispatch.metrics()["rejected_after_close"], 1)
            self.assertEqual(dispatch.metrics()["cancelled"], 1)
        finally:
            release.set()
            dispatch.close(timeout=1)
        self.assertFalse(dispatch.metrics()["worker_alive"])
        self.assertEqual(dispatch.metrics()["completed"], 1)

    def test_handler_error_does_not_kill_dispatch(self):
        failed, done = Event(), Event()
        def handler(item):
            if item == 1:
                raise ValueError("bad endpoint")
            done.set()
        dispatch = EndpointDispatcher(handler, on_error=lambda error: failed.set())
        dispatch.submit(1)
        self.assertTrue(failed.wait(1))
        dispatch.submit(2)
        self.assertTrue(done.wait(1))
        dispatch.close()
        self.assertEqual(dispatch.metrics()["failed"], 1)
        self.assertEqual(dispatch.metrics()["completed"], 1)

    def test_stage_capabilities_are_cumulative(self):
        self.assertFalse(any(capabilities("capture").values()))
        self.assertEqual(sum(capabilities("asr").values()), 1)
        self.assertEqual(sum(capabilities("vision").values()), 2)
        self.assertEqual(sum(capabilities("tts").values()), 3)
        self.assertTrue(all(capabilities("llm").values()))
        self.assertEqual(len(prompts(60)), 7)

    def test_unexercised_model_is_not_a_successful_run(self):
        result = finalize("llm", 60, {"callback_count": 600, "portaudio_overflow_count": 3},
                          {"camera_frames": 300}, {}, [])
        self.assertEqual(result["status"], "not_exercised")
        self.assertIn("llm_calls", result["unexercised"])
        self.assertIn("tts_completed", result["unexercised"])
        self.assertEqual(result["overflow_per_min"], 3)
        self.assertIsNone(result["accuracy"])

    def test_inflight_worker_invalidates_completion(self):
        result = finalize("capture", 10, {"callback_count": 100}, {},
                          {"worker_alive": True}, [])
        self.assertEqual(result["status"], "failed")

    def test_comparison_requires_distinct_runs_same_conditions_and_rates(self):
        runs = [{"run_id": f"{stage}-{i}", "stage": stage, "status": "completed",
                 "compatibility_key": "fixed", "overflow_per_min": i, "input_source": "synthetic_test",
                 "microphone": {"actual_sample_rate": 48000, "max_queue_wait_ms": 2}}
                for stage in STAGES for i in range(3)]
        self.assertTrue(compare(runs)["sufficient_repeats"])
        self.assertFalse(compare(runs + [runs[0]])["comparable"])
        runs[0]["microphone"]["actual_sample_rate"] = 44100
        self.assertFalse(compare(runs)["comparable"])
        runs[0]["status"] = "not_exercised"
        self.assertEqual(len(compare(runs)["excluded"]), 1)

    def test_plan_cli_does_not_import_or_open_sensor_runtime(self):
        # The default command runs in a fresh process, with imports of all sensor/model
        # packages forbidden. A plan must not need any optional device dependencies.
        code = '''
import runpy, sys
class Deny:
 def find_spec(self, fullname, path=None, target=None):
  if fullname.split('.')[0] in {'sounddevice', 'cv2', 'sherpa_onnx', 'mediapipe'}:
   raise RuntimeError('sensor runtime imported during plan: ' + fullname)
sys.meta_path.insert(0,Deny())
sys.argv=['run_audio_load_probe','--stage','llm']
runpy.run_path('scripts/run_audio_load_probe.py',run_name='__main__')
'''
        run = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(run.stdout)["stage"], "llm")


if __name__ == "__main__":
    unittest.main()
