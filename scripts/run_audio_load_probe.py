"""Bounded incremental microphone load probe. Default prints a plan only."""
from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
import json
import os
from threading import Event, Lock, Thread
from time import monotonic, monotonic_ns

from edge_smart_home.benchmark.audio_load import (
    STAGES, capabilities, prompts, compatibility_key, finalize,
)

ROOT = Path(__file__).resolve().parents[1]


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--stage", choices=STAGES, required=True)
    p.add_argument("--duration", type=float, default=60)
    p.add_argument("--run", action="store_true", help="Open microphone and enabled workloads")
    p.add_argument("--condition", default="unspecified", help="Room/noise/distance/speaker condition ID")
    p.add_argument("--mic-device", type=int)
    p.add_argument("--mic-sample-rate", type=int)
    p.add_argument("--camera", type=int, default=0)
    p.add_argument("--preview", action="store_true", help="Adds a basic OpenCV preview, not full Demo UI")
    p.add_argument("--asr-model-dir", type=Path, default=ROOT / "models/sherpa-onnx-streaming-zipformer-zh-xlarge-int8-2025-06-30")
    p.add_argument("--gesture-model", type=Path, default=ROOT / "models/gesture_recognizer.task")
    p.add_argument("--tts-model-dir", type=Path, default=ROOT / "models/sherpa-onnx-vits-zh-ll")
    p.add_argument("--output-dir", type=Path, default=ROOT / "evaluation/audio_load")
    return p


def main():
    p = parser()
    args = p.parse_args()
    if not 1 <= args.duration <= 600:
        p.error("duration must be finite and between 1 and 600 seconds")
    if args.mic_sample_rate is not None and args.mic_sample_rate <= 0:
        p.error("mic sample rate must be positive")
    plan = {"stage": args.stage, "enabled": capabilities(args.stage),
            "duration_seconds": args.duration, "condition": args.condition,
            "backend": "mock", "prompts": prompts(args.duration),
            "note": "Read each prompt once. Prompts do not constitute ground truth. "
                    "No microphone, camera, playback or inference unless --run is supplied."}
    if not args.run:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return
    if args.condition == "unspecified":
        p.error("a real run requires --condition to identify recording conditions")
    run(args, plan)


def run(args, plan, *, input_source="real_mic"):
    from edge_smart_home.app.orchestrator import AssistantOrchestrator
    from edge_smart_home.app.pipeline import TextControlPipeline
    from edge_smart_home.audio.diagnostics import AudioDiagnosticRecorder
    from edge_smart_home.audio.dispatch import EndpointDispatcher
    from edge_smart_home.audio.feedback import create_feedback
    from edge_smart_home.audio.microphone import SoundDeviceMicrophone
    from edge_smart_home.audio.pipeline import VoiceControlPipeline
    from edge_smart_home.audio.sherpa_onnx_adapters import SherpaOnnxStreamingAsr
    from edge_smart_home.benchmark.run_record import resource_sample
    from edge_smart_home.vision.mediapipe_adapter import MediaPipeGestureRecognizer

    os.environ["EDGE_SMART_HOME_BACKEND"] = "mock"
    config = ROOT / "configs"
    audio = json.loads((config / "audio.json").read_text())
    vision = json.loads((config / "vision.json").read_text())["gesture"]
    enabled = plan["enabled"]
    metadata = {**plan, "input_source": input_source, "audio_config": audio,
                "vision_config": vision, "config_dir": str(config),
                "asr_model_dir": str(args.asr_model_dir), "gesture_model": str(args.gesture_model),
                "tts_model_dir": str(args.tts_model_dir), "hotwords": str(config / "hotwords.txt")}
    recorder = AudioDiagnosticRecorder(args.output_dir, metadata)
    print(f"Session: {recorder.session_dir}", flush=True)
    stop = Event()
    blocked = Event()
    lock = Lock()
    counts = Counter()
    statuses = Counter()
    errors = []
    camera = recognizer = feedback = dispatcher = asr_thread = None
    mic = SoundDeviceMicrophone(audio["chunk_duration_ms"], args.mic_device,
                                args.mic_sample_rate or audio.get("microphone_sample_rate"),
                                blocked.is_set)
    started = None
    interrupted = False
    resources = []
    measured_mic = None

    def fail(error):
        with lock:
            errors.append({"type": type(error).__name__, "message": str(error)})
        stop.set()

    def trace_sink(row):
        if row.get("kind") == "feedback_complete":
            if any(item["name"] == "tts_playback" and not item.get("error_type")
                   for item in row.get("spans", [])):
                with lock:
                    counts["tts_completed"] += 1
        recorder.record_trace(row)

    try:
        app = (AssistantOrchestrator.from_config_dir(config) if enabled["llm"] else
               AssistantOrchestrator(TextControlPipeline.from_config_dir(config)))
        if enabled["llm"] and getattr(app.intelligent_path, "provider_name", None) != "ollama":
            raise ValueError("LLM stage requires configured ollama provider")
        app.session_id = recorder.session_id
        app.trace_sink = trace_sink
        if enabled["tts"]:
            def resume():
                stop.wait(.5)
                blocked.clear()
            feedback = create_feedback("tts", tts_model_dir=args.tts_model_dir, asynchronous=True,
                                       before_speak=blocked.set, after_speak=resume)
        pipeline = None
        if enabled["asr"]:
            asr = SherpaOnnxStreamingAsr(
                args.asr_model_dir / "tokens.txt", args.asr_model_dir / "encoder.int8.onnx",
                args.asr_model_dir / "decoder.onnx", args.asr_model_dir / "joiner.int8.onnx",
                audio["sample_rate"], audio["asr"], config / "hotwords.txt")
            pipeline = VoiceControlPipeline(mic, asr, app, diagnostics=recorder, feedback=feedback)
            app.trace_sink = trace_sink
        if enabled["vision"]:
            import cv2
            recognizer = MediaPipeGestureRecognizer(args.gesture_model, vision)
            camera = cv2.VideoCapture(args.camera)
            if not camera.isOpened():
                raise RuntimeError(f"cannot open camera {args.camera}")
            camera.set(cv2.CAP_PROP_FRAME_WIDTH, vision["camera_width"])
            camera.set(cv2.CAP_PROP_FRAME_HEIGHT, vision["camera_height"])
            camera.set(cv2.CAP_PROP_FPS, vision["camera_fps"])
            camera.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        def handle(recognition):
            result = pipeline.process_recognition(recognition)
            if result:
                with lock:
                    statuses[result.status] += 1
                    counts["llm_calls"] += result.llm_calls
                print(f"[{args.stage}] {result.text}: {result.status}", flush=True)

        dispatcher = EndpointDispatcher(handle, on_error=fail)

        def consume():
            try:
                if pipeline is None:
                    for chunk in mic.chunks():
                        recorder.record_audio(chunk)
                else:
                    for recognition in pipeline.iter_recognitions():
                        if stop.is_set():
                            break
                        with lock:
                            counts["recognitions"] += 1
                        if not dispatcher.submit(recognition):
                            recognition.trace.session_id = recorder.session_id
                            recognition.trace.sink = trace_sink
                            recognition.trace.emit("recognition_dropped", reason="queue_full_or_stopped")
            except Exception as error:
                fail(error)

        conditions = {"duration": args.duration, "condition": args.condition,
                      "mic_device": args.mic_device, "mic_sample_rate": args.mic_sample_rate,
                      "camera": args.camera, "preview": args.preview,
                      "prompts": plan["prompts"], "backend": "mock"}
        # Read identity only; no warmup/generation outside the measured LLM workload.
        from urllib.request import urlopen
        from urllib.parse import urlsplit
        agent_config = json.loads((config / "agent.json").read_text())
        model_config = agent_config.get("ollama", {})
        url = urlsplit(model_config.get("endpoint", "http://127.0.0.1:11434/api/generate"))
        identity = {"model": model_config.get("model"), "digest": None}
        try:
            with urlopen(f"{url.scheme}://{url.netloc}/api/tags", timeout=2) as response:
                tags = json.load(response)
            identity["digest"] = next((item.get("digest") for item in tags.get("models", [])
                                       if item.get("name") == identity["model"]), None)
        except (OSError, ValueError):
            pass
        if enabled["llm"] and identity["digest"] is None:
            raise RuntimeError("cannot resolve Ollama model identity; no valid LLM comparison possible")
        conditions["ollama_identity"] = identity
        record = json.loads((recorder.session_dir / "run_record.json").read_text())
        key = compatibility_key(record, conditions)
        (recorder.session_dir / "protocol.json").write_text(
            json.dumps({**plan, "conditions": conditions, "compatibility_key": key},
                       ensure_ascii=False, indent=2) + "\n")
        print("采集开始；请按提示读口令。按 Ctrl+C 可提前停止。", flush=True)
        started = monotonic()
        asr_thread = Thread(target=consume, name="probe-capture-asr", daemon=True)
        asr_thread.start()
        prompt_index = 0
        next_resource = 0
        last_frame_ms = -1
        while not stop.is_set() and monotonic() - started < args.duration:
            elapsed = monotonic() - started
            if prompt_index < len(plan["prompts"]) and elapsed >= plan["prompts"][prompt_index]["at_seconds"]:
                print("请说：" + plan["prompts"][prompt_index]["text"], flush=True)
                prompt_index += 1
            if elapsed >= next_resource:
                resources.append({"elapsed_seconds": elapsed, **resource_sample()})
                next_resource = elapsed + 1
            if camera is not None:
                ok, frame = camera.read()
                if not ok:
                    counts["camera_read_failures"] += 1
                    if counts["camera_read_failures"] >= 5:
                        raise RuntimeError("five camera read failures")
                else:
                    stamp = max(last_frame_ms + 1, monotonic_ns() // 1_000_000)
                    last_frame_ms = stamp
                    recognizer.recognize_rgb(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB), stamp)
                    counts["camera_frames"] += 1
                    if args.preview:
                        cv2.imshow("Audio load probe", frame)
                        if cv2.waitKey(1) & 0xff == 27:
                            interrupted = True
                            break
            else:
                stop.wait(.02)
    except KeyboardInterrupt:
        interrupted = True
    except Exception as error:
        fail(error)
    finally:
        measured_elapsed = monotonic() - started if started is not None else 0
        if started is not None:
            resources.append({"elapsed_seconds": measured_elapsed, **resource_sample()})
        measured_mic = mic.metrics()
        with lock:
            measured_counts = dict(counts)
            measured_statuses = dict(statuses)
        stop.set()
        mic.stop()
        if dispatcher:
            dispatcher.close(timeout=2)
        if asr_thread:
            asr_thread.join(timeout=2)
        def cleanup(name, action):
            try:
                action()
            except Exception as error:
                fail(RuntimeError(f"{name} cleanup failed: {error}"))
        if feedback:
            cleanup("feedback", feedback.close)
        if camera is not None:
            cleanup("camera", camera.release)
            if args.preview:
                cleanup("preview", cv2.destroyAllWindows)
        if recognizer is not None:
            cleanup("recognizer", recognizer.close)
        feedback_metrics = feedback.metrics() if feedback and hasattr(feedback, "metrics") else {}
        measured_counts["feedback_worker_alive"] = feedback_metrics.get("worker_alive", False)
        measured_counts["asr_worker_alive"] = bool(asr_thread and asr_thread.is_alive())
        metrics = dispatcher.metrics() if dispatcher else {}
        result = finalize(args.stage, measured_elapsed, measured_mic, measured_counts, metrics,
                          list(errors), interrupted)
        result.update({"run_id": recorder.session_id, "compatibility_key": locals().get("key"),
                       "voice_statuses": measured_statuses, "resources": resources,
                       "counts_after_shutdown": dict(counts),
                       "shutdown_seconds": monotonic() - started - measured_elapsed if started else 0,
                       "note_shutdown": "In-flight execution may complete during shutdown; callback rates use acquisition window only."})
        recorder.update_summary({**measured_mic, "probe_status": result["status"]})
        recorder.close()
        session_summary = json.loads((recorder.session_dir / "summary.json").read_text())
        result["asr_mean_rtf"] = session_summary.get("mean_rtf")
        result["input_source"] = input_source
        (recorder.session_dir / "load_summary.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps({"status": result["status"], "unexercised": result["unexercised"],
                          "report": str(recorder.session_dir / "load_summary.json")}, ensure_ascii=False))
    if result["status"] == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
