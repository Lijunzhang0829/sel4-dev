import unittest
import json
from pathlib import Path
import signal
import tempfile
from contextlib import ExitStack
from unittest.mock import patch

from regen.tools.contract_closure_validation import terminal_reason, run_pair, window_contracts, stop_pair
from regen.tools.proof_idle_watch import sha
from regen.execution.proof_commits import digest
from regen.tools.contract_closure_validation import native_exit_gate, stop_workflow, prepare_and_run, observe
from types import SimpleNamespace


class NativeExitGateTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.name='regen-isabelle-build-123-'+'a'*32
        (self.root/'service-invocation.json').write_text(json.dumps({'main_pid':123}))
        (self.root/'service-runner.log').write_text('[regen-event] '+json.dumps({
            'event':'isabelle_build_started','container':self.name})+'\n')

    def inspected(self,running=True,command=None):
        return SimpleNamespace(returncode=0,stderr='',stdout=json.dumps([{'Name':'/'+self.name,
            'Config':{'Cmd':command or ['/bin/sh','/native-owner/owner.sh','60','/native-owner/owner.heartbeat','isabelle']},
            'State':{'Running':running}}]))

    def test_grace_expired_stops_exact_owned_name(self):
        with patch('regen.tools.contract_closure_validation.subprocess.run',side_effect=[
                self.inspected(),self.inspected(),SimpleNamespace(returncode=0),self.inspected(False)]) as run:
            self.assertTrue(native_exit_gate(self.root,grace_seconds=0)['all_exited'])
        self.assertEqual(run.call_args_list[2].args[0],['docker','stop','--timeout','10',self.name])

    def test_unknown_owner_never_stopped(self):
        with patch('regen.tools.contract_closure_validation.subprocess.run',return_value=self.inspected(command=['unrelated'])) as run:
            with self.assertRaisesRegex(RuntimeError,'refusing cleanup'):
                native_exit_gate(self.root,grace_seconds=0)
        self.assertEqual(run.call_count,1)

    def test_pid_mismatch_never_inspected_or_stopped(self):
        (self.root/'service-invocation.json').write_text(json.dumps({'main_pid':456}))
        with patch('regen.tools.contract_closure_validation.subprocess.run') as run:
            with self.assertRaisesRegex(RuntimeError,'PID/UUID'):
                native_exit_gate(self.root)
        run.assert_not_called()

    def test_absent_container_is_clean(self):
        for prefix in ('Error: No such object: ', 'error: no such object: ',
                       'Error response from daemon: No such container: ', 'No such object: '):
            with self.subTest(prefix=prefix), patch(
                    'regen.tools.contract_closure_validation.subprocess.run',return_value=SimpleNamespace(
                        returncode=1,stderr=prefix+self.name+'\n')) as run:
                self.assertTrue(native_exit_gate(self.root)['all_exited'])
                self.assertEqual(run.call_count,1)

    def test_missing_diagnostic_must_match_exact_object_and_entire_error(self):
        for error in ('error: no such object: other',
                      'error: no such object: '+self.name.upper(),
                      'permission denied; No such object: '+self.name,
                      'error: no such object: '+self.name+'\nCannot connect to Docker daemon',
                      'permission denied while trying to connect to Docker',
                      'Cannot connect to the Docker daemon'):
            with self.subTest(error=error), patch(
                    'regen.tools.contract_closure_validation.subprocess.run',return_value=SimpleNamespace(
                        returncode=1,stderr=error)) as run:
                with self.assertRaisesRegex(RuntimeError,'identity inspection failed'):
                    native_exit_gate(self.root,grace_seconds=0)
                self.assertEqual(run.call_count,1)

    def test_resident_requires_exact_arm_mount(self):
        name='regen-batch-'+'b'*16
        directory=self.root/name
        directory.mkdir()
        (directory/'owner_watchdog.sh').write_text('owner')
        (self.root/'service-runner.log').write_text('')
        bad=SimpleNamespace(returncode=0,stderr='',stdout=json.dumps([{'Name':'/'+name,
            'Config':{'Cmd':['/bin/sh','/worker/owner_watchdog.sh','60','/worker/owner.heartbeat']},
            'State':{'Running':True},'Mounts':[{'Destination':'/worker','Source':'/different-arm'}]}]))
        with patch('regen.tools.contract_closure_validation.subprocess.run',return_value=bad) as run:
            with self.assertRaisesRegex(RuntimeError,'mount identity'):
                native_exit_gate(self.root,grace_seconds=0)
        self.assertEqual(run.call_count,1)

    def test_prepare_only_stop_is_safe(self):
        self.assertFalse(stop_workflow(self.root)['services_launched'])

    def test_unbound_service_receipt_blocks_cleanup_claim(self):
        service_path=self.root/'arms/baseline/service-launch.json'
        service_path.parent.mkdir(parents=True)
        service_path.write_text('{}')
        with self.assertRaisesRegex(RuntimeError,'unbound workflow'):
            stop_workflow(self.root)

    def test_interrupted_preparation_never_starts_model_service(self):
        checkpoint=self.root/'checkpoint.json'
        checkpoint.write_text('{}')
        manifest=self.root/'manifest.json'
        manifest.write_text('{}')
        with patch('regen.tools.application_release.verify'), \
             patch('regen.tools.contract_closure_validation.prepare_pair',side_effect=KeyboardInterrupt), \
             patch('regen.tools.contract_closure_validation.run_pair') as run:
            with self.assertRaises(KeyboardInterrupt):
                prepare_and_run(self.root/'workflow',checkpoint=checkpoint,application_manifest=manifest,prior_trial=self.root/'old')
        run.assert_not_called()
        self.assertTrue((self.root/'workflow/control-receipt.json').exists())
        self.assertFalse(stop_workflow(self.root/'workflow')['services_launched'])

    def test_changed_control_receipt_rejects_cleanup(self):
        control=self.root/'workflow'
        control.mkdir()
        (control/'control-receipt.json').write_text(json.dumps({'arms':str(control/'arms')}))
        (control/'prepared-binding.json').write_text(json.dumps({'control_receipt_sha256':'incorrect'}))
        with self.assertRaisesRegex(RuntimeError,'receipt changed'):
            stop_workflow(control)

    def test_preparation_forwards_expanded_limits_and_neutral_history(self):
        checkpoint=self.root/'checkpoint.json'
        checkpoint.write_text('{}')
        manifest=self.root/'manifest.json'
        manifest.write_text('{}')
        def prepare(checkpoint,arms):
            arms.mkdir()
            (arms/'preparation.json').write_text('{}')
        with patch('regen.tools.application_release.verify'), \
             patch('regen.tools.contract_closure_validation.prepare_pair',side_effect=prepare), \
             patch('regen.tools.contract_closure_validation.stop_workflow'), \
             patch('regen.tools.contract_closure_validation.run_pair',return_value={'comparison_complete':True}) as run:
            control=self.root/'workflow'
            prepare_and_run(control,checkpoint=checkpoint,application_manifest=manifest,
                            prior_trial=self.root/'old',budget_effective_seconds=7200,wall_limit=14400)
        run.assert_called_once_with(control/'arms',control/'comparison',manifest,
                                    budget_effective_seconds=7200,wall_limit=14400)
        receipt=json.loads((control/'control-receipt.json').read_text())
        self.assertEqual(receipt['budget_effective_seconds'],7200)
        self.assertEqual(receipt['wall_safety_limit_seconds'],14400)
        self.assertEqual(receipt['prior_trial'],str(self.root/'old'))
        self.assertNotIn('prior_invalid_trial',receipt)

    def test_preparation_retains_original_failure_when_fallback_cleanup_fails(self):
        checkpoint=self.root/'checkpoint.json'
        checkpoint.write_text('{}')
        manifest=self.root/'manifest.json'
        manifest.write_text('{}')
        with patch('regen.tools.application_release.verify'), \
             patch('regen.tools.contract_closure_validation.prepare_pair',side_effect=ValueError('original preparation failure')), \
             patch('regen.tools.contract_closure_validation.stop_workflow',side_effect=RuntimeError('cleanup unavailable')):
            with self.assertRaisesRegex(ValueError,'original preparation failure'):
                prepare_and_run(self.root/'workflow',checkpoint=checkpoint,application_manifest=manifest,prior_trial=self.root/'old')
        evidence=json.loads((self.root/'workflow/cleanup-error.json').read_text())
        self.assertEqual(evidence['original_error']['message'],'original preparation failure')
        self.assertEqual(evidence['message'],'cleanup unavailable')


class DiagnosticWindowTests(unittest.TestCase):
    def reason(self,**row):
        return terminal_reason({'theories':{'T':row}},'T')

    def test_total_budget_not_inactivity(self):
        self.assertEqual(self.reason(effective_seconds=3600,idle_effective_seconds=1),
                         'trial_effective_budget_exhausted')

    def test_first_new_contract_terminates(self):
        self.assertEqual(self.reason(effective_seconds=27,new_contract_count=1),'first_new_contract')

    def test_late_contract_does_not_erase_budget(self):
        self.assertEqual(self.reason(effective_seconds=3602,new_contract_count=1,
            first_crossing_at=3600,last_new_contract_at=3602),'trial_effective_budget_exhausted')

    def test_exact_deadline_contract_wins(self):
        self.assertEqual(self.reason(effective_seconds=3600,new_contract_count=1,
            first_crossing_at=3600,last_new_contract_at=3600),'first_new_contract')

    def test_helper_only_progress_does_not_terminate(self):
        self.assertIsNone(self.reason(effective_seconds=3599,new_contract_count=0))

    def test_expanded_window_keeps_first_contract_edge(self):
        self.assertIsNone(terminal_reason({'theories':{'T':{'effective_seconds':3600}}},'T',budget=7200))
        self.assertEqual(terminal_reason({'theories':{'T':{'effective_seconds':3601,'new_contract_count':1}}},
                                         'T',budget=7200),'first_new_contract')

    def test_invalid_limits_fail_before_side_effects(self):
        for budget in (0,-1,float('nan'),float('inf')):
            with self.subTest(budget=budget), self.assertRaises(ValueError):
                run_pair('/not-used','/not-used','/not-used',budget_effective_seconds=budget)

    def test_unclassified_preparation_defers_budget(self):
        self.assertIsNone(self.reason(effective_seconds=3700,deadline_decision_deferred=True))

    def test_no_clock_yet_does_not_fake_expiration(self):
        self.assertIsNone(terminal_reason({},'T'))

    def test_late_acceptance_excluded_from_window_success(self):
        state={'effective_clock':{'schema':'theory_effective_v1',
            'progress':[{'at':3602,'theory':'T','proof_ids':['contract:late']}],
            'snapshot':{'theories':{'T':{'first_crossing_at':3600}}}}}
        result=window_contracts(state,'T',['contract:late'])
        self.assertEqual(result['in_window_new_contract_ids'],[])
        self.assertEqual(result['late_new_contract_ids'],['contract:late'])

    def test_shutdown_tail_not_a_second_trial_success(self):
        state={'effective_clock':{'schema':'theory_effective_v1','progress':[
            {'at':100,'theory':'T','proof_ids':['contract:first']},
            {'at':103,'theory':'T','proof_ids':['contract:tail']}],
            'snapshot':{'theories':{'T':{'first_crossing_at':None}}}}}
        result=window_contracts(state,'T',['contract:first','contract:tail'])
        self.assertEqual(result['in_window_new_contract_ids'],['contract:first'])
        self.assertEqual(result['late_new_contract_ids'],['contract:tail'])

    def test_failed_reconciliation_cannot_claim_success(self):
        result=window_contracts({},'T',['contract:unclassified'],verified=False)
        self.assertFalse(result['window_result_verified'])
        self.assertFalse(result['in_window_contract_success'])
        self.assertEqual(result['unclassified_new_contract_ids'],['contract:unclassified'])

    def test_uncertain_preparation_cannot_verify_window_success(self):
        state={'effective_clock':{'schema':'theory_effective_v1','progress':[
            {'at':100,'theory':'T','proof_ids':['contract:first']}],
            'snapshot':{'theories':{'T':{'deadline_decision_deferred':True}}}}}
        result=window_contracts(state,'T',['contract:first'])
        self.assertFalse(result['window_result_verified'])
        self.assertFalse(result['in_window_contract_success'])


class SupervisorCleanupTests(unittest.TestCase):
    """Lifecycle controls only; proof checks are never mocked in native trials."""
    def setUp(self):
        temporary=tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.arms=self.root/'arms'
        self.arms.mkdir()
        rows=[]
        for strategy in ('baseline','helper-closure-v1'):
            arm=self.arms/strategy
            commits=arm/'theories/Scheduler_Dispatch/acceptance/commits'
            commits.mkdir(parents=True)
            (commits/'000001.json').write_text(json.dumps({'state':{}}))
            rows.append({'strategy':strategy,'run_dir':str(arm),'initial_state_sha256':digest({})})
        (self.arms/'preparation.json').write_text(json.dumps({'arms':rows,'same_inherited_state':True}))
        self.manifest=self.root/'manifest.json'
        self.manifest.write_text('{}')

    def run_failure(self, failure):
        def launch(root):
            (root/'service-launch.json').write_text('{}')
        with patch('regen.tools.application_release.verify'), \
             patch('regen.tools.contract_closure_validation.service.start',side_effect=launch), \
             patch('regen.tools.contract_closure_validation.service.status',return_value={'running':True}), \
             patch('regen.tools.contract_closure_validation.service.stop') as stop, \
             patch('regen.tools.contract_closure_validation.observe',side_effect=failure):
            with self.assertRaises(BaseException):
                run_pair(self.arms,self.root/'comparison',self.manifest)
            stop.assert_called_once_with(self.arms/'baseline')

    def test_observer_setup_failure_stops_started_arm(self):
        self.run_failure(OSError('observer output unavailable'))

    def test_sigterm_stops_started_arm(self):
        old=signal.getsignal(signal.SIGTERM)
        def terminate(*args,**kwargs):
            signal.getsignal(signal.SIGTERM)(signal.SIGTERM,None)
        self.run_failure(terminate)
        self.assertEqual(signal.getsignal(signal.SIGTERM),old)

    def test_launch_error_after_receipt_stops_owned_arm(self):
        def launch(root):
            (root/'service-launch.json').write_text('{}')
            raise OSError('launch response lost after start')
        with patch('regen.tools.application_release.verify'), \
             patch('regen.tools.contract_closure_validation.service.start',side_effect=launch), \
             patch('regen.tools.contract_closure_validation.service.status',return_value={'running':True}), \
             patch('regen.tools.contract_closure_validation.service.stop') as stop:
            with self.assertRaises(OSError):
                run_pair(self.arms,self.root/'comparison',self.manifest)
            stop.assert_called_once_with(self.arms/'baseline')

    def test_cleanup_identity_mismatch_is_not_success(self):
        (self.arms/'baseline/service-launch.json').write_text('{}')
        with patch('regen.tools.contract_closure_validation.service.status',return_value={
                'running':False,'identity_verified':False,'status':'identity_mismatch'}), \
             patch('regen.tools.contract_closure_validation.service.stop') as stop:
            with self.assertRaisesRegex(RuntimeError,'identity unverified'):
                stop_pair(self.arms,sha(self.arms/'preparation.json'))
            stop.assert_not_called()

    def test_unverified_terminal_blocks_second_arm(self):
        result={'final_service':{'running':False},'cleanup_error':None,
                'window_result_verified':False,'reason':'observer_error'}
        with patch('regen.tools.application_release.verify'), \
             patch('regen.tools.contract_closure_validation.service.start') as start, \
             patch('regen.tools.contract_closure_validation.observe',return_value=result), \
             patch('regen.tools.contract_closure_validation.native_exit_gate',return_value={'all_exited':True}):
            with self.assertRaisesRegex(RuntimeError,'verified normal terminal'):
                run_pair(self.arms,self.root/'comparison',self.manifest)
        start.assert_called_once_with(self.arms/'baseline')

    def test_missing_baseline_container_allows_variant_and_binds_budget(self):
        name='regen-isabelle-build-123-'+'a'*32
        def launch(root):
            (root/'service-launch.json').write_text('{}')
            (root/'service-invocation.json').write_text(json.dumps({'main_pid':123}))
            (root/'service-runner.log').write_text('[regen-event] '+json.dumps({
                'event':'isabelle_build_started','container':name})+'\n')
        for budget in (3600,7200):
            with self.subTest(budget=budget), ExitStack() as stack:
                # Separate output artifacts while preserving the prepared test states.
                for arm in self.arms.iterdir():
                    if arm.is_dir() and (arm/'experiment.json').exists():
                        (arm/'experiment.json').unlink()
                stack.enter_context(patch('regen.tools.application_release.verify'))
                start=stack.enter_context(patch('regen.tools.contract_closure_validation.service.start',side_effect=launch))
                observer=stack.enter_context(patch('regen.tools.contract_closure_validation.observe',side_effect=lambda *a,**kw: {
                    'final_service':{'running':False},'cleanup_error':None,
                    'window_result_verified':True,'reason':'trial_effective_budget_exhausted'}))
                stack.enter_context(patch('regen.tools.contract_closure_validation.subprocess.run',return_value=SimpleNamespace(
                    returncode=1,stderr='error: no such object: '+name)))
                options={} if budget==3600 else {'budget_effective_seconds':7200,'wall_limit':14400}
                result=run_pair(self.arms,self.root/str(budget),self.manifest,**options)
                self.assertTrue(result['comparison_complete'])
                self.assertEqual([call.args[0].name for call in start.call_args_list],['baseline','helper-closure-v1'])
                for call in observer.call_args_list:
                    self.assertEqual(call.kwargs['budget_effective_seconds'],budget)
                    self.assertEqual(call.kwargs['wall_limit'],14400 if budget==7200 else 10800)
                for row in result['arms']:
                    experiment=json.loads((self.arms/row['strategy']/'experiment.json').read_text())
                    self.assertEqual(experiment['diagnostic_window_effective_seconds'],budget)

    def test_real_gate_failure_blocks_variant_and_persists_primary_error(self):
        def launch(root):
            (root/'service-launch.json').write_text('{}')
        old=signal.getsignal(signal.SIGTERM)
        with patch('regen.tools.application_release.verify'), \
             patch('regen.tools.contract_closure_validation.service.start',side_effect=launch) as start, \
             patch('regen.tools.contract_closure_validation.service.status',return_value={'running':True}), \
             patch('regen.tools.contract_closure_validation.service.stop',side_effect=OSError('stop unavailable')), \
             patch('regen.tools.contract_closure_validation.observe',return_value={
                 'final_service':{'running':False},'cleanup_error':None,
                 'window_result_verified':True,'reason':'first_new_contract'}), \
             patch('regen.tools.contract_closure_validation.native_exit_gate',side_effect=[
                 RuntimeError('inspection permission denied'),RuntimeError('cleanup daemon unavailable')]) as gate:
            with self.assertRaisesRegex(RuntimeError,'inspection permission denied'):
                run_pair(self.arms,self.root/'comparison',self.manifest)
        self.assertEqual(gate.call_count,2)
        start.assert_called_once_with(self.arms/'baseline')
        report=json.loads((self.root/'comparison/report.json').read_text())
        self.assertFalse(report['comparison_complete'])
        self.assertEqual(report['error']['message'],'inspection permission denied')
        self.assertEqual([r['stage'] for r in report['cleanup_errors']],['service_stop','native_exit_gate'])
        self.assertEqual(len(report['arms']),1)
        self.assertEqual(signal.getsignal(signal.SIGTERM),old)


class ObserverBudgetTests(unittest.TestCase):
    def test_configured_budget_reaches_clock_and_first_terminal_edge(self):
        for budget in (3600,7200):
            with self.subTest(budget=budget), tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
                root=Path(temporary)/'arm'
                root.mkdir()
                (root/'checkpoint.json').write_text('{}')
                (root/'service-launch.json').write_text('{}')
                stack.enter_context(patch('regen.tools.contract_closure_validation.accepted_ids',return_value=set()))
                stack.enter_context(patch('regen.tools.contract_closure_validation.service.status',return_value={
                    'running':True,'identity_verified':True,'status':'running'}))
                stop=stack.enter_context(patch('regen.tools.contract_closure_validation.service.stop'))
                stack.enter_context(patch('regen.tools.contract_closure_validation.clock.ingest'))
                stack.enter_context(patch('regen.tools.contract_closure_validation.watch.scan'))
                evaluate=stack.enter_context(patch('regen.tools.contract_closure_validation.clock.evaluate',side_effect=[
                    {'theories':{'T':{'effective_seconds':budget-1}}},
                    {'theories':{'T':{'effective_seconds':budget}}},
                    {'theories':{'T':{'effective_seconds':budget}}}]))
                sleep=stack.enter_context(patch('regen.tools.contract_closure_validation.time.sleep'))
                options={} if budget==3600 else {'budget_effective_seconds':7200,'wall_limit':14400}
                report=observe(root,Path(temporary)/'output','T',**options)
                self.assertEqual(report['reason'],'trial_effective_budget_exhausted')
                self.assertEqual(report['budget_effective_seconds'],budget)
                self.assertIn(str(budget),report['window_policy'])
                self.assertEqual([call.args[3] for call in evaluate.call_args_list],[budget]*3)
                sleep.assert_called_once_with(5)
                stop.assert_called_once_with(root)

    def test_observer_original_error_survives_cleanup_status_failure(self):
        with tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
            root=Path(temporary)/'arm'
            root.mkdir()
            (root/'checkpoint.json').write_text('{}')
            (root/'service-launch.json').write_text('{}')
            stack.enter_context(patch('regen.tools.contract_closure_validation.accepted_ids',return_value=set()))
            stack.enter_context(patch('regen.tools.contract_closure_validation.service.status',side_effect=[
                ValueError('original observer failure'),RuntimeError('cleanup failure'),RuntimeError('final status failure')]))
            stack.enter_context(patch('regen.tools.contract_closure_validation.clock.ingest'))
            stack.enter_context(patch('regen.tools.contract_closure_validation.watch.scan'))
            stack.enter_context(patch('regen.tools.contract_closure_validation.clock.evaluate',return_value={}))
            with self.assertRaisesRegex(ValueError,'original observer failure'):
                observe(root,Path(temporary)/'output','T')
            report=json.loads((Path(temporary)/'output/report.json').read_text())
            self.assertEqual(report['error']['message'],'original observer failure')
            self.assertEqual(report['cleanup_error']['message'],'cleanup failure')
            self.assertFalse(report['window_result_verified'])
