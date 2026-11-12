from __future__ import annotations

from argparse import Namespace
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('evaluate_w15_devices', ROOT / 'scripts/evaluate_w15_devices.py')
W15 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(W15)
CONFIG = {n: W15.read(ROOT / 'configs' / (n + '.json')) for n in W15.CONFIG_NAMES}
CASES = W15.read(ROOT / 'configs/evaluation/w15-device-regressions-v1.json')


class W15EvaluationTests(unittest.TestCase):
    def test_fixed_thirty_four_authored_cases_and_paths(self):
        W15.validate_cases(CASES)
        self.assertEqual(len(CASES['cases']), 34)
        self.assertEqual({c['path'] for c in CASES['cases']}, {'direct', 'text', 'voice', 'scene'})

    def test_duplicate_oracle_and_unknown_path_are_rejected(self):
        for mutation in ('duplicate', 'path', 'missing_expected'):
            value = deepcopy(CASES)
            if mutation == 'duplicate':
                value['cases'][1]['id'] = value['cases'][0]['id']
            elif mutation == 'path':
                value['cases'][0]['path'] = 'real_ha'
            else:
                del value['cases'][0]['expect']['status']
            with self.assertRaises((ValueError, KeyError)):
                W15.validate_cases(value)

    def test_catalog_has_one_oracle_for_every_declared_capability(self):
        cases = W15.catalog_cases(CONFIG['devices'])
        self.assertEqual(len(cases), 103)
        self.assertEqual(len({c['id'] for c in cases}), 103)
        self.assertEqual({(c['command']['device_id'], c['command']['action']) for c in cases},
                         {(d, a) for d, v in CONFIG['devices'].items() for a in v['capabilities']})

    def test_catalog_enforces_privacy_and_pause_prerequisites(self):
        cases = W15.catalog_cases(CONFIG['devices'])
        for c in cases:
            action = c['command']['action']
            if action in ('start_recording', 'show_feed', 'pause'):
                self.assertEqual(c['setup'][0]['command']['action'], 'play' if action == 'pause' else 'disable_privacy')

    def test_scene_references_and_arguments_are_audited(self):
        self.assertEqual(W15.scene_audit(CONFIG), {'scenes': 9, 'commands': 46,
                                                  'all_references_and_parameters_valid': True, 'errors': []})
        changed = deepcopy(CONFIG)
        changed['scenes']['home']['commands'][0]['device_id'] = 'not-a-device'
        self.assertFalse(W15.scene_audit(changed)['all_references_and_parameters_valid'])

    def test_current_authored_mock_oracles(self):
        for case in CASES['cases']:
            with self.subTest(case=case['id']):
                row = W15.execute_case(case, CONFIG)
                self.assertTrue(row['passed'], row)

    def test_current_all_capability_examples(self):
        for case in W15.catalog_cases(CONFIG['devices']):
            with self.subTest(case=case['id']):
                row = W15.execute_case(case, CONFIG)
                self.assertTrue(row['passed'], row)

    def test_synthetic_voice_never_calls_asr_or_model(self):
        with patch('edge_smart_home.audio.sherpa_onnx_adapters._sherpa_onnx', side_effect=AssertionError('ASR forbidden')):
            for case in CASES['cases']:
                if case['path'] == 'voice':
                    row = W15.execute_case(case, CONFIG)
                    self.assertTrue(row['passed'], row)
                    self.assertEqual(row['input_kind'], 'synthetic_transcript')
                    self.assertEqual(row['llm_calls'], 0)

    def test_changed_readback_and_unrelated_state_are_detected(self):
        row = W15.execute_case(CASES['cases'][0], CONFIG)
        changed = deepcopy(row)
        changed['execution_records'][0]['readback']['attributes']['power'] = False
        self.assertFalse(W15.check_case(changed)['returned_matches_explicit_readback'])
        changed = deepcopy(row)
        changed['after']['bedroom_sensor']['attributes']['humidity'] = 0
        self.assertFalse(W15.check_case(changed)['unrelated_devices_unchanged'])

    def test_offline_query_is_failure_with_last_state_readback(self):
        row = W15.execute_case(CASES['cases'][15], CONFIG)
        self.assertEqual(row['error_code'], 'DEVICE_OFFLINE')
        self.assertEqual(row['before'], row['after'])
        self.assertFalse(row['after']['living_room_light']['available'])
        self.assertIsNone(row['execution_records'][0]['returned'])

    def make_run(self, directory):
        root = Path(directory)
        pdir, run = root / 'protocol', root / 'run'
        clean = {'commit': 'f' * 40, 'status': ''}
        frozen = {'scripts/evaluate_w15_devices.py': W15.sha(ROOT / 'scripts/evaluate_w15_devices.py')}
        with patch.object(W15, 'git', return_value=clean), patch.object(W15, 'sources', return_value=frozen):
            W15.prepare(Namespace(outdir=pdir, cases=ROOT / 'configs/evaluation/w15-device-regressions-v1.json', config_dir=ROOT / 'configs'))
            summary = W15.run(Namespace(outdir=run, protocol=pdir / 'protocol.json'))
        self.assertTrue(summary['passed'])
        return root, run

    def reindex(self, run):
        p = run / 'raw-index.json'
        d = W15.read(p)
        for item in d['artifacts']:
            file = run / item['path']
            item.update(bytes=file.stat().st_size, sha256=W15.sha(file))
        p.write_text(json.dumps(d))

    def test_prepare_run_report_are_reproducible_and_mock_scoped(self):
        with TemporaryDirectory() as tmp:
            root, run = self.make_run(tmp)
            result = W15.report(Namespace(outdir=root / 'recomputed', run=run))
            self.assertTrue(result['passed'])
            self.assertEqual(result['authored_passed'], 34)
            self.assertEqual(result['capability_passed'], 103)
            for key in ('real_ha_calls', 'physical_device_calls', 'asr_model_calls', 'ollama_calls', 'microphone_calls', 'network_attempts'):
                self.assertEqual(result[key], 0)
            for name in ('summary.json', 'report.md'):
                self.assertEqual((run / name).read_bytes(), (root / 'recomputed' / name).read_bytes())

    def test_existing_output_refused_before_input_or_device_reads(self):
        with TemporaryDirectory() as tmp:
            with patch.object(W15, 'read', side_effect=AssertionError('must reject before read')):
                with self.assertRaises(FileExistsError):
                    W15.run(Namespace(outdir=Path(tmp), protocol=Path('does-not-exist')))

    def test_prepare_rejects_dirty_source(self):
        with TemporaryDirectory() as tmp, patch.object(W15, 'git', return_value={'commit': 'f' * 40, 'status': ' M code'}):
            with self.assertRaisesRegex(ValueError, 'clean'):
                W15.prepare(Namespace(outdir=Path(tmp) / 'new', cases=Path('missing'), config_dir=Path('missing')))

    def test_report_rejects_raw_hash_mutation(self):
        with TemporaryDirectory() as tmp:
            root, run = self.make_run(tmp)
            (run / 'records/0000.json').write_text('{}')
            with self.assertRaisesRegex(ValueError, 'hash'):
                W15.report(Namespace(outdir=root / 'report', run=run))

    def test_report_rejects_false_checks_even_when_index_rehashed(self):
        with TemporaryDirectory() as tmp:
            root, run = self.make_run(tmp)
            path = run / 'records/0000.json'
            d = W15.read(path)
            d['checks']['status'] = False
            path.write_text(json.dumps(d))
            self.reindex(run)
            with self.assertRaisesRegex(ValueError, 'check'):
                W15.report(Namespace(outdir=root / 'report', run=run))

    def test_report_rejects_relabelled_source_path_even_when_index_rehashed(self):
        with TemporaryDirectory() as tmp:
            root, run = self.make_run(tmp)
            path = run / 'records/0000.json'
            d = W15.read(path)
            d['path'] = 'voice'
            d['input_kind'] = 'synthetic_transcript'
            path.write_text(json.dumps(d))
            self.reindex(run)
            with self.assertRaisesRegex(ValueError, 'case'):
                W15.report(Namespace(outdir=root / 'report', run=run))

    def test_report_rejects_reordered_ledger_even_when_index_rehashed(self):
        with TemporaryDirectory() as tmp:
            root, run = self.make_run(tmp)
            path = run / 'ledger.jsonl'
            path.write_text('\n'.join(reversed(path.read_text().splitlines())) + '\n')
            self.reindex(run)
            with self.assertRaisesRegex(ValueError, 'ledger'):
                W15.report(Namespace(outdir=root / 'report', run=run))


if __name__ == '__main__':
    unittest.main()
