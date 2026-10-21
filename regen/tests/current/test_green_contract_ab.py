from contextlib import ExitStack
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from regen.execution.proof_commits import digest
from regen.tools import green_contract_ab as ab


class WindowTests(unittest.TestCase):
    def state(self, *, at=None, deadline=None, seconds=0, deferred=False):
        return {'effective_clock': {'schema': 'theory_effective_v1',
                'progress': [] if at is None else [{'theory': 'T', 'at': at, 'proof_ids': ['contract:target']}],
                'snapshot': {'theories': {'T': {'effective_seconds': seconds,
                    'first_crossing_at': deadline, 'deadline_decision_deferred': deferred}}}}}

    def test_other_contract_does_not_end_or_reset_window(self):
        state = self.state(seconds=3599)
        state['effective_clock']['progress'] = [{'theory': 'T', 'at': 10, 'proof_ids': ['contract:other']}]
        self.assertIsNone(ab.target_window(state, 'T', 'contract:target')['reason'])
        def evaluate(s, observations, now, budget, *, budget_policy):
            self.assertEqual(len(s['effective_clock']['progress']), 1)
            self.assertEqual(budget_policy, 'fixed_budget')
            self.assertEqual(budget, 3600)
            return {'effective_seconds': 3600}
        with patch.object(ab.clock, 'evaluate', side_effect=evaluate):
            ab.evaluate_window(state, {}, 3600, 3600)
        self.assertEqual(state['effective_clock']['progress'][0]['proof_ids'], ['contract:other'])
        state['effective_clock']['snapshot']['theories']['T'].update(effective_seconds=3600, first_crossing_at=3600)
        self.assertEqual(ab.target_window(state, 'T', 'contract:target')['reason'], 'trial_effective_budget_exhausted')

    def test_progress_restored_even_on_clock_error(self):
        state = self.state(at=1)
        with patch.object(ab.clock, 'evaluate', side_effect=ValueError('clock')):
            with self.assertRaises(ValueError):
                ab.evaluate_window(state, {}, 2, 3600)
        self.assertEqual(len(state['effective_clock']['progress']), 1)

    def test_real_clock_other_contract_does_not_extend_cumulative_deadline(self):
        state = {}
        evidence = ab.clock.clock_state(state)
        evidence['events'] = [{'at': 0, 'event': 'theory_generation_started', 'theory': 'T',
                               'run_dir': '/arm', 'generation_id': 'g', 'clock_schema': ab.clock.MODE}]
        evidence['progress'] = [{'theory': 'T', 'at': 3500, 'proof_ids': ['contract:other']},
                                {'theory': 'T', 'at': 3605, 'proof_ids': ['contract:target']}]
        snapshot = ab.evaluate_window(state, {'/arm': {'running': True}}, 3610, 3600)
        self.assertEqual(snapshot['theories']['T']['first_crossing_at'], 3600)
        result = ab.target_window(state, 'T', 'contract:target')
        self.assertFalse(result['target_success'])
        self.assertTrue(result['late_target_acceptance'])
        self.assertEqual(len(evidence['progress']), 2)

    def test_target_deadline_tie_and_late_shutdown_acceptance(self):
        for at, success in ((3599, True), (3600, True), (3601, False)):
            with self.subTest(at=at):
                row = ab.target_window(self.state(at=at, deadline=3600, seconds=3601), 'T', 'contract:target')
                self.assertEqual(row['target_success'], success)
                self.assertEqual(row['reason'], 'target_contract_green' if success else 'trial_effective_budget_exhausted')

    def test_uncertain_queue_cannot_claim_target_success(self):
        row = ab.target_window(self.state(at=100, deferred=True), 'T', 'contract:target')
        self.assertFalse(row['window_result_verified'])
        self.assertIsNone(row['reason'])


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.control = self.root/'workflow'
        self.manifest = self.root/'manifest.json'
        self.manifest.write_text('{}')
        cases = []
        for name in ('deletion', 'insertion'):
            checkpoint = self.root/(name+'.json')
            checkpoint.write_text(json.dumps({'source_run': str(self.root/name), 'theories': {'T': {'commit_id': name}}}))
            cases.append({'name': name, 'theory': 'T', 'target_contract_id': 'contract:target',
                          'checkpoint': str(checkpoint), 'checkpoint_sha256': ab.watch.sha(checkpoint)})
        self.config = {'cases': cases, 'budget_effective_seconds': 3600, 'wall_limit_seconds': 10800,
                       'variant_strategy': 'bounded-audited-prefix-v1'}
        self.config_path = self.root/'config.json'
        self.config_path.write_text(json.dumps(self.config))
        self.state = {'graph': {'nodes': [{'id': 'contract:target'}]}, 'proofs': {'helper:inherited': 'proof'},
                      'batch_requests': 192, 'historical_costs': 5000}

    def fork(self, source, root, *, checkpoint):
        directory = root/'theories/T/acceptance/commits'
        directory.mkdir(parents=True)
        (directory/'000001.json').write_text(json.dumps({'state': self.state}))
        (root/'checkpoint.json').write_text(json.dumps(checkpoint))
        return {'run_id': root.name}

    def patches(self, stack):
        stack.enter_context(patch.object(ab, 'verify'))
        stack.enter_context(patch('regen.execution.repository_execution.fork_workspace_continuation', side_effect=self.fork))
        def start(root):
            (root/'service-launch.json').write_text('{}')
        starter = stack.enter_context(patch.object(ab.service, 'start', side_effect=start))
        stack.enter_context(patch.object(ab.service, 'status', return_value={
            'running': False, 'identity_verified': True, 'status': 'stopped'}))
        gate = stack.enter_context(patch.object(ab.closure, 'native_exit_gate', return_value={'all_exited': True}))
        return starter, gate

    def test_four_arms_serial_and_identical_state_per_case(self):
        order = []
        with ExitStack() as stack:
            start, gate = self.patches(stack)
            def observed(root, output, theory, target, **limits):
                order.append(root.name)
                self.assertEqual(start.call_count, len(order))
                self.assertEqual(limits, {'budget': 3600, 'wall_limit': 10800})
                experiment = json.loads((root/'experiment.json').read_text())
                self.assertEqual(experiment['target_contract_id'], target)
                self.assertEqual(experiment['diagnostic_window_effective_seconds'], 3600)
                self.assertIn('--max-parallel-theories', experiment['command'])
                focus = experiment['command'].index('--focus-contract-id')
                self.assertEqual(experiment['command'][focus+1], target)
                return {'window_result_verified': True, 'target_success': False,
                        'reason': 'trial_effective_budget_exhausted'}
            stack.enter_context(patch.object(ab, 'observe', side_effect=observed))
            report = ab.run_workflow(self.control, self.config_path, self.manifest)
        self.assertEqual(order, ['deletion--baseline', 'deletion--variant', 'insertion--baseline', 'insertion--variant'])
        self.assertTrue(report['comparison_complete'])
        self.assertEqual(len(report['arms']), 4)
        preparation = json.loads((self.control/'preparation.json').read_text())
        self.assertEqual({r['initial_state_sha256'] for r in preparation['arms']}, {digest(self.state)})
        progress = json.loads((self.control/'prepare-progress.json').read_text())
        self.assertEqual(progress['completed_arms'], order)
        self.assertEqual(progress['phase'], 'arm_prepared')

    def test_fork_preparation_progress_exists_before_long_work(self):
        self.control.mkdir()
        def failed(source, root, *, checkpoint):
            progress = json.loads((self.control/'prepare-progress.json').read_text())
            self.assertEqual(progress['phase'], 'forking')
            self.assertEqual(progress['current_arm'], 'deletion--baseline')
            self.assertEqual(progress['completed_arms'], [])
            raise OSError('fork failed')
        with patch('regen.execution.repository_execution.fork_workspace_continuation', side_effect=failed):
            with self.assertRaisesRegex(OSError, 'fork failed'):
                ab.prepare(self.control, self.config)

    def test_over_limit_window_rejected_before_expensive_preparation(self):
        self.config.update(incremental_checkpoint_window=True,budget_effective_seconds=10801)
        self.config_path.write_text(json.dumps(self.config))
        with self.assertRaisesRegex(ValueError,'at most 10800'):
            ab.configuration(self.config_path)

    def test_variant_only_runs_two_cases_without_new_baselines(self):
        self.config.update(variant_only=True,variant_strategy='sketch-groups-v1',
                           wall_budget=True,wall_limit_seconds=3600)
        self.config_path.write_text(json.dumps(self.config))
        order=[]
        with ExitStack() as stack:
            start,_=self.patches(stack)
            def observed(root,output,theory,target,**limits):
                order.append(root.name)
                self.assertEqual(limits,{'budget':3600,'wall_limit':3600,'wall_budget':True})
                return {'window_result_verified':True,'target_success':False,
                        'reason':'trial_wall_safety_limit'}
            stack.enter_context(patch.object(ab,'observe',side_effect=observed))
            report=ab.run_workflow(self.control,self.config_path,self.manifest)
        self.assertEqual(order,['deletion--variant','insertion--variant'])
        self.assertEqual(start.call_count,2)
        self.assertTrue(report['comparison_complete'])
        preparation=json.loads((self.control/'preparation.json').read_text())
        self.assertTrue(preparation['variant_only'])
        self.assertFalse(preparation['same_inherited_states'])

    def test_explicit_sol_model_is_release_bound_and_used_in_command(self):
        self.config.update(model='gpt-5.6-sol',reasoning_effort='medium',variant_only=True)
        self.config_path.write_text(json.dumps(self.config))
        with ExitStack() as stack:
            self.patches(stack)
            stack.enter_context(patch('regen.infrastructure.audited_llm.model_settings',return_value={
                'role_models':{'File Planner':'gpt-5.6-sol'},'reasoning_effort':'medium'}))
            def observed(root,*args,**kwargs):
                argv=json.loads((root/'experiment.json').read_text())['command']
                self.assertEqual(argv[argv.index('--planner-model')+1],'gpt-5.6-sol')
                return {'window_result_verified':True,'target_success':True}
            stack.enter_context(patch.object(ab,'observe',side_effect=observed))
            self.assertTrue(ab.run_workflow(self.control,self.config_path,self.manifest)['comparison_complete'])

    def test_independent_case_failure_does_not_stop_peer_or_claim_complete(self):
        import threading
        self.config.update(variant_only=True,case_workers=2,independent_case_failures=True)
        self.config_path.write_text(json.dumps(self.config))
        barrier=threading.Barrier(2)
        with ExitStack() as stack:
            self.patches(stack)
            stack.enter_context(patch.object(ab,'parallel_admission',return_value={'admitted':True}))
            def observed(root,*args,**limits):
                barrier.wait(timeout=5)
                if root.name.startswith('deletion'):raise RuntimeError('one provider failed')
                self.assertFalse(limits['stop_event'].is_set())
                return {'window_result_verified':True,'target_success':True}
            stack.enter_context(patch.object(ab,'observe',side_effect=observed))
            report=ab.run_workflow(self.control,self.config_path,self.manifest)
        self.assertEqual(report['phase'],'partial')
        self.assertFalse(report['comparison_complete'])
        self.assertEqual(sum(r['report']['target_success'] for r in report['arms']),1)

    def test_two_case_workers_overlap_and_keep_independent_budgets(self):
        import threading
        self.config.update(variant_only=True,variant_strategy='sketch-reviewed-v1',case_workers=2,
                           native_memory_mb=12288,budget_effective_seconds=7200,
                           wall_budget=True,wall_limit_seconds=7200)
        self.config_path.write_text(json.dumps(self.config))
        barrier=threading.Barrier(2)
        observed=[]
        with ExitStack() as stack:
            start,_=self.patches(stack)
            stack.enter_context(patch.object(ab,'parallel_admission',return_value={'admitted':True}))
            def observe(root,output,theory,target,**limits):
                barrier.wait(timeout=5)
                observed.append(root.name)
                self.assertEqual(limits['budget'],7200)
                self.assertGreater(limits['wall_limit'],7190)
                self.assertLessEqual(limits['wall_limit'],7200)
                self.assertFalse(limits['stop_event'].is_set())
                argv=json.loads((root/'experiment.json').read_text())['command']
                self.assertEqual(argv[argv.index('--native-memory-mb')+1],'12288')
                return {'window_result_verified':True,'target_success':False}
            stack.enter_context(patch.object(ab,'observe',side_effect=observe))
            report=ab.run_workflow(self.control,self.config_path,self.manifest)
        self.assertCountEqual(observed,['deletion--variant','insertion--variant'])
        self.assertEqual(start.call_count,2)
        self.assertTrue(report['comparison_complete'])
        self.assertEqual(report['case_workers'],2)

    def test_parallel_failure_stops_the_peer_and_preserves_error(self):
        import threading
        self.config.update(variant_only=True,case_workers=2,native_memory_mb=12288)
        self.config_path.write_text(json.dumps(self.config))
        barrier=threading.Barrier(2)
        stopped=threading.Event()
        with ExitStack() as stack:
            self.patches(stack)
            stack.enter_context(patch.object(ab,'parallel_admission',return_value={}))
            def observe(root,output,theory,target,**limits):
                barrier.wait(timeout=5)
                if root.name.startswith('deletion'):
                    raise ValueError('parallel observer failure')
                self.assertTrue(limits['stop_event'].wait(timeout=5))
                stopped.set()
                return {'window_result_verified':False}
            stack.enter_context(patch.object(ab,'observe',side_effect=observe))
            with self.assertRaisesRegex(ValueError,'parallel observer failure'):
                ab.run_workflow(self.control,self.config_path,self.manifest)
        self.assertTrue(stopped.is_set())
        self.assertEqual(json.loads((self.control/'report.json').read_text())['phase'],'failed')

    def test_configured_memory_matches_the_checkpoint_budget_binding(self):
        from unittest.mock import Mock
        overview=Mock(selected=['T'],options={})
        root=self.root/'memory-arm';root.mkdir()
        row={'run_dir':str(root),'theory':'T','strategy':'sketch-reviewed-v1',
             'target_contract_id':'contract:target','checkpoint':self.config['cases'][0]['checkpoint']}
        with patch('regen.planning.proof_workspace_agent.WorkspaceController',return_value=overview), \
             patch('regen.tools.checkpoint_trial_budget.initialize_checkpoint_trial_budget',return_value={}) as initialize:
            ab.diagnostic_window(row,{'authorization':'user authorized','native_memory_mb':12288})
        self.assertEqual(initialize.call_args.args[2]['memory_mb'],12288)
        self.assertEqual(initialize.call_args.args[2]['build_ml_heap_mb'],9216)

    def test_prepared_workflow_reuses_validated_forks_and_is_single_use(self):
        self.config.update(variant_only=True,variant_strategy='sketch-groups-v1')
        self.config_path.write_text(json.dumps(self.config))
        self.control.mkdir()
        with patch('regen.execution.repository_execution.fork_workspace_continuation',side_effect=self.fork):
            ab.prepare(self.control,self.config)
        with ExitStack() as stack:
            self.patches(stack)
            fork=stack.enter_context(patch('regen.execution.repository_execution.fork_workspace_continuation'))
            stack.enter_context(patch.object(ab,'observe',return_value={'window_result_verified':True}))
            report=ab.run_workflow(self.control,self.config_path,self.manifest,reuse_prepared=True)
        fork.assert_not_called()
        self.assertTrue(report['comparison_complete'])
        with ExitStack() as stack:
            self.patches(stack)
            with self.assertRaisesRegex(ValueError,'single-use'):
                ab.run_workflow(self.control,self.config_path,self.manifest,reuse_prepared=True)

    def test_diagnostic_policy_matches_normal_cli_integration_default(self):
        from unittest.mock import Mock
        root=self.root/'arm';root.mkdir()
        overview=Mock(root=root,selected=['T'],options={'model':'gpt-6-astra',
            'call_timeout':900,'check_timeout':900,'candidate_timeout':120})
        row={'run_dir':str(root),'theory':'T','strategy':'sketch-groups-v1',
             'target_contract_id':'contract:target','checkpoint':self.config['cases'][0]['checkpoint']}
        with patch('regen.planning.proof_workspace_agent.WorkspaceController',return_value=overview), \
             patch('regen.tools.checkpoint_trial_budget.initialize_checkpoint_trial_budget',return_value={}) as initialize:
            ab.diagnostic_window(row,{'authorization':'user authorized','budget_effective_seconds':3600})
        binding=initialize.call_args.args[2]
        self.assertTrue(binding['integration_required'])
        self.assertIsInstance(binding['cpus'],float)
        self.assertEqual(binding['selected_theories'],['T'])
        self.assertEqual(binding['candidate_timeout'],120)

    def test_failure_stops_switch_and_persists_original_and_cleanup_errors(self):
        with ExitStack() as stack:
            start, gate = self.patches(stack)
            gate.side_effect = RuntimeError('cleanup denied')
            stack.enter_context(patch.object(ab, 'observe', side_effect=ValueError('original observer failure')))
            with self.assertRaisesRegex(ValueError, 'original observer failure'):
                ab.run_workflow(self.control, self.config_path, self.manifest)
        self.assertEqual(start.call_count, 1)
        report = json.loads((self.control/'report.json').read_text())
        self.assertFalse(report['comparison_complete'])
        self.assertEqual(report['error']['message'], 'original observer failure')
        self.assertIn('cleanup denied', report['cleanup_error']['message'])

    def test_unverified_result_blocks_second_arm(self):
        with ExitStack() as stack:
            start, _ = self.patches(stack)
            stack.enter_context(patch.object(ab, 'observe', return_value={'window_result_verified': False}))
            with self.assertRaisesRegex(RuntimeError, 'verified terminal'):
                ab.run_workflow(self.control, self.config_path, self.manifest)
        self.assertEqual(start.call_count, 1)

    def test_preparation_rejects_inherited_target(self):
        self.state['proofs']['contract:target'] = 'existing'
        self.control.mkdir()
        with patch('regen.execution.repository_execution.fork_workspace_continuation', side_effect=self.fork):
            with self.assertRaisesRegex(ValueError, 'already GREEN'):
                ab.prepare(self.control, self.config)

    def test_changed_checkpoint_rejected_before_prepare(self):
        Path(self.config['cases'][0]['checkpoint']).write_text('{}')
        with self.assertRaisesRegex(ValueError, 'identity mismatch'):
            ab.configuration(self.config_path)

    def test_unbound_service_and_changed_binding_fail_closed(self):
        path = self.control/'arms/deletion--baseline/service-launch.json'
        path.parent.mkdir(parents=True)
        path.write_text('{}')
        with self.assertRaisesRegex(RuntimeError, 'unbound'):
            ab.stop_workflow(self.control)

    def test_cleanup_attempts_native_gate_even_when_service_stop_fails(self):
        root = self.root/'arm'
        root.mkdir()
        (root/'service-launch.json').write_text('{}')
        with patch.object(ab, 'checked_status', return_value={'running': True}), \
             patch.object(ab.service, 'stop', side_effect=RuntimeError('stop error')), \
             patch.object(ab.closure, 'native_exit_gate', side_effect=RuntimeError('gate error')) as gate:
            errors = ab.cleanup_arm(root)
        gate.assert_called_once_with(root)
        self.assertEqual([r['stage'] for r in errors], ['service_stop', 'native_exit_gate'])

    def test_cleanup_checks_frozen_binding_before_service_access(self):
        with ExitStack() as stack:
            self.patches(stack)
            stack.enter_context(patch.object(ab, 'observe', return_value={'window_result_verified': True}))
            ab.run_workflow(self.control, self.config_path, self.manifest)
        (self.control/'preparation.json').write_text('{}')
        with patch.object(ab, 'cleanup_arm') as cleanup:
            with self.assertRaisesRegex(RuntimeError, 'binding changed'):
                ab.stop_workflow(self.control)
        cleanup.assert_not_called()


class ProviderWarmupTests(unittest.TestCase):
    def test_materializes_only_byte_matched_sealed_providers(self):
        for tampered in (False,True):
            with self.subTest(tampered=tampered), tempfile.TemporaryDirectory() as tmp:
                base=Path(tmp); root=base/'arm'; source=base/'source'
                generated=root/'generated'; generated.mkdir(parents=True)
                provider=generated/'Regen_P.thy'; provider.write_text('sealed provider')
                directory=root/'theories/T'; directory.mkdir(parents=True)
                (directory/'imports.json').write_text('["Regen_P"]')
                record={'source_heaps':str(source/'theories/T/heaps'),
                    'provider_session_receipt_copied':False,
                    'standard_native_build_gate_preserved':True,
                    'scope':'sealed provider session only; target theory was never sealed',
                    'exact_generated_provider_sha256':{'Regen_P.thy':ab.watch.sha(provider)},
                    'imports':['Regen_P']}
                manifest=base/'manifest.json'; manifest.write_text(json.dumps(record))
                case={'theory':'T','native_provider_heap_manifest':str(manifest),
                    'native_provider_heap_manifest_sha256':ab.watch.sha(manifest)}
                if tampered: provider.write_text('different provider')
                with patch('regen.infrastructure.resource_budget.clone_heaps',return_value={}) as clone:
                    if tampered:
                        with self.assertRaisesRegex(ValueError,'continuation providers differ'):
                            ab.warm_provider_heaps(root,case,source)
                        clone.assert_not_called()
                    else:
                        ab.warm_provider_heaps(root,case,source)
                        self.assertEqual((directory/'generated/Regen_P.thy').read_bytes(),provider.read_bytes())
                        clone.assert_called_once_with(source/'theories/T/heaps',directory/'heaps')
                        self.assertFalse((directory/'provider-session.json').exists())


class ObserverTests(unittest.TestCase):
    def test_other_contract_at_first_poll_does_not_stop_target_window(self):
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            root = Path(tmp)/'arm'
            root.mkdir()
            (root/'checkpoint.json').write_text('{}')
            (root/'service-launch.json').write_text('{}')
            stack.enter_context(patch.object(ab.closure, 'accepted_ids', side_effect=[set(), {'contract:other'}]))
            status = {'running': True, 'identity_verified': True, 'status': 'running'}
            stack.enter_context(patch.object(ab.service, 'status', side_effect=[status, status, status,
                {'running': False, 'identity_verified': True, 'status': 'stopped'}]))
            stop = stack.enter_context(patch.object(ab.service, 'stop'))
            stack.enter_context(patch.object(ab.closure, 'native_exit_gate'))
            stack.enter_context(patch.object(ab.clock, 'ingest'))
            def scan(roots, state):
                ab.clock.clock_state(state)['progress'] = [{'theory': 'T', 'at': 1, 'proof_ids': ['contract:other']}]
            stack.enter_context(patch.object(ab.watch, 'scan', side_effect=scan))
            seconds = iter((3599, 3600, 3600))
            def evaluate(state, observations, now, budget, *, budget_policy):
                self.assertEqual(len(ab.clock.clock_state(state)['progress']), 1)
                self.assertEqual(budget_policy, 'fixed_budget')
                value = next(seconds)
                result = {'theories': {'T': {'effective_seconds': value,
                    'first_crossing_at': 3600 if value == 3600 else None}}}
                ab.clock.clock_state(state)['snapshot'] = result
                return result
            stack.enter_context(patch.object(ab.clock, 'evaluate', side_effect=evaluate))
            sleep = stack.enter_context(patch.object(ab.time, 'sleep'))
            report = ab.observe(root, Path(tmp)/'output', 'T', 'contract:target')
            self.assertTrue(report['window_result_verified'])
            self.assertFalse(report['target_success'])
            self.assertEqual(report['reason'], 'trial_effective_budget_exhausted')
            self.assertEqual(report['new_contract_ids'], ['contract:other'])
            sleep.assert_called_once_with(5)
            stop.assert_called_once_with(root)


if __name__ == '__main__':
    unittest.main()
