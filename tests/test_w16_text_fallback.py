from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import run_assistant_demo
from edge_smart_home.app import cli


class TextFallbackTests(unittest.TestCase):
    def test_no_model_text_fallback_controls_mock_and_rejects_invalid_requests(self):
        output = io.StringIO()
        with patch.dict(os.environ, {'EDGE_SMART_HOME_BACKEND': 'mock'}), \
             patch.object(cli.AssistantOrchestrator, 'from_config_dir', side_effect=AssertionError('must not construct model provider')), \
             patch.object(cli.AssistantOrchestrator, 'preload_model', side_effect=AssertionError('must not preload model')), \
             patch('builtins.input', side_effect=['打开客厅灯', '把客厅空调温度设为31度', '打开书房灯', '退出']), \
             redirect_stdout(output):
            cli.main(['--no-model'])
        rows = [json.loads(line) for line in output.getvalue().splitlines() if line.startswith('{')]
        self.assertEqual([r['status'] for r in rows], ['executed', 'request_rejected', 'request_rejected'])
        self.assertEqual(rows[0]['result']['backend_kind'], 'mock')
        self.assertEqual(rows[0]['result']['completion_scope'], 'mock_state_snapshot')
        self.assertEqual(rows[0]['result']['attributes'], {'power': True, 'brightness': 100})
        self.assertEqual(rows[1]['error_code'], 'REQUEST_ARGUMENT_INVALID')
        self.assertEqual(rows[2]['error_code'], 'UNKNOWN_DEVICE')
        self.assertTrue(all(r['llm_calls'] == 0 for r in rows))

    def test_unified_existing_text_entry_forwards_explicit_no_model(self):
        argv = ['run_assistant_demo.py', '--backend', 'mock', 'text', '--no-model']
        with patch.object(sys, 'argv', argv), patch.dict(os.environ, {}, clear=False), \
             patch.object(run_assistant_demo, '_launch') as launch:
            run_assistant_demo.main()
        self.assertEqual(launch.call_args.args[1], ['edge-smart-home', '--no-model'])

    def test_default_text_entry_keeps_existing_provider_policy(self):
        args = run_assistant_demo._build_parser().parse_args(['text'])
        self.assertFalse(args.no_model)


if __name__ == '__main__':
    unittest.main()
