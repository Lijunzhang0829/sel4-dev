"""Normal-runtime accounting regressions; fake clocks never launch a model."""
import tempfile
import unittest
import io
from contextlib import redirect_stdout
from regen.infrastructure.experiment_ledger import Ledger
from regen.infrastructure.normal_runtime_budget import NormalRuntimeBudget


class NormalRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.now=0.
        self.ledger=Ledger(self.tmp.name,'normal',clock=lambda:self.now)
        self.budget=NormalRuntimeBudget(self.ledger,1000,monotonic=lambda:self.now)
        self.addCleanup(self.budget.close)

    def state(self):return self.budget.snapshot()

    def test_normal_phases_and_failed_requests_count(self):
        for phase in ('prefix_preparation','seal','provider_recovery'):
            with self.budget.excluded(phase):self.now+=10
        self.budget.begin_native_request('A','n',{})
        self.now+=10
        self.budget.finish_native_request('A','n','timeout')
        self.budget.begin_provider_request('A','p')
        self.now+=10
        self.budget.finish_provider_request('A','p',outcome='failed',failure='transport')
        self.budget.credit_provider_request('p','retry')
        self.assertEqual(self.state()['effective_experiment_seconds'],50)
        self.assertEqual(self.state()['excluded_experiment_seconds'],0)

    def test_global_exclusions_are_a_union(self):
        self.now=10
        a=self.budget.begin_exclusion('experiment_pause',confirmed=True,evidence_id='pause')
        self.now=20
        b=self.budget.begin_exclusion('provider_interruption',confirmed=True,evidence_id='http')
        self.now=30;self.budget.end_exclusion(a)
        self.now=40;self.budget.end_exclusion(b)
        self.now=50
        self.assertEqual(self.state()['effective_experiment_seconds'],20)
        self.assertEqual(self.state()['excluded_experiment_seconds'],30)
        self.assertEqual(self.state()['experiment_wall_seconds'],50)

    def test_all_lanes_must_be_blocked(self):
        self.budget.register({'A':['a'],'B':['b']})
        self.budget.select('A',worker_id='A');self.budget.select('B',worker_id='B')
        self.now=10;self.budget.confirm_provider_failure('A','failure-a')
        self.now=20;self.budget.confirm_provider_failure('B','failure-b')
        self.now=40;self.budget.provider_recovered('A')
        self.now=50
        self.assertEqual(self.state()['effective_experiment_seconds'],30)
        self.assertEqual(self.state()['excluded_experiment_seconds'],20)

    def test_evidence_required(self):
        with self.assertRaises(ValueError):
            self.budget.begin_exclusion('experiment_pause',confirmed=True)
        with self.assertRaises(ValueError):
            self.budget.begin_exclusion('seal',confirmed=True,evidence_id='not-outage')

    def test_real_eof_event_keeps_callback_charged_until_owner_stops(self):
        from regen.infrastructure.codex_workspace_session import CodexWorkspaceSession, ProviderFeedbackError
        from regen.infrastructure.parallel_generation_budget import ParallelGenerationBudgetBridge
        self.budget.register({'A':['a']})
        bridge=ParallelGenerationBudgetBridge(self.budget)
        self.addCleanup(bridge.close)
        session=CodexWorkspaceSession(tools=[{'name':'status','description':'test harness',
            'inputSchema':{'type':'object'}}],feedback_retry_mode=True)
        session.thread_id='thread';session.turn_id='turn'
        with redirect_stdout(io.StringIO()),bridge.worker('A'):
            session._generation_start()
            session._callback_executing=True
            self.now=10
            with self.assertRaises(ProviderFeedbackError):
                session._raise_feedback_failure({'kind':'app_server_eof','http_status':None},source='stdout_eof')
            failure=next(r for r in self.ledger.rows if r['kind']=='generation_provider_request_failed')
            self.assertTrue(failure['data']['evidence']['callback_executing'])
            self.now=20
            state=self.state()
            self.assertEqual(state['effective_experiment_seconds'],20)
            self.assertEqual(state['worker_seconds'],20)
            self.assertEqual(state['theories']['A']['effective_seconds'],20)
            session._callback_executing=False
            session._generation_stop('ProviderFeedbackError')
            self.now=50
            self.assertEqual(self.state()['effective_experiment_seconds'],20)
            bridge.handle('theory_generation_started',{'theory':'A'})
            self.now=60
            self.assertEqual(self.state()['effective_experiment_seconds'],30)
            self.assertEqual(self.state()['excluded_experiment_seconds'],30)

    def test_missing_callback_state_is_not_evidence_that_native_work_stopped(self):
        from regen.infrastructure.parallel_generation_budget import ParallelGenerationBudgetBridge
        self.budget.register({'A':['a']})
        bridge=ParallelGenerationBudgetBridge(self.budget)
        self.addCleanup(bridge.close)
        bridge.handle('theory_generation_started',{'theory':'A'})
        self.now=10
        bridge.handle('generation_provider_request_failed',{'theory':'A','request_id':'r',
            'failure':{'kind':'app_server_eof'}})
        self.now=20
        self.assertEqual(self.state()['effective_experiment_seconds'],20)
        bridge.handle('theory_generation_stopped',{'theory':'A'})
        self.now=50
        self.assertEqual(self.state()['effective_experiment_seconds'],20)

    def test_parallel_failure_does_not_remove_healthy_peer_work(self):
        from regen.infrastructure.parallel_generation_budget import ParallelGenerationBudgetBridge
        self.budget.register({'A':['a'],'B':['b']})
        bridge=ParallelGenerationBudgetBridge(self.budget)
        self.addCleanup(bridge.close)
        for theory in ('A','B'):bridge.handle('theory_generation_started',{'theory':theory})
        self.now=10
        bridge.handle('generation_provider_request_failed',{'theory':'A','request_id':'r',
            'failure':{'kind':'app_server_eof'},'callback_executing':True})
        self.now=20
        bridge.handle('theory_generation_stopped',{'theory':'A'})
        self.now=40
        state=self.state()
        self.assertEqual(state['effective_experiment_seconds'],40)
        self.assertEqual(state['worker_seconds'],60)
        self.assertEqual(state['theories']['A']['effective_seconds'],20)
        self.assertEqual(state['theories']['B']['effective_seconds'],40)

    def test_serial_http_failure_charges_callback_then_excludes_recovery(self):
        from regen.infrastructure.generation_activity_bridge import GenerationActivityBridge
        self.budget.register({'A':['a']})
        self.budget.select('A')
        bridge=GenerationActivityBridge(self.budget)
        self.addCleanup(bridge.close)
        self.now=10
        bridge.handle('generation_provider_http_failure',{'failure':{'http_status':503},
            'callback_executing':True})
        self.now=20
        self.assertEqual(self.state()['effective_experiment_seconds'],20)
        bridge.handle('theory_generation_stopped',{})
        self.now=50
        self.assertEqual(self.state()['effective_experiment_seconds'],20)
        bridge.handle('theory_generation_started',{})
        self.now=60
        self.assertEqual(self.state()['effective_experiment_seconds'],30)

    def test_offline_restart_preserves_charges(self):
        self.now=40;self.budget.cancel('operator_pause');self.budget.close()
        self.now=100
        resumed=NormalRuntimeBudget(self.ledger,1000,monotonic=lambda:self.now,
            resume_authorization='explicit resume')
        self.addCleanup(resumed.close)
        self.now=110
        state=resumed.snapshot()
        self.assertEqual(state['effective_experiment_seconds'],50)
        self.assertEqual(state['excluded_experiment_seconds'],60)
        self.assertEqual(state['experiment_wall_seconds'],110)

    def test_planning_handoff_counts_transition_and_preserves_spent(self):
        self.now=20;self.budget.close()
        self.now=30
        resumed=NormalRuntimeBudget(self.ledger,1000,monotonic=lambda:self.now,
            handoff=True,resume_authorization='planning handoff')
        self.addCleanup(resumed.close)
        self.assertEqual(resumed.snapshot()['effective_experiment_seconds'],30)
        self.assertEqual(resumed.snapshot()['excluded_experiment_seconds'],0)

    def test_unclean_tail_is_unknown_until_reconciled(self):
        self.now=10;self.budget.snapshot();self.budget._write_state('test_checkpoint')
        # A second reader sees the last open durable checkpoint after a crash.
        self.now=20
        resumed=NormalRuntimeBudget(self.ledger,1000,monotonic=lambda:self.now,
            resume_authorization='recover crashed controller')
        self.addCleanup(resumed.close)
        self.assertIsNone(resumed.snapshot()['remaining_seconds'])
        resumed.reconcile_unknown_tail(active_seconds=5,wall_seconds=10,evidence_id='operator-audit')
        self.assertEqual(resumed.snapshot()['effective_experiment_seconds'],15)

if __name__=='__main__':unittest.main()
