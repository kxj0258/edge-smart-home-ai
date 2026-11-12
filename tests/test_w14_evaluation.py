from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import struct
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import evaluate_w14_asr as evaluation
from edge_smart_home.audio.interfaces import StreamingRecognition
from edge_smart_home.device import MockDeviceBackend


class FakeAsr:
    def __init__(self, output="完全错误", error=None):
        self.output, self.error, self.calls = output, error, 0

    def transcribe_waveform(self, samples, rate, chunk_ms):
        self.calls += 1
        if self.error:
            raise self.error
        return StreamingRecognition(self.output, len(samples) / rate, .002)


@unittest.skipUnless(importlib.util.find_spec("numpy"), "optional voice dependency numpy is unavailable")
class W14EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.git = {"commit": "1" * 40, "status": ""}
        for name, value in (("git_info", self.git), ("source_hashes", {"scripts/evaluate_w14_asr.py": "2" * 64})):
            p = patch.object(evaluation, name, return_value=value); p.start(); self.addCleanup(p.stop)
        p = patch.object(evaluation, "dependency_identity", return_value={"unit_fixture": "no native import/inference"})
        p.start(); self.addCleanup(p.stop)

    def wav(self, name, value=1, channels=1, rate=16000, width=2):
        path = self.base / name
        with wave.open(str(path), "wb") as target:
            target.setnchannels(channels); target.setsampwidth(width); target.setframerate(rate)
            target.writeframes(struct.pack("<h", value) * 160)
        return path

    def manifest(self):
        cases = []
        for index in range(5):
            path = self.wav(f"{index}.wav", value=index + 1)
            cases.append({"id": f"H{index}", "wav": str(path), "sha256": evaluation.sha256(path),
                          "expected": "打开客厅灯", "source_kind": "human", "source_url": "https://example.org/fixed/audio",
                          "license": "CC-BY-4.0", "attribution": "fixture author", "provenance_note": "test fixture, not measured human data"})
        path = self.base / "manifest.json"
        path.write_text(json.dumps({"schema_version": 1, "kind": "w14_short_speech_manifest", "cases": cases}))
        return path

    def prepared(self):
        model = self.base / "model"; model.mkdir()
        for name in ("tokens.txt", "encoder.int8.onnx", "decoder.onnx", "joiner.int8.onnx"):
            (model / name).write_bytes(name.encode())
        args = SimpleNamespace(outdir=self.base / "prepared", manifest=self.manifest(), config_dir=ROOT / "configs",
                               model_dir=model, tokens=None, encoder=None, decoder=None, joiner=None, hotwords=None)
        evaluation.prepare(args)
        return args.outdir / "protocol.json"

    def executed(self, fake=None):
        protocol = self.prepared()
        out = self.base / "run"
        fake = fake or FakeAsr()
        result = evaluation.run(SimpleNamespace(protocol=protocol, outdir=out), asr_factory=lambda _: fake)
        return result, out, protocol, fake

    def test_manifest_requires_provenance_and_separates_origin(self):
        path = self.manifest(); value = evaluation.read_json(path)
        del value["cases"][0]["license"]
        with self.assertRaisesRegex(ValueError, "license"):
            evaluation.validate_manifest(value, path)
        value = evaluation.read_json(path)
        value["cases"][0]["source_kind"] = "mock"
        with self.assertRaisesRegex(ValueError, "Mock and unknown"):
            evaluation.validate_manifest(value, path)

    def test_manifest_rejects_duplicate_content_and_wrong_hash(self):
        path = self.manifest(); value = evaluation.read_json(path)
        value["cases"][1]["sha256"] = value["cases"][0]["sha256"]
        with self.assertRaisesRegex(ValueError, "unique"):
            evaluation.validate_manifest(value, path)
        value = evaluation.read_json(path); value["cases"][0]["sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            evaluation.validate_manifest(value, path)

    def test_pcm_conversion_preserves_signed_full_scale_and_rate(self):
        import numpy as np
        path = self.wav("edge.wav")
        with wave.open(str(path), "wb") as target:
            target.setnchannels(1); target.setsampwidth(2); target.setframerate(48000)
            target.writeframes(struct.pack("<hhh", -32768, 0, 32767))
        samples, rate, info = evaluation.read_pcm16_mono(path)
        self.assertEqual(rate, 48000)
        np.testing.assert_array_equal(samples, np.array([-1, 0, 32767 / 32768], dtype=np.float32))
        self.assertEqual(info["full_scale_sample_count"], 2)

    def test_pcm_rejects_stereo_and_truncated_payload(self):
        stereo = self.wav("stereo.wav", channels=2)
        with self.assertRaises(evaluation.PcmInputError) as caught:
            evaluation.read_pcm16_mono(stereo)
        self.assertEqual(caught.exception.code, "PCM_FORMAT_UNSUPPORTED")
        truncated = self.wav("truncated.wav")
        truncated.write_bytes(truncated.read_bytes()[:-2])
        with self.assertRaises(evaluation.PcmInputError) as caught:
            evaluation.read_pcm16_mono(truncated)
        self.assertEqual(caught.exception.code, "PCM_TRUNCATED")

    def test_resolver_reuses_guard_and_never_executes(self):
        configs = {name: evaluation.read_json(ROOT / "configs" / (name + ".json"))
                   for name in ("devices", "intents", "routing", "scenes", "safety")}
        with patch.object(MockDeviceBackend, "execute", side_effect=AssertionError("must not execute")):
            valid = evaluation.text_probe("打开客厅灯", configs)
            invalid = evaluation.text_probe("把客厅空调温度设置为31度", configs)
            missing = evaluation.text_probe("把客厅灯亮度调一下", configs)
        self.assertEqual(valid["status"], "resolved_unexecuted")
        self.assertEqual(valid["device_executions"], 0)
        self.assertEqual(invalid["error_code"], "REQUEST_ARGUMENT_INVALID")
        self.assertEqual(missing["error_code"], "REQUEST_ARGUMENT_MISSING")

    def test_wrong_transcripts_do_not_invent_accuracy_gate(self):
        summary, out, _, asr = self.executed()
        self.assertTrue(summary["passed"])
        self.assertEqual(asr.calls, 5)
        self.assertEqual(summary["overall"]["recognizer"]["exact_matches"], 0)
        self.assertEqual(summary["by_source_kind"]["human"]["completed"], 5)
        self.assertEqual(summary["by_source_kind"]["synthetic"]["recordings"], 0)
        self.assertEqual(summary["real_device_executions"], 0)
        self.assertIsNone(summary["live_overflow_count"])
        self.assertEqual(len((out / "ledger.jsonl").read_text().splitlines()), 10)

    def test_recognizer_and_voice_correction_are_separate(self):
        summary, out, _, _ = self.executed(FakeAsr(output="我我打开客厅灯"))
        row = evaluation.read_json(out / "records/0000.json")
        self.assertEqual(row["recognizer_output"], "我我打开客厅灯")
        self.assertEqual(row["voice_normalized"], "我打开客厅灯")
        self.assertNotEqual(row["metrics_recognizer"]["character_edits"], row["metrics_voice"]["character_edits"])

    def test_native_decode_failure_retained_and_fails_gates(self):
        summary, out, _, asr = self.executed(FakeAsr(error=RuntimeError("native decode fixture")))
        self.assertFalse(summary["passed"])
        self.assertEqual(asr.calls, 5)
        rows = [evaluation.read_json(path) for path in sorted((out / "records").glob("*.json"))]
        self.assertTrue(all(r["errors"][0]["code"] == "ASR_DECODE_FAILED" for r in rows))
        self.assertTrue(all(not r["completed"] for r in rows))

    def test_runtime_load_failure_preserves_failed_run(self):
        protocol = self.prepared(); out = self.base / "run"
        def fail(_):
            raise RuntimeError("load fixture")
        summary = evaluation.run(SimpleNamespace(protocol=protocol, outdir=out), asr_factory=fail)
        self.assertFalse(summary["passed"])
        self.assertEqual(summary["asr_calls"], 0)
        self.assertEqual(evaluation.read_json(out / "execution.json")["error"]["code"], "ASR_RUNTIME_FAILED")
        result = evaluation.report(SimpleNamespace(run=out, outdir=self.base / "rebuilt"))
        self.assertEqual(result, summary)

    def test_existing_output_refused_before_protocol_read_or_native_load(self):
        out = self.base / "exists"; out.mkdir()
        with patch.object(evaluation, "read_json", side_effect=AssertionError("must not read")), self.assertRaises(FileExistsError):
            evaluation.run(SimpleNamespace(protocol=self.base / "missing.json", outdir=out), asr_factory=lambda _: self.fail("must not load"))

    def test_protocol_input_and_source_mutation_rejected(self):
        protocol = self.prepared(); p = evaluation.read_json(protocol)
        Path(p["cases"][0]["audio_identity"]["path"]).write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "Frozen file differs"):
            evaluation.validate_protocol(p)
        with patch.object(evaluation, "source_hashes", return_value={}):
            with self.assertRaisesRegex(ValueError, "dependencies or.*sources"):
                evaluation.validate_protocol(p)

    def test_report_rebuild_is_identical_and_rejects_record_tamper(self):
        _, out, _, _ = self.executed()
        rebuilt = self.base / "rebuilt"
        with patch.object(evaluation, "dependency_identity", side_effect=AssertionError("offline report must not import native")):
            evaluation.report(SimpleNamespace(run=out, outdir=rebuilt))
        for name in ("summary.json", "report.md"):
            self.assertEqual((out / name).read_bytes(), (rebuilt / name).read_bytes())
        (out / "records/0000.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "Raw evidence differs"):
            evaluation.report(SimpleNamespace(run=out, outdir=self.base / "tamper"))

    def test_report_rejects_unindexed_extra_record(self):
        _, out, _, _ = self.executed()
        (out / "records/9999.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "exact.*set"):
            evaluation.report(SimpleNamespace(run=out, outdir=self.base / "extra"))

    def reindex(self, out):
        index = evaluation.read_json(out / "raw-index.json")
        for item in index["artifacts"]:
            item["bytes"] = (out / item["path"]).stat().st_size
            item["sha256"] = evaluation.sha256(out / item["path"])
        (out / "raw-index.json").write_text(json.dumps(index))

    def test_report_recomputes_metrics_and_checks_ledger_even_with_updated_index(self):
        _, out, _, _ = self.executed()
        path = out / "records/0000.json"; original = path.read_text()
        row = json.loads(original); row["metrics_recognizer"]["character_edits"] = 0
        path.write_text(json.dumps(row)); self.reindex(out)
        with self.assertRaisesRegex(ValueError, "metrics.*recomputation"):
            evaluation.report(SimpleNamespace(run=out, outdir=self.base / "wrong-metric"))
        path.write_text(original)
        ledger_path = out / "ledger.jsonl"
        ledger = [json.loads(line) for line in ledger_path.read_text().splitlines()]
        ledger[0]["id"] = "wrong-case"
        ledger_path.write_text("\n".join(json.dumps(row) for row in ledger) + "\n"); self.reindex(out)
        with self.assertRaisesRegex(ValueError, "Ledger order/ID"):
            evaluation.report(SimpleNamespace(run=out, outdir=self.base / "wrong-ledger"))

    def test_report_rejects_top_level_reference_drift(self):
        _, out, _, _ = self.executed()
        path = out / "records/0000.json"
        row = evaluation.read_json(path); row["expected"] = "被改过的参考"
        path.write_text(json.dumps(row)); self.reindex(out)
        with self.assertRaisesRegex(ValueError, "Recorded source"):
            evaluation.report(SimpleNamespace(run=out, outdir=self.base / "wrong-reference"))

    def test_missing_and_unknown_rows_cannot_satisfy_minimum(self):
        summary, out, protocol, _ = self.executed()
        p = evaluation.read_json(protocol)
        rows = [evaluation.read_json(path) for path in sorted((out / "records").glob("*.json"))]
        execution = evaluation.read_json(out / "execution.json")
        self.assertFalse(evaluation.aggregate(p, rows[:-1], execution)["passed"])
        for row in rows:
            row["source_kind"] = "unknown"
        self.assertFalse(evaluation.aggregate(p, rows, execution)["passed"])

    def test_percentile_uses_fixed_linear_interpolation(self):
        self.assertEqual(evaluation.percentile([1, 2, 3, 4, 5], .95), 4.8)
        self.assertIsNone(evaluation.percentile([], .5))


if __name__ == "__main__":
    unittest.main()
