from contextlib import ExitStack
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from regen.tools import controlled_proof_experiment as control


class ControlledProtocolTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.output = self.root/'workflow'
        self.evidence = self.root/'evidence'
        self.evidence.mkdir()
        self.application = Path(control.__file__).resolve().parents[2]
        self.source_path = 'regen/tools/green_contract_ab.py'
        self.source_sha = control.watch.sha(self.application/self.source_path)
        self.manifest = self.write('manifest.json', {'files': {self.source_path: {'sha256': self.source_sha}}})
        offline_fixture = self.write('offline-fixture.json', {'application': {self.source_path: self.source_sha}})
        def case(name, target):
            checkpoint = self.write(name+'-checkpoint.json', {'source_run': '/source/'+name})
            fixture = self.write(name+'-fixture/manifest.json', {'fixture': name})
            return {'name': name, 'theory': 'T', 'target_contract_id': target,
                    'checkpoint': str(checkpoint), 'checkpoint_sha256': control.watch.sha(checkpoint),
                    'prepared_fixture': control.bound_file(fixture)}
        self.config = {'pilot_case': case('choose', 'contract:R:chooseThread_corres:123'),
                       'formal_cases': [case('delete', 'contract:delete'), case('insert', 'contract:insert')],
                       'variant_strategy': 'bounded-audited-prefix-v1', 'budget_effective_seconds': 3600,
                       'wall_limit_seconds': 10800, 'blocking_units': ['old-experiment.service'],
                       'offline_inputs': [control.bound_file(self.write('trace.json', {'trace': [45, 46]})),
                                          control.bound_file(offline_fixture)]}
        self.config_path = self.write('config.json', self.config)
        self.plan = control.freeze_plan(self.config_path, self.manifest, self.application, self.output)
        for stage in control.GATES:
            self.gate_evidence(stage)

    def write(self, name, value):
        path = self.root/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
        return path

    def gate_evidence(self, stage):
        artifacts = []
        for kind in control.GATES[stage]['artifacts']:
            payload = {'schema_version': 1, 'status': 'passed', 'checks': {'tested': True},
                'tests': {'modules': ['test_prepared_checkpoint'], 'run': 5, 'failures': 0, 'errors': 0},
                'source_sha256': {self.source_path: self.source_sha}, 'model_lifecycle_policy': 2,
                'production_and_target_clock_policy_tests': {'policies': ['no_new_contract', 'fixed_budget'],
                    'progress_array_unchanged': True, 'unknown_silence_charged': True,
                    'missing_model_lifecycle_refuses_budget_conclusion': True,
                    'candidate_audit_charged_full_seal_excluded': True}}
            if kind == 'mechanism_replay':
                payload = {'schema': 'controlled-native-replay-v1', 'pass': True, 'checks': {'tested': True},
                    'model_calls': 0, 'production_acceptance_writes': 0, 'source_unchanged': True,
                    'native_cleanup_verified': True, 'order': ['A', 'B', 'B', 'A'], 'initial_prefix_facts': 37,
                    'fixture_manifest_sha256': self.config['offline_inputs'][1]['sha256'],
                    'arms': [{'pass': True, 'original_costs_preserved': True, 'journal_replay_verified': True,
                              'cold_recovery_verified': True, 'requests': [{'real': True}]} for _ in range(4)]}
            raw = self.write(kind+'.json', payload)
            artifacts.append({**control.bound_file(raw), 'kind': kind,
                              'assertions': {'checks.tested': True}})
        value = {'schema': 'controlled-proof-gate-evidence-v1', 'stage': stage,
                 'plan_id': self.plan['plan_id'], 'binding': self.plan['binding'], 'status': 'passed',
                 'checks': {k: True for k in control.GATES[stage]['checks']}, 'artifacts': artifacts}
        self.write('evidence/'+stage+'.json', value)
        return value

    def pilot(self, *, success=True, valid=True):
        self.write('workflow/pilot/report.json', {'schema': 'controlled-proof-stage-report-v1',
            'stage': 'pilot', 'binding': self.plan['binding'], 'plan_id': self.plan['plan_id'], 'stage_complete': True,
            'arms': [{'name': 'choose--pair1--baseline', 'strategy': 'baseline',
                      'report': {'window_result_verified': valid, 'target_success': success}},
                     {'name': 'choose--pair1--variant', 'strategy': 'bounded-audited-prefix-v1',
                      'report': {'window_result_verified': valid}}]})

    def authorize(self):
        self.write('evidence/formal-authorization.json', {
            'schema': 'controlled-proof-formal-authorization-v1', 'plan_id': self.plan['plan_id'],
            'binding': self.plan['binding'], 'max_arms': 8, 'max_effective_arm_seconds': 28800,
            'automatic_replacements': 0, 'authorized': True})

    def test_plan_caps_and_balanced_order(self):
        pilot = [r for r in self.plan['arms'] if r['stage'] == 'pilot']
        formal = [r for r in self.plan['arms'] if r['stage'] == 'formal']
        self.assertEqual(len(pilot), 2)
        self.assertEqual(sum(r['budget_effective_seconds'] for r in formal), 28800)
        self.assertEqual([r['strategy'] for r in formal[:4]],
                         ['baseline', 'bounded-audited-prefix-v1', 'bounded-audited-prefix-v1', 'baseline'])
        self.assertEqual(self.plan['policies']['production'], 'no_new_contract')
        self.assertEqual(self.plan['policies']['experiment'], 'fixed_budget')

    def test_budget_expansion_rejected(self):
        self.config['budget_effective_seconds'] = 7200
        self.write('config.json', self.config)
        with self.assertRaisesRegex(ValueError, 'fixed at 3600'):
            control.freeze_plan(self.config_path, self.manifest, self.application, self.root/'other')

    def test_pilot_gate_accepts_only_bound_evidence(self):
        result = control.gates(self.output/'plan.json', self.evidence, 'pilot')
        self.assertTrue(result['ready'])
        value = self.gate_evidence('clock_recovery')
        value['binding']['config_sha256'] = 'different'
        self.write('evidence/clock_recovery.json', value)
        with self.assertRaises(ValueError):
            control.gates(self.output/'plan.json', self.evidence, 'pilot')

    def test_bare_passed_flag_cannot_unlock_gate(self):
        self.write('evidence/clock_recovery.json', {'passed': True})
        with self.assertRaises(ValueError):
            control.gates(self.output/'plan.json', self.evidence, 'pilot')

    def test_tampered_raw_evidence_rejected(self):
        self.write('clock_validation.json', {'checks': {'tested': False}})
        with self.assertRaisesRegex(ValueError, 'bound file changed'):
            control.gates(self.output/'plan.json', self.evidence, 'pilot')

    def test_unrelated_raw_assertions_rejected(self):
        value = self.gate_evidence('clock_recovery')
        value['artifacts'][0]['assertions'] = {}
        self.write('evidence/clock_recovery.json', value)
        with self.assertRaisesRegex(ValueError, 'inspectable assertions'):
                control.gates(self.output/'plan.json', self.evidence, 'pilot')

    def test_unrelated_passing_tests_cannot_unlock_clock(self):
        value = self.gate_evidence('clock_recovery')
        raw = self.write('clock_validation.json', {'checks': {'tested': True}, 'passed': True})
        value['artifacts'][0].update(control.bound_file(raw))
        self.write('evidence/clock_recovery.json', value)
        with self.assertRaisesRegex(ValueError, 'source-bound executed test'):
            control.gates(self.output/'plan.json', self.evidence, 'pilot')

    def test_missing_mechanism_check_rejected(self):
        value = self.gate_evidence('mechanism_replay')
        value['checks']['mechanism_improved'] = False
        self.write('evidence/mechanism_replay.json', value)
        with self.assertRaisesRegex(ValueError, 'required verified checks'):
            control.gates(self.output/'plan.json', self.evidence, 'pilot')

    def test_offline_input_mutation_invalidates_plan(self):
        self.write('trace.json', {'trace': [67]})
        with self.assertRaisesRegex(ValueError, 'bound file changed'):
            control.load_plan(self.output/'plan.json')

    def test_formal_requires_successful_pilot_and_authorization(self):
        self.pilot(success=False)
        self.authorize()
        with self.assertRaisesRegex(ValueError, 'positive-control'):
            control.gates(self.output/'plan.json', self.evidence, 'formal')
        self.pilot()
        self.assertTrue(control.gates(self.output/'plan.json', self.evidence, 'formal')['ready'])

    def test_formal_untrusted_pilot_not_counted_as_proof_failure_or_pass(self):
        self.pilot(valid=False)
        self.authorize()
        with self.assertRaisesRegex(ValueError, 'positive-control'):
            control.gates(self.output/'plan.json', self.evidence, 'formal')

    def test_resource_gate_never_stops_prior_unit(self):
        result = SimpleNamespace(returncode=0, stdout='LoadState=loaded\nActiveState=active\nSubState=running\nMainPID=100\n')
        with patch.object(control.subprocess, 'run', return_value=result) as run:
            with self.assertRaisesRegex(RuntimeError, 'previous experiment remains active'):
                control.resource_gate(self.plan, self.root)
        self.assertEqual(run.call_count, 1)
        self.assertNotIn('stop', run.call_args.args[0])

    def test_collected_unit_nonzero_not_found_is_finished_but_query_errors_fail_closed(self):
        absent = SimpleNamespace(returncode=1, stdout='LoadState=not-found\n', stderr='Unit not found')
        no_containers = SimpleNamespace(returncode=0, stdout='')
        with patch.object(control.subprocess, 'run', side_effect=[absent, no_containers]) as run, \
             patch.object(control.shutil, 'disk_usage', return_value=SimpleNamespace(free=100*1024**3)), \
             patch.object(Path, 'read_text', return_value='MemAvailable: 30000000 kB\n'):
            self.assertEqual(control.resource_gate(self.plan, self.root)['running_containers'], [])
        self.assertEqual(run.call_count, 2)
        for error in ('Failed to connect to bus: Permission denied', 'Transport endpoint is not connected'):
            with self.subTest(error=error), patch.object(control.subprocess, 'run', return_value=SimpleNamespace(
                    returncode=1, stdout='', stderr=error)) as run:
                with self.assertRaisesRegex(RuntimeError, 'cannot verify previous experiment service'):
                    control.resource_gate(self.plan, self.root)
                self.assertEqual(run.call_count, 1)

    def test_online_single_use_and_preserved_failure(self):
        with ExitStack() as stack:
            stack.enter_context(patch.object(control, 'resource_gate', return_value={}))
            stack.enter_context(patch.object(control, 'clone_case', side_effect=RuntimeError('recovery failed')))
            with self.assertRaisesRegex(RuntimeError, 'recovery failed'):
                control.run_stage(self.output/'plan.json', self.evidence, 'pilot')
            report = json.loads((self.output/'pilot/report.json').read_text())
            self.assertEqual(report['outcome_class'], 'infrastructure_error')
            self.assertFalse(report['stage_complete'])
            with self.assertRaises(FileExistsError):
                control.run_stage(self.output/'plan.json', self.evidence, 'pilot')

    def test_clock_invalidity_is_safety_pause_and_keeps_evidence(self):
        def clone(case, root, manifest):
            root.mkdir(parents=True)
            (root/'checkpoint.json').write_text('{}')
        with ExitStack() as stack:
            stack.enter_context(patch.object(control, 'resource_gate', return_value={}))
            stack.enter_context(patch.object(control, 'clone_case', side_effect=clone))
            stack.enter_context(patch.object(control.runner.closure, 'accepted_ids', return_value=set()))
            start = stack.enter_context(patch.object(control.runner.service, 'start'))
            stack.enter_context(patch.object(control.runner, 'observe',
                side_effect=control.runner.clock.ClockEvidenceError('untrusted model interval')))
            with self.assertRaises(control.runner.clock.ClockEvidenceError):
                control.run_stage(self.output/'plan.json', self.evidence, 'pilot')
        self.assertEqual(start.call_count, 1)
        report = json.loads((self.output/'pilot/report.json').read_text())
        self.assertEqual(report['outcome_class'], 'measurement_invalid')
        self.assertEqual(report['phase'], 'safety_paused')

    def test_successful_pilot_unlocks_exact_eight_arm_formal_once(self):
        def clone(case, root, manifest):
            root.mkdir(parents=True)
            (root/'checkpoint.json').write_text('{}')
        with ExitStack() as stack:
            stack.enter_context(patch.object(control, 'resource_gate', return_value={}))
            stack.enter_context(patch.object(control, 'clone_case', side_effect=clone))
            stack.enter_context(patch.object(control.runner.closure, 'accepted_ids', return_value=set()))
            start = stack.enter_context(patch.object(control.runner.service, 'start'))
            observer = stack.enter_context(patch.object(control.runner, 'observe',
                side_effect=lambda *a, **k: {'window_result_verified': True, 'target_success': True,
                                           'reason': 'target_contract_green', 'cleanup_errors': []}))
            pilot = control.run_stage(self.output/'plan.json', self.evidence, 'pilot')
            self.assertTrue(pilot['stage_complete'])
            self.assertEqual(start.call_count, 2)
            self.authorize()
            formal = control.run_stage(self.output/'plan.json', self.evidence, 'formal')
            self.assertTrue(formal['stage_complete'])
            self.assertEqual(len(formal['arms']), 8)
            self.assertEqual(start.call_count, 10)
            self.assertTrue(all(c.kwargs['budget'] == 3600 for c in observer.call_args_list))
            with self.assertRaises(FileExistsError):
                control.run_stage(self.output/'plan.json', self.evidence, 'formal')

    def test_clone_rejects_fixture_from_another_checkpoint_before_copy(self):
        case = self.config['pilot_case']
        path = Path(case['prepared_fixture']['path'])
        path.write_text(json.dumps({'checkpoint_sha256': 'other'}))
        case['prepared_fixture'] = control.bound_file(path)
        with patch('regen.tools.prepared_checkpoint.derive') as derive:
            with self.assertRaisesRegex(ValueError, 'does not bind case checkpoint'):
                control.clone_case(case, self.root/'destination', self.manifest)
        derive.assert_not_called()


if __name__ == '__main__':
    unittest.main()
