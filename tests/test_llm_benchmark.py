from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import json
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import benchmark_llm as bench
from edge_smart_home.common.llm_resources import summarize_memory


def metadata():
    details = {"format": "gguf", "family": "qwen3", "parameter_size": "4.0B", "quantization_level": "Q4_K_M"}
    dims = {"block_count": 36, "embedding_length": 2560, "attention.head_count": 32,
            "attention.head_count_kv": 8, "attention.key_length": 128, "attention.value_length": 128,
            "context_length": 262144}
    return {"version": {"version": bench.SERVER_VERSION},
            "tags": {"models": [{"name": "qwen3:4b", "digest": bench.MODEL_DIGEST, "details": details}]},
            "show": {"details": details, "model_info": {"general.architecture": "qwen3", **{"qwen3."+k: v for k, v in dims.items()}},
                     "thinking": {"values": [True], "default": True}, "template": "fixed", "license": "Apache-2.0"},
            "ps": {"models": []}}


class FakeClient:
    def __init__(self): self.data = metadata(); self.context = None; self.calls = []
    def ps(self):
        return {"models": [] if self.context is None else [{"name": "qwen3:4b", "digest": bench.MODEL_DIGEST,
                  "context_length": self.context, "size": 4096, "size_vram": 4096}]}
    def get_json(self, path):
        self.calls.append(path)
        return deepcopy(self.ps() if path == "/api/ps" else self.data[path.split("/")[-1]])
    def show(self): self.calls.append("/api/show"); return deepcopy(self.data["show"])


class FakeMemory:
    def __init__(self, **kwargs): pass
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def result(self):
        processes = [{"pid": 1, "ppid": 0, "starttime_ticks": 1, "argv": ["ollama", "serve"], "role": "server"},
                     {"pid": 2, "ppid": 1, "starttime_ticks": 2, "argv": ["runner"], "role": "runner"}]
        samples = [{"monotonic_ns": n, "sample_duration_ns": 1, "sum_rss_bytes": 1024 + n, "sum_pss_bytes": None,
                    "processes": deepcopy(processes), "process_tree_discovery_complete": True}
                   for n in (1, 500_000_000, 10_000_000_000)]
        return {"samples": samples, "summary": summarize_memory(samples)}


class FakeStream:
    def __init__(self, client): self.client = client; self.calls = []
    def __call__(self, endpoint, payload, **kwargs):
        self.calls.append(deepcopy(payload)); self.client.context = payload["options"]["num_ctx"]
        text = '{"kind":"response","message":"我可以介绍本地能力。","commands":[]}'
        final = {"model": "qwen3:4b", "response": "", "done": True, "done_reason": "stop", "prompt_eval_count": 3752,
                 "prompt_eval_cached_count": 3751, "prompt_eval_duration": 10_000_000, "load_duration": 1000,
                 "eval_count": 48, "eval_duration": 600_000_000}
        t0 = 1_000_000; first = 10 + len(self.calls)
        events = []
        for i, (elapsed, value) in enumerate(((first, {"model": "qwen3:4b", "response": text, "done": False}), (first+600, final))):
            stamp = t0 + elapsed*1_000_000
            events.append({"index": i, "received_ns": stamp-1, "parsed_ns": stamp, "elapsed_ms": float(elapsed),
                           "raw_line": json.dumps(value), "event": value})
        return {"passed": True, "request": {"payload": deepcopy(payload)}, "t0_ns": t0,
                "finish_ns": t0+(first+601)*1_000_000, "events": events, "response_text": text, "thinking_text": "",
                "final": final, "timings": {"first_event_ms": float(first), "first_response_ms": float(first),
                    "first_thinking_ms": None, "done_ms": float(first+600), "finish_ms": float(first+601)}}


class BenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(); self.addCleanup(self.temp.cleanup); self.root = Path(self.temp.name)
        self.sources = patch.object(bench, "source_hashes", return_value={"fixed": "abc"}); self.sources.start(); self.addCleanup(self.sources.stop)
        self.git = patch.object(bench, "git_info", return_value={"commit": "a"*40, "status": ""}); self.git.start(); self.addCleanup(self.git.stop)
        self.client = FakeClient(); self.stream = FakeStream(self.client)
        args = SimpleNamespace(config_dir=ROOT/"configs", think="false", num_thread=4, num_predict=None,
            contexts=[4096,8192], rounds=3, samples_per_context=20, warmup=2, timeout=120., memory_interval=.05,
            references=None, thinking_strategy="Product false request; no internal disabling guarantee", outdir=self.root/"prepare")
        args.outdir.mkdir(); bench.prepare(args, client=self.client); self.draft = args.outdir/"draft.json"
        self.p = bench.read_json(self.draft)

    def execute(self, name="pilot", streamer=None):
        out = self.root/name; out.mkdir()
        result = bench.execute(SimpleNamespace(command="pilot", protocol=self.draft, outdir=out, pilot_samples=1),
            client=self.client, streamer=streamer or self.stream, sampler_factory=FakeMemory)
        return out, result

    def reindex(self, directory):
        index = bench.read_json(directory/"raw-index.json")
        for name in index["sha256"]: index["sha256"][name] = bench.sha256(directory/name)
        (directory/"raw-index.json").write_text(json.dumps(index))

    def test_prepare_is_metadata_only_and_preserves_product_prompt(self):
        self.assertEqual(self.stream.calls, []); self.assertEqual(self.p["generation_calls"], 0)
        _, original = bench.base_payload(ROOT/"configs")
        self.assertEqual(self.p["base_payload"]["prompt"], original["prompt"])
        self.assertEqual(self.p["base_payload"]["options"]["num_thread"], 4)
        self.assertFalse(self.p["base_payload"]["think"])

    def test_formal_schedule_alternates_and_excludes_warmups(self):
        rows = bench.schedule(self.p)
        self.assertEqual(len(rows), 132)
        self.assertEqual(sum(r["phase"]=="sample" for r in rows),120)
        self.assertEqual([rows[i]["context"] for i in (0,22,44,66,88,110)],[4096,8192,8192,4096,4096,8192])
        left = bench.request_payload(self.p, rows[0]); right = bench.request_payload(self.p, rows[22])
        left["options"].pop("num_ctx"); right["options"].pop("num_ctx"); self.assertEqual(left,right)

    def test_quantiles_use_raw_samples_and_null_is_unavailable(self):
        self.assertEqual(bench.quantiles(range(20))["p95"],18.05)
        self.assertEqual(bench.quantiles([None,None])["n"],0)
        with self.assertRaises(ValueError): bench.quantiles([float("nan")])

    def test_protocol_rejects_undersampling_or_mutated_payload(self):
        for key, value in (("rounds",2),("samples_per_context_per_round",19),("connect_timeout_seconds",31)):
            p=deepcopy(self.p); p[key]=value
            with self.assertRaises(ValueError): bench.validate_protocol(p)
        p=deepcopy(self.p); p["base_payload"]["prompt"] += "changed"
        with self.assertRaises(ValueError): bench.validate_protocol(p)

    def test_pilot_raw_rebuild_and_freeze_are_identical(self):
        out, result = self.execute(); self.assertTrue(result["passed"]); self.assertEqual(len(self.stream.calls),6)
        self.assertEqual(bench.rebuild(out),bench.read_json(out/"summary.json"))
        summary=bench.read_json(out/"summary.json"); self.assertEqual(summary["measured_samples"],2)
        self.assertEqual(summary["warmups_excluded"],4)
        frozen=self.root/"frozen.json"
        bench.freeze(SimpleNamespace(draft=self.draft,pilot_dir=out,out=frozen))
        self.assertEqual(bench.read_json(frozen)["state"],"frozen")

    def test_full_formal_matrix_pools_sixty_samples_per_context(self):
        out,_=self.execute(); frozen=self.root/"frozen.json"
        bench.freeze(SimpleNamespace(draft=self.draft,pilot_dir=out,out=frozen))
        target=self.root/"formal"; target.mkdir()
        result=bench.execute(SimpleNamespace(command="run",protocol=frozen,outdir=target),
            client=self.client,streamer=self.stream,sampler_factory=FakeMemory)
        self.assertTrue(result["passed"])
        summary=bench.rebuild(target)
        for group in summary["groups"].values():
            self.assertEqual(group["pooled"]["client_visible_ttft_ms"]["n"],60)
            self.assertEqual(group["pooled"]["incomplete_rss_samples"],0)
            self.assertGreater(group["pooled"]["incomplete_pss_samples"],0)

    def test_failure_preserves_partial_raw_and_never_retries(self):
        def failure(*args,**kwargs):
            error=RuntimeError("bounded stream failure"); error.result={"passed":False,"events":[{"raw_line":"partial"}]}; raise error
        out,result=self.execute(streamer=failure)
        self.assertFalse(result["passed"])
        records=list((out/"records").glob("*.json")); self.assertEqual(len(records),1)
        self.assertEqual(bench.read_json(records[0])["stream"]["events"][0]["raw_line"],"partial")
        self.assertEqual(len((out/"calls.jsonl").read_text().splitlines()),2)

    def test_rebuild_rejects_raw_changes_even_before_validation(self):
        out,_=self.execute(); file=out/"records/00000.json"; file.write_text("{}")
        with self.assertRaisesRegex(ValueError,"artifact"): bench.rebuild(out)

    def test_rebuild_rejects_schedule_change_with_recomputed_index(self):
        out,_=self.execute(); file=out/"schedule.json"; rows=bench.read_json(file); rows.pop(); file.write_text(json.dumps(rows)); self.reindex(out)
        with self.assertRaisesRegex(ValueError,"sampling plan"): bench.rebuild(out)

    def test_rebuild_rejects_duplicate_request_indices(self):
        out,_=self.execute(); file=out/"records/00001.json"; row=bench.read_json(file); row["call_index"]=0; file.write_text(json.dumps(row)); self.reindex(out)
        with self.assertRaisesRegex(ValueError,"indices"): bench.rebuild(out)

    def test_rebuild_rejects_counter_context_and_ttft_drift(self):
        for field in ("counter","context","ttft","thinking","memory"):
            out,_=self.execute(name=field); file=out/"records/00002.json"; row=bench.read_json(file)
            if field=="counter": row["stream"]["final"]["prompt_eval_count"]=3751
            elif field=="context": row["ps_after"]["models"][0]["context_length"]=8192
            elif field=="ttft": row["stream"]["timings"]["first_response_ms"]+=1
            elif field=="thinking": row["stream"]["thinking_text"]="observed thought"
            else: row["memory"]["samples"][0]["process_tree_discovery_complete"]=False
            file.write_text(json.dumps(row)); self.reindex(out)
            with self.assertRaises(ValueError,msg=field): bench.rebuild(out)

    def test_formal_refuses_draft_and_dirty_source_before_generation(self):
        target=self.root/"bad"; target.mkdir()
        with self.assertRaisesRegex(ValueError,"frozen"):
            bench.execute(SimpleNamespace(command="run",protocol=self.draft,outdir=target),client=self.client,streamer=self.stream,sampler_factory=FakeMemory)
        self.assertEqual(self.stream.calls,[])
        with patch.object(bench,"source_hashes",return_value={"fixed":"changed"}):
            with self.assertRaisesRegex(ValueError,"sources changed"):
                self.execute(name="source-change")
        self.assertEqual(self.stream.calls,[])


if __name__ == "__main__": unittest.main()
