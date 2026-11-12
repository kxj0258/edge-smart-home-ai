from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from io import StringIO
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import inspect_llm_backend as inspect
from edge_smart_home.agent.local_llm import LlmHttpError


DIGEST = "a" * 64
FIXED_CONFIG = {"enabled":True,"provider":"ollama","max_commands":8,
    "ollama":{"endpoint":"http://127.0.0.1:11434/api/generate","model":"qwen3:4b",
        "connect_timeout_seconds":2.0,"total_timeout_seconds":30.0,"max_output_tokens":256,
        "temperature":0.0,"seed":42,"think":False,"keep_alive":"10m"}}


def metadata():
    details = {"format": "gguf", "family": "qwen3", "parameter_size": "4.0B", "quantization_level": "Q4_K_M"}
    info = {"general.architecture": "qwen3", "qwen3.block_count": 36,
            "qwen3.embedding_length": 2560, "qwen3.attention.head_count": 32,
            "qwen3.attention.head_count_kv": 8, "qwen3.attention.key_length": 128,
            "qwen3.attention.value_length": 128, "qwen3.context_length": 262144}
    return {"version": {"version": "0.40.2"},
            "tags": {"models": [{"name": "qwen3:4b", "digest": DIGEST, "details": details}]},
            "show": {"details": details, "model_info": info, "thinking": {"values": [True], "default": True},
                     "template": "template", "license": "Apache-2.0", "parameters": "", "capabilities": ["completion", "thinking"]},
            "ps": {"models": []}}


class FakeMetadata:
    def __init__(self, value=None): self.value = value or metadata(); self.calls = []
    def get_json(self, path): self.calls.append(path); return deepcopy(self.value[path.split("/")[-1]])
    def show(self): self.calls.append("/api/show"); return deepcopy(self.value["show"])


class FakeTransport:
    def __init__(self, response=None, error=None):
        self.response = response if response is not None else {
            "response": '{"kind":"response","message":"我可以介绍本地能力。","commands":[]}',
            "thinking": "sample reasoning", "done": True, "done_reason": "stop", "eval_count": 20, "eval_duration": 2_000_000_000}
        self.response.setdefault("model", "qwen3:4b")
        self.error = error; self.calls = []
    def post_json(self, url, payload, **kwargs):
        self.calls.append((url, payload, kwargs))
        if self.error: raise self.error
        return deepcopy(self.response)


def setup_args(directory, *, smoke=False, config=None):
    root = Path(directory); configs = root / "configs"; configs.mkdir()
    (configs / "agent.json").write_text(json.dumps(FIXED_CONFIG))
    (configs / "devices.json").write_bytes((ROOT / "configs/devices.json").read_bytes())
    out = root / "out"; out.mkdir()
    return SimpleNamespace(config_dir=configs, config=config, outdir=out, smoke=smoke,
                           expected_digest=DIGEST, expected_version="0.40.2")


class IdentityTest(unittest.TestCase):
    def test_exact_dimensions_do_not_infer_kv_precision_from_weight_quantization(self):
        value = inspect.identity(metadata(), "qwen3:4b", DIGEST, "0.40.2")
        self.assertEqual(value["dimensions"]["key_head_dim"], 128)
        self.assertNotEqual(value["dimensions"]["key_head_dim"], 2560 // 32)
        self.assertIsNone(value["kv_cache_dtype"])
        self.assertEqual(value["details"]["quantization_level"], "Q4_K_M")
        self.assertFalse(inspect.thinking_support(metadata()["show"], False))

    def test_digest_tag_show_resident_and_version_mismatch_rejected(self):
        variants = []
        value = metadata(); value["tags"]["models"][0]["digest"] = "b" * 64; variants.append(value)
        value = metadata(); value["tags"]["models"].append(deepcopy(value["tags"]["models"][0])); variants.append(value)
        value = metadata(); value["show"]["details"] = {"format": "other"}; variants.append(value)
        value = metadata(); value["ps"]["models"] = [{"name": "qwen3:4b", "digest": "b" * 64}]; variants.append(value)
        value = metadata(); value["version"]["version"] = "other"; variants.append(value)
        value = metadata(); value["show"]["remote_host"] = "cloud.example"; variants.append(value)
        for value in variants:
            with self.assertRaises(ValueError): inspect.identity(value, "qwen3:4b", DIGEST, "0.40.2")

    def test_missing_explicit_head_dimension_cannot_be_filled_from_hidden_size(self):
        value = metadata(); del value["show"]["model_info"]["qwen3.attention.key_length"]
        with self.assertRaises(ValueError): inspect.identity(value, "qwen3:4b", DIGEST, None)

    def test_unknown_thinking_support_is_not_assumed_false(self):
        self.assertIsNone(inspect.thinking_support({}, False))
        self.assertFalse(inspect.thinking_support({"thinking":{"values":[0]}}, False))

    def test_real_tags_superset_of_show_details_is_supported_and_retained(self):
        value = metadata()
        shared = {**value["show"]["details"], "parent_model":"", "families":["qwen3"]}
        value["show"]["details"] = deepcopy(shared)
        value["tags"]["models"][0]["details"] = {**shared, "context_length":262144,
            "embedding_length":2560, "runner":"ggml"}
        result = inspect.identity(value,"qwen3:4b",DIGEST,"0.40.2")
        self.assertEqual(result["tag_details"]["runner"],"ggml")
        self.assertNotIn("runner",result["details"])
        self.assertEqual(result["dimensions"]["model_context_length"],262144)

    def test_core_shared_details_and_tag_dimension_disagreement_still_rejected(self):
        variants = []
        for key, changed in (("family","other"),("format",""),("parameter_size",None),("quantization_level","Q8_0"),
                             ("context_length",4096),("embedding_length",2561),("context_length",True)):
            value = metadata();value["tags"]["models"][0]["details"] = {**value["tags"]["models"][0]["details"],key:changed}
            variants.append(value)
        value=metadata();value["tags"]["models"][0]["details"]={**value["tags"]["models"][0]["details"],"families":["other"]}
        value["show"]["details"]={**value["show"]["details"],"families":["qwen3"]};variants.append(value)
        for value in variants:
            with self.assertRaises(ValueError):inspect.identity(value,"qwen3:4b",DIGEST,"0.40.2")


class CounterRatesTest(unittest.TestCase):
    def test_nanosecond_rates_and_prompt_cache_subtraction(self):
        value = {"done":True,"eval_count":20,"eval_duration":2_000_000_000,
                 "prompt_eval_count":1000,"prompt_eval_cached_count":800,"prompt_eval_duration":1_000_000_000}
        rates = inspect.server_rates(value)
        self.assertEqual(rates["server_generation_tps"],10)
        self.assertEqual(rates["server_uncached_prompt_tps"],200)
        self.assertIsNone(rates["client_ttft_ms"])

    def test_missing_cached_count_is_not_zero_and_nonstream_ttft_unmeasured(self):
        result = inspect.server_rates({"done":True,"prompt_eval_count":1000,"prompt_eval_duration":1_000_000_000})
        self.assertIsNone(result["server_uncached_prompt_tps"])
        self.assertFalse(result["prompt_cache_count_observed"])
        self.assertIn("NOT_MEASURED",result["client_ttft_status"])

    def test_missing_unfinished_zero_nan_bool_or_negative_counters_do_not_make_rates(self):
        for response in (None, {}, {"done":False,"eval_count":2,"eval_duration":1}):
            self.assertIsNone(inspect.server_rates(response)["server_generation_tps"])
        for bad in (0,-1,True,float("nan"),float("inf"),1.0,2**64):
            for key in ("eval_count","eval_duration"):
                value={"done":True,"eval_count":2,"eval_duration":1};value[key]=bad
                self.assertIsNone(inspect.server_rates(value)["server_generation_tps"])


class ExistingPlannerProbeTest(unittest.TestCase):
    def test_metadata_only_makes_no_generation_and_preserves_advisory_incompatibility(self):
        with TemporaryDirectory() as directory:
            args=setup_args(directory);delegate=FakeTransport();client=FakeMetadata()
            result=inspect.run(args,client,delegate)
            self.assertTrue(result["passed"]);self.assertFalse(result["configured_thinking_request_supported"])
            self.assertEqual(result["generation_calls"],0);self.assertEqual(delegate.calls,[])
            self.assertEqual(len(result["artifacts_sha256"]),8)

    def test_one_existing_planner_call_uses_product_options_and_never_executes(self):
        with TemporaryDirectory() as directory, patch.object(inspect.MockDeviceBackend,"execute",side_effect=AssertionError("must not execute")):
            args=setup_args(directory,smoke=True);delegate=FakeTransport()
            result=inspect.run(args,FakeMetadata(),delegate);call=delegate.calls[0]
            self.assertTrue(result["passed"]);self.assertEqual(len(delegate.calls),1)
            self.assertEqual(call[1]["options"],{"temperature":0.0,"num_predict":256,"seed":42})
            self.assertFalse(call[1]["stream"]);self.assertFalse(call[1]["think"])
            self.assertEqual(call[2],{"connect_timeout_seconds":2.0,"total_timeout_seconds":30.0})
            self.assertEqual(result["smoke"]["device_execution_calls"],0)
            self.assertTrue(result["smoke"]["observed_nonempty_thinking"])
            self.assertIsNone(result["smoke"]["rates"]["client_ttft_ms"])
            self.assertEqual(len(result["artifacts_sha256"]),9)

    def test_http400_keeps_error_and_elapsed_but_does_not_forge_ttft_or_success(self):
        with TemporaryDirectory() as directory:
            args=setup_args(directory,smoke=True)
            result=inspect.run(args,FakeMetadata(),FakeTransport(error=LlmHttpError(400)))
            raw=json.loads((args.outdir/"generation.json").read_text())
            self.assertFalse(result["passed"]);self.assertEqual(result["smoke"]["planner_kind"],"unavailable")
            self.assertEqual(raw["error"]["http_status"],400);self.assertGreaterEqual(raw["client_final_wall_ms"],0)
            self.assertIsNone(result["smoke"]["rates"]["server_generation_tps"])

    def test_empty_invalid_or_length_truncated_response_is_preserved_as_failure(self):
        for response in ({"done":True,"response":""},{"done":True,"response":None},
                         {"done":True,"response":"not json"},
                         {"done":True,"done_reason":"length","response":'{"kind":"response","message":"说明","commands":[]}'}):
            with TemporaryDirectory() as directory:
                args=setup_args(directory,smoke=True)
                result=inspect.run(args,FakeMetadata(),FakeTransport(response=response))
                self.assertFalse(result["passed"]);self.assertTrue((args.outdir/"generation.json").is_file())

    def test_explicit_candidate_config_does_not_rewrite_product_or_override_other_options(self):
        with TemporaryDirectory() as directory:
            args=setup_args(directory,smoke=True);product=(args.config_dir/"agent.json").read_bytes()
            candidate=json.loads(product);candidate["ollama"]["think"]=True
            args.config=Path(directory)/"candidate.json";args.config.write_text(json.dumps(candidate))
            delegate=FakeTransport();result=inspect.run(args,FakeMetadata(),delegate)
            self.assertTrue(delegate.calls[0][1]["think"]);self.assertFalse(result["uses_default_product_config"])
            self.assertEqual((args.config_dir/"agent.json").read_bytes(),product)
            self.assertEqual(delegate.calls[0][1]["options"]["num_predict"],256)

    def test_config_drift_during_observation_rejects_success(self):
        with TemporaryDirectory() as directory:
            args=setup_args(directory);client=FakeMetadata();original=client.get_json
            def mutate(path):
                value=original(path)
                if client.calls.count("/api/ps")==2:(args.config_dir/"agent.json").write_text("{}")
                return value
            client.get_json=mutate
            with self.assertRaisesRegex(ValueError,"source changed"):inspect.run(args,client)

    def test_completed_response_from_another_model_cannot_pass_identity(self):
        with TemporaryDirectory() as directory:
            args=setup_args(directory,smoke=True);delegate=FakeTransport();delegate.response["model"]="other:4b"
            with self.assertRaisesRegex(ValueError,"response model differs"):inspect.run(args,FakeMetadata(),delegate)
            self.assertTrue((args.outdir/"generation.json").is_file())


class ConfigAndCliTest(unittest.TestCase):
    def test_remote_endpoint_nonfinite_timeout_and_unload_keepalive_rejected(self):
        changes=[("endpoint","https://example.com/api/generate"),("endpoint","http://user@127.0.0.1/api/generate"),
                 ("total_timeout_seconds",float("nan")),("max_output_tokens",True),
                 ("keep_alive",0),("keep_alive",False),("keep_alive","0"),("keep_alive","-1"),("keep_alive","")]
        for key,value in changes:
            with TemporaryDirectory() as directory:
                args=setup_args(directory);path=args.config_dir/"agent.json";config=json.loads(path.read_text())
                config["ollama"][key]=value;path.write_text(json.dumps(config))
                with self.assertRaises(ValueError):inspect.load_config(args.config_dir)

    def test_smoke_requires_digest_before_creating_any_output(self):
        with TemporaryDirectory() as directory, redirect_stderr(StringIO()):
            out=Path(directory)/"new"
            with self.assertRaises(SystemExit):inspect.main(["--smoke","--outdir",str(out)])
            self.assertFalse(out.exists())

    def test_cli_failure_exit_and_existing_output_preservation(self):
        with TemporaryDirectory() as directory, redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            out=Path(directory)/"new"
            with patch.object(inspect,"run",return_value={"passed":False,"generation_calls":1}):
                self.assertEqual(inspect.main(["--outdir",str(out)]),1)
            saved=(out/"report.json").read_bytes()
            with self.assertRaises(SystemExit):inspect.main(["--outdir",str(out)])
            self.assertEqual((out/"report.json").read_bytes(),saved)

    def test_cli_identity_error_retains_diagnostic_without_success(self):
        with TemporaryDirectory() as directory, redirect_stderr(StringIO()):
            out=Path(directory)/"new"
            with patch.object(inspect,"run",side_effect=ValueError("model digest mismatch")):
                self.assertEqual(inspect.main(["--outdir",str(out)]),2)
            self.assertTrue((out/"error.json").is_file());self.assertFalse((out/"report.json").exists())


if __name__=="__main__":unittest.main()
