import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from regen.execution.proof_commits import digest
from regen.tools import contract_closure_continuation as continuation
from regen.tools.proof_idle_watch import sha


class ContractClosureContinuationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.control = self.root / 'control'
        self.arms = self.control / 'arms'
        self.baseline = self.arms / 'baseline'
        self.pending = self.arms / 'helper-closure-v1'
        self.worker = self.root / 'original-worker'
        self.manifest = self.root / 'original-manifest.json'
        self.supervisor_manifest = self.root / 'supervisor-manifest.json'
        self.checkpoint = self.root / 'checkpoint.json'
        self.output = self.root / 'continuation'
        self.write(self.manifest, {'release': 'original'})
        self.write(self.supervisor_manifest, {'release': 'repaired-supervisor'})
        self.write(self.checkpoint, {'checkpoint': 'accepted-original-state',
                                     'theories': {'Scheduler_Dispatch': {}}})
        config = self.worker / 'regen/config/proof-models.json'
        self.write(config, {'reasoning_effort': 'medium',
                            'role_models': {'File Planner': 'gpt-6-astra'}})
        state = {'accepted_contracts': ['inherited'], 'revision': 1}
        rows = []
        for arm in (self.baseline, self.pending):
            self.write(arm / 'theories/Scheduler_Dispatch/acceptance/commits/000001.json',
                       {'state': state})
            rows.append({'strategy': arm.name, 'run_dir': str(arm),
                         'initial_state_sha256': digest(state)})
        self.write(self.pending / 'checkpoint.json', self.read(self.checkpoint))
        for arm in (self.baseline, self.pending):
            self.write(arm / 'run.json', {'model_config_path': str(config),
                                        'budgets': {'calls': 99}, 'image': 'original-image'})
        self.preparation = self.arms / 'preparation.json'
        self.write(self.preparation, {'same_inherited_state': True, 'historical_costs_reset': False,
                                     'checkpoint_sha256': sha(self.checkpoint), 'arms': rows})
        self.receipt = self.control / 'control-receipt.json'
        self.write(self.receipt, {'arms': str(self.arms), 'checkpoint': str(self.checkpoint),
                                 'checkpoint_sha256': sha(self.checkpoint),
                                 'application_manifest': str(self.manifest),
                                 'application_manifest_sha256': sha(self.manifest)})
        self.binding = self.control / 'prepared-binding.json'
        self.write(self.binding, {'control_receipt_sha256': sha(self.receipt),
                                  'preparation_sha256': sha(self.preparation)})
        self.experiment = {
            'command': ['python-original', '-m', 'regen.execution.proof_session_runner',
                        'parallel-workspace', '--run-dir', str(self.baseline),
                        '--generation-strategy', 'baseline', '--native-slots', '1',
                        '--planner-model', 'gpt-6-astra', '--allow-external-call'],
            'cwd': str(self.worker), 'application_manifest_sha256': sha(self.manifest),
            'diagnostic_window_effective_seconds': 3600, 'historical_costs_reset': False,
            'production_resumed': False,
            'resource_requirements': {'min_free_disk_bytes': 100, 'min_available_memory_bytes': 200},
            'future_preserved_metadata': {'nested': ['original-setting']},
        }
        self.write(self.baseline / 'experiment.json', self.experiment)
        self.launch = self.baseline / 'service-launch.json'
        self.write(self.launch, {'experiment_sha256': sha(self.baseline / 'experiment.json'),
                                 'command': self.experiment['command'], 'cwd': str(self.worker)})
        self.baseline_report = self.control / 'comparison/baseline/report.json'
        self.report = {'terminal': True, 'window_result_verified': True,
                       'reason': 'first_new_contract', 'cleanup_error': None,
                       'final_service': {'running': False}, 'run_dir': str(self.baseline),
                       'budget_effective_seconds': 3600, 'wall_safety_limit_seconds': 10800,
                       'source_launch_sha256': sha(self.launch)}
        self.write(self.baseline_report, self.report)
        self.write(self.baseline / 'trial-stop.json',
                   {'reason': self.report['reason'], 'automatic_resume_allowed': False})
        self.verify = self.add_patch('regen.tools.contract_closure_continuation.verify')
        self.status = self.add_patch('regen.tools.contract_closure_continuation.trial.service.status',
                                     return_value={'running': False, 'identity_verified': True,
                                                   'status': 'inactive'})
        self.gate = self.add_patch('regen.tools.contract_closure_continuation.trial.native_exit_gate',
                                   return_value={'owned_native_containers': [], 'all_exited': True})
        self.start = self.add_patch('regen.tools.contract_closure_continuation.trial.service.start')
        self.stop = self.add_patch('regen.tools.contract_closure_continuation.trial.service.stop')
        self.observe = self.add_patch('regen.tools.contract_closure_continuation.trial.observe')

    def add_patch(self, target, **kwargs):
        patcher = patch(target, **kwargs)
        mock = patcher.start()
        self.addCleanup(patcher.stop)
        return mock

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))

    def read(self, path):
        return json.loads(path.read_text())

    def validate(self):
        return continuation.validate(self.control, self.supervisor_manifest)

    def test_validate_inherits_exact_experiment_except_target_and_strategy(self):
        evidence, baseline = self.validate()
        expected = copy.deepcopy(self.experiment)
        expected['command'][expected['command'].index('--run-dir') + 1] = str(self.pending)
        expected['command'][expected['command'].index('--generation-strategy') + 1] = 'helper-closure-v1'
        self.assertEqual(evidence['pending_experiment'], expected)
        self.assertEqual(baseline, self.report)
        self.assertEqual(self.read(self.baseline / 'experiment.json'), self.experiment)
        self.assertEqual(evidence['original_worker_application'], str(self.worker))
        self.assertFalse(evidence['proof_worker_release_changed'])
        self.assertFalse(evidence['baseline_restarted'])
        self.assertFalse((self.pending / 'experiment.json').exists())
        self.verify.assert_any_call(self.worker, self.manifest)
        self.start.assert_not_called()
        self.stop.assert_not_called()
        self.gate.assert_not_called()

    def test_validate_rejects_unverified_baseline_terminal_reports(self):
        for change in ({'terminal': False}, {'window_result_verified': False},
                       {'reason': 'interrupted'}, {'cleanup_error': 'failed'},
                       {'final_service': {'running': True}}):
            with self.subTest(change=change):
                self.write(self.baseline_report, dict(self.report, **change))
                with self.assertRaisesRegex(ValueError, 'baseline is not a verified completed trial'):
                    self.validate()
        self.start.assert_not_called()

    def test_validate_rejects_running_or_unverifiable_baseline_service(self):
        for status in ({'running': True, 'identity_verified': True, 'status': 'active'},
                       {'running': False, 'identity_verified': False, 'status': 'unknown'}):
            with self.subTest(status=status):
                self.status.return_value = status
                with self.assertRaisesRegex(ValueError, 'baseline service has not verifiably exited'):
                    self.validate()
        self.gate.assert_not_called()

    def test_validate_rejects_changed_preparation_bindings(self):
        for path in (self.receipt, self.preparation):
            before = path.read_bytes()
            with self.subTest(path=path):
                self.write(path, dict(self.read(path), changed=True))
                with self.assertRaisesRegex(ValueError, 'workflow preparation binding changed'):
                    self.validate()
            path.write_bytes(before)

    def test_validate_rejects_changed_manifest_checkpoint_or_launch(self):
        for path, message in ((self.manifest, 'original application manifest changed'),
                              (self.checkpoint, 'source checkpoint changed'),
                              (self.launch, 'baseline is not a verified completed trial')):
            before = path.read_bytes()
            with self.subTest(path=path):
                self.write(path, dict(self.read(path), changed=True))
                with self.assertRaisesRegex(ValueError, message):
                    self.validate()
            path.write_bytes(before)

    def test_validate_rejects_every_started_pending_marker(self):
        for marker in ('experiment.json', 'service-launch.json', 'service-invocation.json',
                       'service-runner.log', 'trial-stop.json', 'parallel-sessions'):
            path = self.pending / marker
            with self.subTest(marker=marker):
                if marker == 'parallel-sessions':
                    path.mkdir()
                else:
                    path.write_text('{}')
                with self.assertRaisesRegex(ValueError, 'optimized arm already started or bound'):
                    self.validate()
                if path.is_dir():
                    path.rmdir()
                else:
                    path.unlink()
        self.start.assert_not_called()

    def test_validate_rejects_pending_new_acceptance(self):
        self.write(self.pending / 'theories/Scheduler_Dispatch/acceptance/commits/000002.json',
                   {'state': {'new_contract': True}})
        with self.assertRaisesRegex(ValueError, 'optimized arm has post-checkpoint acceptance'):
            self.validate()
        self.start.assert_not_called()

    def test_validate_rejects_changed_pending_initial_state(self):
        self.write(self.pending / 'theories/Scheduler_Dispatch/acceptance/commits/000001.json',
                   {'state': {'changed': True}})
        with self.assertRaisesRegex(ValueError, 'inherited state changed'):
            self.validate()

    def test_validate_rejects_changed_baseline_experiment_binding(self):
        self.write(self.baseline / 'experiment.json', dict(self.experiment, production_resumed=True))
        with self.assertRaisesRegex(ValueError, 'baseline experiment binding differs'):
            self.validate()

    def test_validate_rejects_changed_prepared_execution_configuration(self):
        original = self.read(self.pending / 'run.json')
        for field in ('budgets', 'execution_models', 'implementation', 'checker_policy',
                      'complete_context_policy', 'image', 'schedule'):
            with self.subTest(field=field):
                self.write(self.pending / 'run.json', dict(original, **{field: 'changed'}))
                with self.assertRaisesRegex(ValueError, 'prepared execution configuration differs: ' + field):
                    self.validate()
        self.start.assert_not_called()

    def test_cleanup_failure_after_success_never_publishes_complete_comparison(self):
        self.start.side_effect = lambda arm: self.write(arm / 'service-launch.json', {'mock': True})
        self.observe.return_value = dict(self.report, run_dir=str(self.pending))
        exited = {'owned_native_containers': [], 'all_exited': True}
        self.gate.side_effect = [exited, exited, RuntimeError('final native gate failed')]
        with self.assertRaisesRegex(RuntimeError, 'final native gate failed'):
            continuation.continue_pending(self.control, self.output, self.supervisor_manifest)
        report = self.read(self.output / 'report.json')
        self.assertFalse(report['comparison_complete'])
        self.assertEqual(report['phase'], 'cleanup_failed')
        self.assertEqual(report['cleanup_error'], 'final native gate failed')
        self.assertEqual(self.read(self.output / 'progress.json'), report)
        self.assertFalse((self.control / 'comparison/report.json').exists())

    def test_continue_starts_only_helper_and_preserves_baseline(self):
        baseline_bytes = self.baseline_report.read_bytes()
        self.start.side_effect = lambda arm: self.write(arm / 'service-launch.json', {'mock': True})
        self.observe.return_value = dict(self.report, run_dir=str(self.pending))
        report = continuation.continue_pending(self.control, self.output, self.supervisor_manifest)
        self.start.assert_called_once_with(self.pending)
        self.observe.assert_called_once_with(self.pending, self.control / 'comparison/helper-closure-v1',
                                              'Scheduler_Dispatch')
        self.assertTrue(report['comparison_complete'])
        self.assertEqual([arm['strategy'] for arm in report['arms']], ['baseline', 'helper-closure-v1'])
        self.assertEqual(self.baseline_report.read_bytes(), baseline_bytes)
        self.assertEqual(self.read(self.output / 'report.json'), report)
        self.assertEqual(self.read(self.control / 'comparison/report.json'), report)
        self.stop.assert_not_called()
        self.assertEqual(self.read(self.pending / 'experiment.json')['cwd'], str(self.worker))

    def test_continue_persists_original_and_cleanup_errors(self):
        self.start.side_effect = lambda arm: self.write(arm / 'service-launch.json', {'mock': True})
        self.observe.side_effect = ValueError('observation failed')
        self.status.side_effect = lambda arm: {'running': arm == self.pending,
                                                'identity_verified': True, 'status': 'active'}
        self.stop.side_effect = RuntimeError('mock stop failed')
        baseline_bytes = self.baseline_report.read_bytes()
        with self.assertRaisesRegex(RuntimeError, 'mock stop failed'):
            continuation.continue_pending(self.control, self.output, self.supervisor_manifest)
        self.start.assert_called_once_with(self.pending)
        self.stop.assert_called_once_with(self.pending)
        report = self.read(self.output / 'report.json')
        self.assertFalse(report['comparison_complete'])
        self.assertEqual(report['phase'], 'cleanup_failed')
        self.assertEqual(report['cleanup_error'], 'mock stop failed')
        self.assertEqual(report['error'], {'type': 'ValueError', 'message': 'observation failed'})
        self.assertEqual(self.read(self.output / 'progress.json'), report)
        self.assertEqual(self.baseline_report.read_bytes(), baseline_bytes)
        self.assertFalse((self.control / 'comparison/report.json').exists())


if __name__ == '__main__':
    unittest.main()
