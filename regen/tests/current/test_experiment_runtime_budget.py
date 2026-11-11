"""Unified runtime: measured outage unions, late evidence and durable cutoffs."""
from copy import deepcopy
import tempfile
import unittest

from regen.execution.check_tasks import CheckCancelled
from regen.infrastructure.experiment_ledger import Ledger
from regen.infrastructure.experiment_runtime_budget import ExperimentRuntimeBudget, correct_stopped_budget
from regen.tests.current.test_normal_native_interruptions import BINDING, receipt
from regen.tests.current import test_normal_runtime_handoff as legacy_handoff
from regen.infrastructure.experiment_runtime_budget import POLICY, MODE


class UnifiedRuntimeHandoffTests(legacy_handoff.NormalRuntimeHandoffTests):
    budget_class=ExperimentRuntimeBudget
    clock_policy=POLICY
    clock_mode=MODE
    budget_target='regen.infrastructure.experiment_runtime_budget.ExperimentRuntimeBudget'


class ExperimentRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.now=0.;self.ledger=Ledger(self.tmp.name,'unified',clock=lambda:self.now)
        self.stage=ExperimentRuntimeBudget(self.ledger,1000,monotonic=lambda:self.now)
        self.addCleanup(self.stage.close)
        self.stage.register({'A':['a'],'B':['b']});self.stage.select('A',worker_id='A')

    def state(self):return self.stage.snapshot()

    def test_every_normal_phase_and_native_timeout_counts(self):
        for phase in ('prefix_preparation','provider_build','seal','integration'):
            with self.stage.excluded(phase):self.now+=10
        self.stage.begin_native_request('A','n',BINDING);self.now+=10
        self.stage.finish_native_request('A','n','timeout')
        self.assertEqual(self.state()['effective_seconds'],50)
        with self.assertRaises(ValueError):
            self.stage.confirm_interruption('native','n',receipt('n'),evidence_id='bad')

    def test_cold_native_checks_without_worker_identity_still_count(self):
        self.stage.begin_native_request('A','cold',{'prefix_sha256':'a'*64,'request_sha256':'b'*64})
        self.now=10;self.stage.finish_native_request('A','cold','completed')
        self.assertEqual(self.state()['effective_seconds'],10)

    def test_provider_only_lost_wait_suffix_is_refunded_without_successful_retry(self):
        self.now=5;self.stage.begin_provider_request('A','p');self.now=15
        result=self.stage.finish_provider_request('A','p',outcome='failed',
            failure={'kind':'http_connection'},waiting_seconds=4)
        self.assertEqual(result['refund_seconds'],4)
        self.assertEqual(self.state()['effective_seconds'],11)
        self.assertEqual(self.state()['excluded_experiment_seconds'],4)

    def test_slow_reasoning_and_unclassified_failure_remain_charged(self):
        self.stage.begin_provider_request('A','p');self.now=100
        self.stage.finish_provider_request('A','p',outcome='failed',
            failure={'kind':'no_complete_response'},waiting_seconds=100)
        self.assertEqual(self.state()['effective_seconds'],100)

    def test_network_and_native_overlap_retains_useful_work_and_never_double_refunds(self):
        self.stage.begin_native_request('A','n',BINDING);self.stage.begin_provider_request('A','p')
        self.now=20
        self.stage.finish_provider_request('A','p',outcome='failed',failure={'kind':'transport'},waiting_seconds=15)
        self.assertEqual(self.state()['effective_seconds'],20)
        self.stage.finish_native_request('A','n','watchdog',interruption_evidence=receipt('n'))
        self.assertEqual(self.state()['effective_seconds'],5)
        again=self.stage.confirm_interruption('native','n',receipt('n'),evidence_id='native:n')
        self.assertEqual(again['refund_seconds'],0)
        self.assertEqual(self.state()['excluded_experiment_seconds'],15)

    def test_healthy_parallel_worker_keeps_global_wall_time(self):
        self.stage.select('B',worker_id='B');self.stage.begin_native_request('A','n',BINDING)
        self.stage.begin_provider_request('B','p');self.now=20
        self.stage.finish_native_request('A','n','watchdog',interruption_evidence=receipt('n'))
        self.assertEqual(self.state()['effective_seconds'],20)
        self.stage.finish_provider_request('B','p',outcome='failed',failure={'kind':'app_server_eof'},waiting_seconds=20)
        self.assertEqual(self.state()['effective_seconds'],0)
        self.assertEqual(self.state()['worker_seconds'],0)

    def test_known_pause_and_late_worker_fault_are_a_union(self):
        self.stage.begin_native_request('A','n',BINDING);self.now=10
        h=self.stage.begin_exclusion('experiment_pause',confirmed=True,evidence_id='pause')
        self.now=20;self.stage.end_exclusion(h);self.now=30
        self.stage.finish_native_request('A','n','watchdog',interruption_evidence=receipt('n'))
        self.assertEqual(self.state()['effective_seconds'],0)
        self.assertEqual(self.state()['excluded_experiment_seconds'],30)

    def test_actual_model_overlap_during_native_wait_is_retained(self):
        self.stage.begin_native_request('A','n',BINDING);self.now=10
        self.stage.set_model_overlap(True,worker_id='A');self.now=20
        self.stage.set_model_overlap(False,worker_id='A');self.now=30
        self.stage.finish_native_request('A','n','watchdog',interruption_evidence=receipt('n'))
        self.assertEqual(self.state()['effective_seconds'],10)

    def test_invalid_native_receipt_is_retained_not_a_new_clock_failure(self):
        self.stage.begin_native_request('A','n',BINDING);self.now=10
        self.stage.finish_native_request('A','n','watchdog',
            interruption_evidence=receipt('n',response_missing=False))
        self.assertEqual(self.state()['effective_seconds'],10)
        self.assertFalse(self.stage.event.is_set())

    def test_late_evidence_after_pause_close_and_resume_survives_replay(self):
        self.stage.begin_native_request('A','n',BINDING);self.now=30
        self.stage.finish_native_request('A','n','watchdog');self.stage.cancel('operator_pause');self.stage.close()
        self.now=100
        result=self.stage.confirm_interruption('native','n',receipt('n'),evidence_id='late-native')
        self.assertEqual(result['refund_seconds'],30)
        self.assertEqual(self.state()['experiment_wall_seconds'],100)
        resumed=ExperimentRuntimeBudget(self.ledger,1000,monotonic=lambda:self.now,resume_authorization='resume')
        self.addCleanup(resumed.close)
        self.assertEqual(resumed.snapshot()['effective_seconds'],0)
        self.assertEqual(resumed.confirm_interruption('native','n',receipt('n'),evidence_id='late-native')['refund_seconds'],0)

    def test_budget_cutoff_pauses_and_stopped_late_correction_can_restore_allowance(self):
        self.stage.seconds=10;self.stage.base_seconds=10
        self.stage.begin_native_request('A','n',BINDING);self.now=12
        self.stage.finish_native_request('A','n','watchdog')
        with self.assertRaises(CheckCancelled):self.stage.check()
        self.assertFalse(self.stage.finished);self.stage.close();self.now=100
        adjusted=correct_stopped_budget(self.ledger,'native','n',receipt('n'),evidence_id='audit-late')
        self.assertEqual(adjusted['refund_seconds'],12)
        resumed=ExperimentRuntimeBudget(self.ledger,10,monotonic=lambda:self.now,resume_authorization='resume')
        self.addCleanup(resumed.close)
        self.assertEqual(resumed.snapshot()['remaining_seconds'],10)
        self.assertEqual(resumed.snapshot()['excluded_experiment_seconds'],100)

    def test_changed_evidence_and_unknown_request_cannot_refund(self):
        self.stage.begin_provider_request('A','p');self.now=10
        self.stage.finish_provider_request('A','p',outcome='failed')
        evidence={'failure':{'kind':'transport'},'waiting_seconds':5}
        self.stage.confirm_interruption('provider','p',evidence,evidence_id='audit')
        before=self.state()['effective_seconds']
        for uid,body in [('p',{**evidence,'waiting_seconds':9}),('unknown',evidence)]:
            with self.assertRaises(ValueError):
                self.stage.confirm_interruption('provider',uid,body,evidence_id='audit')
        self.assertEqual(self.state()['effective_seconds'],before)

    def test_ordinary_phase_snapshots_do_not_duplicate_full_timeline(self):
        self.stage.begin_provider_request('A','p')
        for tick in range(1,100):self.now=tick;self.stage.snapshot()
        self.stage.finish_provider_request('A','p',outcome='success')
        segments=[r for r in self.ledger.rows if r['kind']=='experiment_runtime_segment']
        self.assertEqual(len(segments),1)
        self.assertNotIn('segments',self.stage._compact())

    def test_serial_planning_outage_refunds_planning_cost(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        budget=ExperimentRuntimeBudget(Ledger(tmp.name,'planning',clock=lambda:self.now),1000,monotonic=lambda:self.now)
        self.addCleanup(budget.close);budget.begin_planning();budget.begin_provider_request(None,'p')
        self.now=10;budget.finish_provider_request(None,'p',outcome='failed',failure={'kind':'transport'},waiting_seconds=6)
        self.assertEqual(budget.snapshot()['planning_seconds'],4)
        self.assertEqual(budget.snapshot()['effective_seconds'],4)

    def test_real_session_failure_credits_only_wait_after_last_model_feedback(self):
        from unittest.mock import patch
        from regen.infrastructure.codex_workspace_session import CodexWorkspaceSession, ProviderFeedbackError
        from regen.infrastructure.parallel_generation_budget import ParallelGenerationBudgetBridge
        bridge=ParallelGenerationBudgetBridge(self.stage);self.addCleanup(bridge.close)
        session=CodexWorkspaceSession(tools=[{'name':'status','description':'fixture','inputSchema':{'type':'object'}}],feedback_retry_mode=True)
        session.thread_id='t';session.turn_id='u'
        with patch('regen.infrastructure.codex_workspace_session.time.monotonic',side_effect=lambda:self.now),bridge.worker('A'):
            session._generation_start();session._provider_request_begin('turn_start')
            self.now=10;session._model_work_notification('item/reasoning/textDelta',{'threadId':'t','turnId':'u'})
            self.now=30
            with self.assertRaises(ProviderFeedbackError):session._raise_feedback_failure({'kind':'http_connection'},source='fixture_eof')
            self.assertEqual(self.state()['effective_seconds'],10)
            session._generation_stop('ProviderFeedbackError');self.now=60
            self.assertEqual(self.state()['effective_seconds'],10)
            self.assertEqual(self.state()['excluded_experiment_seconds'],50)

    def test_session_budget_admission_rejection_preserves_cutoff_during_cleanup(self):
        from regen.infrastructure.codex_workspace_session import CodexWorkspaceSession
        from regen.infrastructure.parallel_generation_budget import ParallelGenerationBudgetBridge
        bridge=ParallelGenerationBudgetBridge(self.stage);self.addCleanup(bridge.close)
        session=CodexWorkspaceSession(tools=[{'name':'status','description':'fixture','inputSchema':{'type':'object'}}],feedback_retry_mode=True)
        self.stage.seconds=10;self.stage.base_seconds=10
        with bridge.worker('A'):
            session._generation_start();self.now=12
            with self.assertRaises(CheckCancelled):session._provider_request_begin('turn_start')
            self.assertIsNone(session._provider_request_id)
            session._generation_stop('CheckCancelled',handler_stop=True)
        self.assertEqual(self.state()['reason'],'experiment_runtime_budget_reached')
        self.assertFalse(self.stage.runtime_intervals.requests)

    def test_service_reports_unified_cutoff_as_a_recoverable_pause(self):
        import json
        from pathlib import Path
        from regen.tools.parallel_workspace_service import _effective_budget_status
        from regen.execution.proof_commits import digest
        root=Path(self.tmp.name)/'service';root.mkdir()
        run={'run_id':'service-test'};(root/'run.json').write_text(json.dumps(run))
        ledger=Ledger(root/'effective-generation-budget',run['run_id'],clock=lambda:self.now)
        ledger.append('parallel_budget_binding',run_sha256=digest(run))
        stage=ExperimentRuntimeBudget(ledger,10,monotonic=lambda:self.now)
        self.addCleanup(stage.close)
        ledger.append('parallel_budget_invocation',service_invocation_id='current')
        self.now=11
        with self.assertRaises(CheckCancelled):stage.check()
        stage.close()
        value=_effective_budget_status(root,'current')
        self.assertEqual(value['clock_policy'],POLICY)
        self.assertEqual(value['reason'],'experiment_runtime_budget_reached')
        self.assertTrue(value['resume_requires_authorization'])
        self.assertIsNone(_effective_budget_status(root,'other'))

    def test_wall_clock_does_not_require_model_message_completion(self):
        from regen.infrastructure.parallel_generation_budget import ParallelGenerationBudgetBridge
        bridge=ParallelGenerationBudgetBridge(self.stage);self.addCleanup(bridge.close)
        self.now=10;bridge.handle('generation_clock_untrusted',{'theory':'A','reason':'unclosed commentary'})
        self.assertFalse(self.stage.event.is_set())
        self.assertEqual(self.state()['effective_seconds'],10)

    def test_stream_retry_correction_is_idempotent_after_successful_completion(self):
        self.stage.begin_provider_request('A','p');self.now=10
        evidence={'failure':{'kind':'response_stream'},'waiting_seconds':7}
        self.stage.confirm_interruption('provider','p',evidence,evidence_id='stream')
        self.now=20;self.stage.finish_provider_request('A','p',outcome='success')
        again=self.stage.confirm_interruption('provider','p',evidence,evidence_id='stream')
        self.assertEqual(again['refund_seconds'],0)
        self.assertEqual(self.state()['effective_seconds'],13)

    def test_planning_model_error_text_cannot_claim_network_credit(self):
        from regen.infrastructure.planning_retry import retry_planning_request
        from regen.infrastructure.progress_events import observe_events
        events=[]
        def invalid(_):raise ValueError('model returned invalid text saying connection refused')
        with observe_events(lambda event,fields:events.append((event,fields))):
            with self.assertRaises(ValueError):retry_planning_request(invalid)
        finished=next(f for e,f in events if e=='planning_request_finished')
        self.assertNotIn('failure',finished)
        self.assertNotIn('waiting_seconds',finished)

    def test_stopped_audit_cannot_change_live_or_other_clock(self):
        self.stage.begin_native_request('A','n',BINDING);self.now=10
        self.stage.finish_native_request('A','n','watchdog')
        with self.assertRaisesRegex(ValueError,'stopped'):
            correct_stopped_budget(self.ledger,'native','n',receipt('n'),evidence_id='bad-live')

    def test_unfinished_network_window_is_not_refunded_just_for_a_successful_retry(self):
        self.stage.begin_provider_request('A','old');self.now=10
        self.stage.finish_provider_request('A','old',outcome='failed')
        self.stage.begin_provider_request('A','retry');self.now=20
        self.stage.finish_provider_request('A','retry',outcome='success')
        self.assertEqual(self.stage.credit_provider_request('old','retry')['refund_seconds'],0)
        self.assertEqual(self.state()['effective_seconds'],20)
