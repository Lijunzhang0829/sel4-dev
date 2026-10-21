"""No model/native calls: exercise the parallel global-budget boundaries."""
from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from regen.execution.check_tasks import CheckCancelled
from regen.infrastructure.experiment_ledger import Ledger
from regen.infrastructure.parallel_generation_budget import (
    ParallelGenerationBudgetBridge, initialize_parallel_budget)
from regen.infrastructure.progress_events import emit_event


class ParallelEffectiveBudgetTests(unittest.TestCase):
    def setUp(self):
        from regen.infrastructure.effective_time_budget import EffectiveTimeBudget
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.now = [0.]
        self.stage = EffectiveTimeBudget(Ledger(self.root/'clock', 'test'),
            seconds=100, monotonic=lambda: self.now[0])
        self.stage.register({'A': ['a'], 'B': ['b']})
        self.bridge = ParallelGenerationBudgetBridge(self.stage)
        self.addCleanup(self.stage.close)
        self.addCleanup(self.bridge.close)

    def event(self, event, theory, **fields):
        self.bridge.handle(event, {'theory': theory, **fields})

    def elapsed(self):
        return self.stage.snapshot()['effective_seconds']

    def test_two_workers_charge_union_not_sum_and_one_stop_keeps_other(self):
        self.event('theory_generation_started', 'A')
        self.now[0] = 10
        self.event('theory_generation_started', 'B')
        self.now[0] = 30
        self.event('theory_generation_stopped', 'A')
        self.now[0] = 50
        self.assertEqual(self.elapsed(), 50)
        self.event('theory_generation_stopped', 'B')
        self.now[0] = 90
        self.assertEqual(self.elapsed(), 50)

    def test_network_pause_keeps_consumed_union_and_resume_does_not_charge_downtime(self):
        from regen.infrastructure.effective_time_budget import EffectiveTimeBudget
        self.event('theory_generation_started', 'A')
        self.event('theory_generation_started', 'B')
        self.now[0]=20
        self.event('generation_network_pause_requested','A',thread_id='t',turn_id='u',
                   active_request_id='check-9',callback_executing=True)
        self.assertEqual(self.stage.reason,'provider_network_failure')
        self.now[0]=200
        self.assertEqual(self.elapsed(),20)
        self.stage.close()
        resumed=EffectiveTimeBudget(self.stage.ledger,seconds=100,
            resume_authorization='test',monotonic=lambda:self.now[0])
        self.addCleanup(resumed.close)
        self.now[0]=1000
        self.assertEqual(resumed.snapshot()['effective_seconds'],20)
        self.assertEqual(resumed.snapshot()['worker_seconds'],40)
        resumed.select('A',worker_id='A');self.now[0]=1005
        self.assertEqual(resumed.snapshot()['effective_seconds'],25)

    def test_legacy_no_retry_mode_terminal_network_failure_pauses_without_fabricated_uncertainty(self):
        from regen.infrastructure.codex_workspace_session import CodexWorkspaceSession, SessionCancelled
        session=CodexWorkspaceSession(tools=[{'name':'query','description':'test',
                                            'inputSchema':{'type':'object'}}])
        session.thread_id='t';session.turn_id='u';session._cancel_event=self.stage.event
        with redirect_stdout(io.StringIO()), self.bridge.worker('A'):
            session._generation_start()
            session._model_work_items.add(('t','u','reasoning'))
            self.now[0]=10
            with self.assertRaises(SessionCancelled):
                session._notification({'method':'error','params':{'threadId':'t','turnId':'u',
                    'willRetry':False,'error':{'codexErrorInfo':'responseStreamDisconnected'}}})
            self.assertTrue(self.stage.event.is_set())
            session._generation_stop('SessionCancelled')
        self.assertEqual(self.stage.reason,'provider_network_failure')
        self.assertEqual(self.elapsed(),10)
        kinds=[row['kind'] for row in self.stage.ledger.rows]
        self.assertIn('generation_items_interrupted',kinds)
        self.assertNotIn('generation_clock_untrusted',kinds)
        self.assertFalse(self.bridge.phases)

    def test_worker_exclusion_does_not_hide_peer_and_overlaps_union(self):
        self.event('theory_generation_started', 'A')
        self.event('theory_generation_started', 'B')
        self.now[0] = 10
        self.event('generation_exclusion_started', 'A', phase_id='p', category='prefix_preparation')
        self.now[0] = 20
        self.event('generation_exclusion_started', 'B', phase_id='q', category='provider_build')
        self.now[0] = 30
        self.event('generation_exclusion_finished', 'A', phase_id='p')
        self.now[0] = 40
        self.event('generation_exclusion_finished', 'B', phase_id='q')
        self.assertEqual(self.elapsed(), 30)

    def test_observed_model_work_overrides_only_own_native_exclusion(self):
        self.event('theory_generation_started', 'A')
        self.event('generation_exclusion_started', 'A', phase_id='p', category='prefix_preparation')
        self.now[0] = 10
        self.event('model_work_observed', 'A')
        self.now[0] = 25
        self.event('model_work_observation_finished', 'A')
        self.now[0] = 40
        self.event('generation_exclusion_finished', 'A', phase_id='p')
        self.assertEqual(self.elapsed(), 15)

    def test_worker_scope_supplies_theory_to_raw_native_and_transport_events(self):
        with redirect_stdout(io.StringIO()), self.bridge.worker('A'):
            emit_event('theory_generation_started')
            self.now[0] = 10
            emit_event('generation_exclusion_started', phase_id='p', category='statement_preflight')
            self.now[0] = 30
            emit_event('generation_exclusion_finished', phase_id='p')
            self.now[0] = 40
        self.now[0] = 80
        self.assertEqual(self.elapsed(), 20)

    def test_missing_or_cross_theory_event_fails_closed(self):
        with self.assertRaisesRegex(ValueError, 'registered theory'):
            self.bridge.handle('theory_generation_started', {})
        self.assertTrue(self.stage.event.is_set())

    def test_unclosed_worker_phase_cannot_silently_mask_its_retry(self):
        with redirect_stdout(io.StringIO()), self.bridge.worker('A'):
            emit_event('theory_generation_started')
            emit_event('generation_exclusion_started', phase_id='p', category='prefix_preparation')
        self.assertTrue(self.stage.event.is_set())
        self.assertFalse(self.bridge.phases)

    def test_publication_after_budget_is_rejected_before_write(self):
        self.event('theory_generation_started', 'A')
        self.now[0] = 101
        record = {'commit_id': 'c', 'state': {'graph': {'theory': 'A'},
                  'proofs': {'a': 'by simp'}, 'status': 'generating'}}
        wrote = False
        with self.assertRaises(CheckCancelled):
            with self.bridge.acceptance(record):
                wrote = True
        self.assertFalse(wrote)
        self.assertFalse(self.stage.theories['A']['green_contract_ids'])

    def test_green_publication_records_once_and_failed_publication_records_nothing(self):
        self.event('theory_generation_started', 'A')
        record = {'commit_id': 'c', 'state': {'graph': {'theory': 'A'},
                  'proofs': {'a': 'by simp'}, 'status': 'generating'}}
        with self.assertRaises(OSError):
            with self.bridge.acceptance(record):
                raise OSError('write failed')
        self.assertFalse(self.stage.theories['A']['green_contract_ids'])
        with self.bridge.acceptance(record):
            pass
        with self.bridge.acceptance(record):
            pass
        self.assertEqual(self.stage.theories['A']['green_contract_ids'], {'a'})
        rows = [r for r in self.stage.ledger.rows if r['kind'] == 'time_budget_green']
        self.assertEqual(len(rows), 1)

    def test_integration_has_explicit_global_non_generation_scope(self):
        with redirect_stdout(io.StringIO()), self.bridge.integration():
            emit_event('generation_exclusion_started', phase_id='p', category='provider_build')
            self.now[0] = 80
            emit_event('generation_exclusion_finished', phase_id='p')
        self.assertEqual(self.elapsed(), 0)

    def test_exit_settles_generation_before_closing_cancel_event(self):
        from regen.planning.parallel_file_planner import _pause_budget_for_exit
        self.event('theory_generation_started', 'A')
        self.now[0] = 17
        _pause_budget_for_exit(self.stage, KeyboardInterrupt('operator'))
        self.assertEqual(self.elapsed(), 17)
        self.assertEqual(self.stage.reason, 'operator_pause')
        self.now[0] = 99
        self.assertEqual(self.elapsed(), 17)


class ParallelBudgetBindingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.overview = SimpleNamespace(run={'run_id': 'test'},
            list_workspace=lambda: {'theories': [{'theory': 'A',
                'contracts': [{'id': 'a'}], 'status': 'pending', 'green_count': 0}]})

    def test_new_run_binds_digest_without_proof_text(self):
        stage, bridge = initialize_parallel_budget(self.root, self.overview, {'model': 'fixture'})
        self.addCleanup(stage.close)
        self.addCleanup(bridge.close)
        self.assertEqual(stage.snapshot()['seconds'], 172800)
        binding = stage.ledger.rows[0]
        self.assertEqual(binding['kind'], 'parallel_budget_binding')
        self.assertEqual(set(binding['data']), {'policy', 'run_sha256', 'contracts_sha256', 'configuration_sha256'})

    def test_legacy_session_requires_audited_migration_before_zero_clock(self):
        path = self.root/'parallel-sessions'/'old'/'A'/'session-transcript.jsonl'
        path.parent.mkdir(parents=True)
        path.touch()
        with self.assertRaisesRegex(ValueError, 'audited accounting migration'):
            initialize_parallel_budget(self.root, self.overview, {})
        self.assertFalse((self.root/'effective-generation-budget'/'events').exists())

    def test_continuation_without_clock_cannot_reset_budget(self):
        (self.root/'workspace-continuation.json').touch()
        with self.assertRaisesRegex(ValueError, 'never resume from zero'):
            initialize_parallel_budget(self.root, self.overview, {})

    def test_changed_configuration_is_rejected(self):
        stage, bridge = initialize_parallel_budget(self.root, self.overview, {'model': 'one'})
        bridge.close()
        stage.close()
        with self.assertRaisesRegex(ValueError, 'binding changed'):
            initialize_parallel_budget(self.root, self.overview, {'model': 'two'},
                                       resume_authorization='user-approved')

    def test_cli_default_and_explicit_resume_extension_are_forwarded(self):
        from regen.execution.proof_session_runner import main
        for extra, expected in (([], (172800, None, None)),
                (['--effective-seconds', '3600', '--resume-authorization', 'approved',
                  '--extend-effective-seconds', '120'], (3600, 'approved', 120))):
            with patch('sys.argv', ['runner', 'parallel-workspace', '--run-dir', str(self.root),
                    '--allow-external-call', *extra]), redirect_stdout(io.StringIO()), \
                    patch('regen.planning.parallel_file_planner.run_parallel_workspace',
                          return_value={'status': 'partial'}) as run:
                self.assertEqual(main(), 1)
            kwargs = run.call_args.kwargs
            self.assertEqual((kwargs['effective_seconds'], kwargs['resume_authorization'],
                              kwargs['extend_effective_seconds']), expected)

    def test_cli_budget_pause_is_not_reported_as_runner_failure(self):
        from regen.execution.proof_session_runner import main
        error = CheckCancelled('effective_time_budget_reached')
        error.regen_pause_result = {'status':'paused', 'reason':'effective_time_budget_reached'}
        with patch('sys.argv', ['runner', 'parallel-workspace', '--run-dir', str(self.root),
                '--allow-external-call']), redirect_stdout(io.StringIO()), \
                patch('regen.planning.parallel_file_planner.run_parallel_workspace', side_effect=error), \
                patch('regen.execution.proof_session_runner.emit_event') as emit:
            self.assertEqual(main(), 2)
        self.assertEqual(emit.call_args.args[0], 'runner_paused')

    def test_service_manifest_accepts_exact_budget_flags(self):
        from regen.tools import parallel_workspace_service as service
        command = [sys.executable, '-m', 'regen.execution.proof_session_runner',
            'parallel-workspace', '--run-dir', str(self.root), '--effective-seconds', '172800',
            '--resume-authorization', 'approved', '--extend-effective-seconds', '3600']
        (self.root/'experiment.json').write_text(json.dumps({'command':command, 'cwd':str(self.root)}))
        self.assertEqual(service._manifest(self.root)[0]['command'], command)

    def test_service_pause_receipt_must_match_latest_invocation(self):
        from regen.tools import parallel_workspace_service as service
        from regen.infrastructure.effective_time_budget import EffectiveTimeBudget
        now = [0.]
        def create(ledger, **kwargs):
            return EffectiveTimeBudget(ledger, monotonic=lambda: now[0], **kwargs)
        with patch('regen.infrastructure.effective_time_budget.EffectiveTimeBudget', side_effect=create):
            stage, bridge = initialize_parallel_budget(self.root, self.overview, {}, seconds=100)
        (self.root/'run.json').write_text(json.dumps(self.overview.run))
        stage.ledger.append('parallel_budget_invocation', service_invocation_id='same', pid=1)
        stage.select('A', worker_id='A')
        now[0] = 101
        with self.assertRaises(CheckCancelled):
            stage.check()
        stage.close()
        value = service._effective_budget_status(self.root, 'same')
        self.assertEqual(value['reason'], 'effective_time_budget_reached')
        self.assertIsNone(service._effective_budget_status(self.root, 'different'))
        self.assertIsNone(service._effective_budget_status(self.root, None))


class ParallelBudgetRunnerTests(unittest.TestCase):
    def test_only_verified_integration_finishes_other_results_pause_recoverably(self):
        from regen.planning import parallel_file_planner as planner
        from regen.infrastructure.effective_time_budget import EffectiveTimeBudget
        cases = [('generating', False, None, 'partial'),
                 ('sealed', False, None, 'selected_theories_sealed'),
                 ('sealed', True, False, 'integration_failed'),
                 ('sealed', True, True, 'repository_verified')]
        for status, integrate, verified, expected in cases:
            with self.subTest(status=expected), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                binary = root/'codex'; binary.write_text('not executed')
                class Overview:
                    selected = ['A']
                    run = {'run_id':'test', 'execution_input_kind':'refine-dataset',
                        'checker_context_root':str(root), 'checker_context_id':'fixture'}
                    options = {'model':'fixture','call_timeout':900,'check_timeout':900,'candidate_timeout':120}
                    def __init__(self, *args, **kwargs): pass
                    def list_workspace(self):
                        return {'theories':[{'theory':'A','contracts':['a'],'status':status,
                            'selected':True,'ready_for_proof_action':False}]}
                settings = {'codex_bin':str(binary),
                    'codex_runtime_root':str(root/'codex-runtime'),
                    'codex_sha256':hashlib.sha256(binary.read_bytes()).hexdigest()}
                with patch.object(planner, 'WorkspaceController', Overview), \
                        patch('regen.infrastructure.audited_llm.model_settings', return_value=settings), \
                        patch('regen.preparation.checker_context.load_context', return_value={'checker_context_id':'fixture'}), \
                        patch('regen.execution.repository_integration.integrate_repository',
                              return_value={'repository_verified':verified}), redirect_stdout(io.StringIO()):
                    result = planner.run_parallel_workspace(root, allow_external_call=True, integrate=integrate)
                self.assertEqual(result['status'], expected)
                self.assertEqual(result['disposition'], 'complete' if verified else 'pause')
                self.assertEqual(result['generation_budget']['finished'], bool(verified))
                ledger = Ledger(root/'effective-generation-budget', 'test')
                if verified:
                    with self.assertRaisesRegex(ValueError, 'completed'):
                        EffectiveTimeBudget(ledger, resume_authorization='resume')
                else:
                    self.assertTrue(result['generation_budget']['paused'])
                    restored = EffectiveTimeBudget(ledger, resume_authorization='resume')
                    restored.close()

    def test_outer_setup_exception_is_a_persisted_recoverable_pause(self):
        from regen.planning import parallel_file_planner as planner
        from regen.infrastructure.effective_time_budget import budget_journal_summary
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binary = root/'codex'; binary.write_text('not executed')
            class Overview:
                selected = ['A']
                run = {'run_id':'test','execution_input_kind':'refine-dataset',
                    'checker_context_root':str(root),'checker_context_id':'fixture'}
                options = {'model':'fixture','call_timeout':900,'check_timeout':900,'candidate_timeout':120}
                def __init__(self, *args, **kwargs): pass
                def list_workspace(self):
                    return {'theories':[{'theory':'A','contracts':['a'],'status':'pending',
                        'selected':True,'ready_for_proof_action':True}]}
            settings = {'codex_bin':str(binary),
                'codex_runtime_root':str(root/'codex-runtime'),
                'codex_sha256':hashlib.sha256(binary.read_bytes()).hexdigest()}
            with patch.object(planner, 'WorkspaceController', Overview), \
                    patch.object(planner, 'CheckExecutor', side_effect=RuntimeError('setup failed')), \
                    patch('regen.infrastructure.audited_llm.model_settings', return_value=settings), \
                    patch('regen.preparation.checker_context.load_context', return_value={'checker_context_id':'fixture'}), \
                    redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(RuntimeError, 'setup failed') as caught:
                    planner.run_parallel_workspace(root, allow_external_call=True)
            self.assertEqual(caught.exception.regen_pause_result['reason'], 'infrastructure_error')
            saved = budget_journal_summary(Ledger(root/'effective-generation-budget', 'test'))
            self.assertTrue(saved['paused'])
            self.assertFalse(saved['finished'])

    def test_live_worker_is_cancelled_and_pause_state_saved_without_native_or_model(self):
        from regen.planning import parallel_file_planner as planner
        from regen.infrastructure.effective_time_budget import EffectiveTimeBudget
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binary = root/'codex'
            binary.write_text('fixture, not executed')
            now = [0.]
            exited = threading.Event()
            class Overview:
                selected = ['A']
                run = {'run_id':'test', 'execution_input_kind':'refine-dataset',
                    'checker_context_root':str(root), 'checker_context_id':'fixture'}
                options = {'model':'fixture', 'call_timeout':900, 'check_timeout':900,
                           'candidate_timeout':120}
                def __init__(self, *args, **kwargs): pass
                def list_workspace(self):
                    return {'theories':[{'theory':'A','contracts':['a'], 'status':'pending',
                        'selected':True,'ready_for_proof_action':True}]}
            def create(ledger, **kwargs):
                return EffectiveTimeBudget(ledger, monotonic=lambda: now[0], poll_seconds=.005, **kwargs)
            def session(*args, cancel_event, **kwargs):
                try:
                    emit_event('theory_generation_started')
                    now[0] = 101
                    if not cancel_event.wait(3):
                        raise AssertionError('effective budget did not cancel the worker')
                    raise CheckCancelled('worker stopped')
                finally:
                    emit_event('theory_generation_stopped')
                    exited.set()
            settings = {'codex_bin':str(binary),
                'codex_runtime_root':str(root/'codex-runtime'),
                'codex_sha256':hashlib.sha256(binary.read_bytes()).hexdigest()}
            with patch.object(planner, 'WorkspaceController', Overview), \
                    patch.object(planner, 'run_theory_session', side_effect=session), \
                    patch('regen.infrastructure.effective_time_budget.EffectiveTimeBudget', side_effect=create), \
                    patch('regen.infrastructure.audited_llm.model_settings', return_value=settings), \
                    patch('regen.preparation.checker_context.load_context', return_value={'checker_context_id':'fixture'}), \
                    redirect_stdout(io.StringIO()):
                with self.assertRaises(CheckCancelled) as caught:
                    planner.run_parallel_workspace(root, allow_external_call=True,
                        integrate=False, effective_seconds=100)
            self.assertTrue(exited.is_set())
            self.assertEqual(caught.exception.regen_pause_result['status'], 'paused')
            self.assertEqual(caught.exception.regen_pause_result['generation_budget']['effective_seconds'], 101)
            receipt = next((root/'parallel-sessions').glob('*/interruption.json'))
            self.assertEqual(json.loads(receipt.read_text())['status'], 'paused')


if __name__ == '__main__':
    unittest.main()
