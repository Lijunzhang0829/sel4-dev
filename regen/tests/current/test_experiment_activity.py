import tempfile
from pathlib import Path
import unittest
from regen.infrastructure.experiment_ledger import Ledger
from regen.infrastructure.experiment_activity import (
    ActivityStop, GLOBAL_EFFECTIVE_POLICY, GLOBAL_EXPLORATION_POLICY,
    GlobalEffectiveActivityStop, GlobalExplorationStop, UsageSnapshots,
    experiment_summary,
)
from regen.execution.check_tasks import CheckCancelled


class ActivityTests(unittest.TestCase):
    def test_changes_reset_deadline_but_queries_and_reversions_do_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            now=[100.];ledger=Ledger(tmp,'run',clock=lambda:now[0]);stop=ActivityStop(ledger,7200)
            stop.observe('c','oops',kind='draft',baseline=True)
            now[0]+=7100
            self.assertTrue(stop.observe('c','by simp',kind='draft'))
            deadline=stop.deadline
            now[0]+=100
            self.assertFalse(stop.observe('c','by simp',kind='draft'))
            self.assertFalse(stop.observe('c','oops',kind='draft'))
            ledger.append('tool_started',tool='progress')
            self.assertEqual(stop.deadline,deadline)
            now[0]=deadline
            with self.assertRaises(CheckCancelled):stop.check()
            self.assertEqual(stop.reason,'no_contract_update')
            self.assertEqual(sum(r['kind']=='termination' for r in ledger.rows),1)

    def test_total_tokens_not_sum_of_cumulative_snapshots_or_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger=Ledger(tmp,'run');usage=UsageSnapshots(ledger)
            a={'inputTokens':100,'cachedInputTokens':80,'outputTokens':5,'totalTokens':105}
            b={'inputTokens':150,'cachedInputTokens':120,'outputTokens':9,'totalTokens':159}
            usage.record('thread',a,scope={'target_ids':['c','helper:T:h']},source='test')
            usage.record('thread',a,scope={},source='test')
            usage.record('thread',b,scope={},source='test')
            rows=[r for r in ledger.rows if r['kind']=='usage_snapshot']
            self.assertEqual(len(rows),2)
            self.assertEqual(rows[-1]['data']['delta']['totalTokens'],54)
            self.assertIsNone(rows[-1]['data']['total']['reasoningOutputTokens'])
            ledger.append('proof_commit',nodes=[{'uid':'c','kind':'contract','status':'green'},
                {'uid':'h','kind':'helper','status':'green'}])
            summary=experiment_summary(ledger)
            self.assertEqual(summary['curve'][-1]['total_tokens'],159)
            self.assertEqual(summary['curve'][-1]['contracts_green'],1)
            self.assertEqual(summary['curve'][-1]['helpers_green'],1)
            ledger.append('plan_accepted',allocation={})
            ledger.append('termination',reason='test')
            self.assertEqual(experiment_summary(ledger)['curve'][-1]['contracts_green'],0)
            with self.assertRaisesRegex(ValueError,'decreased'):
                usage.record('thread',a,scope={},source='test')
            recovered=UsageSnapshots(Ledger(tmp,'run'))
            recovered.record('thread',b,scope={},source='test')
            self.assertEqual(len(recovered.ledger.rows),len(ledger.rows))

    def test_operation_time_stops_at_recorded_pause_but_includes_normal_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            now=[100.];ledger=Ledger(tmp,'run',clock=lambda:now[0])
            ledger.append('experiment_started')
            now[0]=130;ledger.append('seal_result',status='sealed')
            now[0]=150;ledger.append('termination',reason='idle',disposition='pause')
            now[0]=500;ledger.append('experiment_finished')
            summary=experiment_summary(ledger)
            self.assertEqual(summary['elapsed_seconds'],50)
            self.assertEqual(summary['wall_elapsed_seconds'],400)

    def test_live_summary_uses_current_wall_time_for_fresh_and_resumed_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            now=[100.];ledger=Ledger(tmp,'run',clock=lambda:now[0])
            ledger.append('experiment_started')
            now[0]=140
            fresh=experiment_summary(ledger)
            self.assertEqual(fresh['elapsed_seconds'],40)
            self.assertEqual(fresh['wall_elapsed_seconds'],40)
            ledger.append('experiment_finished')
            now[0]=500;ledger.append('experiment_resumed')
            now[0]=540
            resumed=experiment_summary(ledger)
            self.assertEqual(resumed['elapsed_seconds'],80)
            self.assertEqual(resumed['wall_elapsed_seconds'],440)

    def test_explicit_pause_closes_running_segment_without_finished_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            now=[100.];ledger=Ledger(tmp,'run',clock=lambda:now[0])
            ledger.append('experiment_started')
            now[0]=125;ledger.append('experiment_paused',reason='infrastructure_error')
            now[0]=10000
            self.assertEqual(experiment_summary(ledger)['elapsed_seconds'],25)
            ledger.append('experiment_resumed')
            now[0]+=10
            self.assertEqual(experiment_summary(ledger)['elapsed_seconds'],35)

    def test_cost_scope_is_shared_for_multiple_targets_or_theories(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger=Ledger(tmp,'run');usage=UsageSnapshots(ledger)
            totals=[10,20,30,40]
            scopes=[
                {'target_ids':['c1','c2'],'theories':['A']},
                {'target_ids':['c1'],'theories':['A','B']},
                {'target_ids':['c1'],'theories':['A']},
                {'target_ids':[],'theories':[]},
            ]
            for total,scope in zip(totals,scopes):
                usage.record('thread',{'totalTokens':total},scope=scope,source='test')
            self.assertEqual([row['allocation'] for row in experiment_summary(ledger)['cost_intervals']],
                ['shared','shared','declared-single-target','unattributed'])


class GlobalEffectiveActivityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = [0.]
        self.wall = [1000.]
        self.ledger = Ledger(self.temp.name, 'global-clock',
                             clock=lambda: self.wall[0])
        self.stop = GlobalEffectiveActivityStop(
            self.ledger, 100, monotonic=lambda: self.now[0])
        self.stop.register({'A': ['a1', 'a2'], 'B': ['b1']})
        self.stop.select('A')
        self.addCleanup(self.stop.close)

    def advance(self, seconds):
        self.now[0] += seconds
        self.wall[0] += seconds

    def state(self):
        return self.stop.snapshot()

    def green(self, uid, theory, **kwargs):
        return self.stop.observe(uid, {'status': 'green'},
                                 kind='validation', theory=theory, **kwargs)

    def test_one_clock_crosses_theory_switches_and_only_first_contract_green_resets(self):
        self.advance(40)
        self.stop.select('B')
        self.advance(40)
        self.stop.observe('b1', 'candidate', kind='draft', theory='B')
        self.stop.observe('helper:B:h', {'status': 'green'},
                          kind='validation', theory='B')
        self.assertEqual(self.state()['idle_seconds'], 80)
        self.assertTrue(self.green('b1', 'B'))
        self.assertEqual(self.state()['idle_seconds'], 0)
        self.advance(70)
        self.stop.select('A')
        self.advance(20)
        self.assertFalse(self.green('b1', 'B'))
        self.assertEqual(self.state()['idle_seconds'], 90)
        self.advance(10)
        with self.assertRaises(CheckCancelled):
            self.stop.check()
        self.assertEqual(self.stop.reason,
                         'no_new_contract_global_effective_time')

    def test_exact_deadline_first_green_wins_but_late_green_cannot_revive(self):
        self.advance(100)
        self.assertTrue(self.green('a1', 'A'))
        self.assertFalse(self.stop.event.is_set())
        self.assertEqual(self.state()['idle_seconds'], 0)
        self.advance(100.01)
        with self.assertRaises(CheckCancelled):
            self.green('a2', 'A')
        self.assertEqual(self.state()['green_contract_ids'], ['a1'])

    def test_initialization_pause_and_seal_are_excluded_but_seal_repair_counts(self):
        self.stop.select(None)  # Planning/initialization before generation.
        self.advance(1000)
        self.stop.check()
        self.stop.select('A')
        self.advance(10)
        initialization = self.stop.begin_exclusion('theory_initialization')
        self.advance(500)
        self.stop.end_exclusion(initialization)
        pause = self.stop.begin_exclusion('experiment_pause')
        self.advance(700)
        self.stop.end_exclusion(pause)
        seal = self.stop.begin_exclusion('seal')
        self.advance(900)
        self.stop.end_exclusion(seal)
        self.assertEqual(self.state()['idle_seconds'], 10)
        # A failed native seal returns to model-driven repair and therefore
        # consumes effective generation time.
        self.advance(25)
        self.assertEqual(self.state()['idle_seconds'], 35)

    def test_nested_exclusions_are_union_and_unknown_exclusion_fails(self):
        self.advance(5)
        first = self.stop.begin_exclusion('theory_initialization')
        self.advance(20)
        second = self.stop.begin_exclusion('seal')
        self.advance(30)
        self.stop.end_exclusion(first)
        self.advance(40)
        self.stop.end_exclusion(second)
        self.advance(5)
        self.assertEqual(self.state()['idle_seconds'], 10)
        with self.assertRaises(ValueError):
            self.stop.begin_exclusion('model_claimed_idle')

    def test_unclean_recovery_uses_last_checkpoint_closed_and_excludes_offline_time(self):
        self.advance(25)
        self.green('a1', 'A')
        self.advance(40)
        self.stop.check()
        checkpoint = [r for r in self.ledger.rows
                      if r['kind'] == 'global_activity_state'][-1]
        self.assertEqual(checkpoint['data']['boundary'], 'checkpoint')
        # Mimic process death without writing the semantic close boundary.
        self.stop.closed.set()
        self.advance(50000)
        with self.assertRaisesRegex(ValueError, 'explicit resume authorization'):
            GlobalEffectiveActivityStop(
                self.ledger, 100, monotonic=lambda: self.now[0])
        resumed = GlobalEffectiveActivityStop(
            self.ledger, 100, monotonic=lambda: self.now[0],
            resume_authorization='incident-42-approved')
        self.addCleanup(resumed.close)
        resumed.register({'A': ['a1', 'a2'], 'B': ['b1']})
        self.assertIsNone(resumed.snapshot()['active_theory'])
        self.assertEqual(resumed.snapshot()['idle_seconds'], 40)
        self.assertEqual(resumed.snapshot()['green_contract_ids'], ['a1'])
        self.assertEqual(resumed.snapshot()['gate_state'], 'CLOSED')
        started = [r for r in self.ledger.rows
                   if r['kind'] == 'global_activity_started'][-1]
        self.assertTrue(started['data']['unobserved_shutdown_tail'])
        self.assertEqual(started['data']['recovered_gate'], 'CLOSED')
        self.assertEqual(started['data']['unobserved_tail_upper_bound_seconds'], 5)
        authorization = [r for r in self.ledger.rows
                         if r['kind'] == 'global_activity_resume_authorized'][-1]
        self.assertEqual(authorization['data']['authorization'],
                         'incident-42-approved')
        self.assertFalse(authorization['data']['new_inactivity_window'])
        resumed.select('B')
        self.advance(60)
        with self.assertRaises(CheckCancelled):
            resumed.check()
        with self.assertRaisesRegex(ValueError, 'policy changed'):
            GlobalEffectiveActivityStop(
                self.ledger, 200, monotonic=lambda: self.now[0])

    def test_clean_close_requires_authorization_but_remains_resumable(self):
        self.advance(12)
        self.stop.close()
        closed = [r for r in self.ledger.rows
                  if r['kind'] == 'global_activity_state'][-1]
        self.assertEqual(closed['data']['boundary'], 'close')
        self.assertTrue(closed['data']['state']['closed'])
        self.assertEqual(closed['data']['state']['gate_state'], 'CLOSED')
        with self.assertRaisesRegex(ValueError, 'explicit resume authorization'):
            GlobalEffectiveActivityStop(
                self.ledger, 100, monotonic=lambda: self.now[0])
        resumed = GlobalEffectiveActivityStop(self.ledger, 100,
            monotonic=lambda: self.now[0], resume_authorization='scheduled-restart')
        self.addCleanup(resumed.close)
        self.assertEqual(resumed.snapshot()['idle_seconds'], 12)
        started = [r for r in self.ledger.rows
                   if r['kind'] == 'global_activity_started'][-1]
        self.assertEqual(started['data']['unobserved_tail_upper_bound_seconds'], 0)

    def test_finish_is_non_resumable_even_with_authorization(self):
        self.advance(9)
        self.stop.finish('repository_complete')
        finished = [r for r in self.ledger.rows
                    if r['kind'] == 'global_activity_state'][-1]
        self.assertEqual(finished['data']['boundary'], 'finished')
        self.assertTrue(finished['data']['state']['finished'])
        self.assertEqual(finished['data']['state']['finish_reason'],
                         'repository_complete')
        with self.assertRaisesRegex(ValueError, 'durably finished'):
            GlobalEffectiveActivityStop(self.ledger, 100,
                monotonic=lambda: self.now[0],
                resume_authorization='must-not-override-finish')

    def test_open_checkpoints_persist_totals_and_gate_boundaries_close_immediately(self):
        states_before=sum(r['kind']=='global_activity_state' for r in self.ledger.rows)
        self.advance(1)
        self.stop.check()
        self.assertEqual(sum(r['kind']=='global_activity_state' for r in self.ledger.rows),
                         states_before)
        self.advance(4)
        self.stop.check()
        checkpoint = [r for r in self.ledger.rows
                      if r['kind'] == 'global_activity_state'][-1]
        self.assertEqual(checkpoint['data']['boundary'], 'checkpoint')
        self.assertEqual(checkpoint['data']['state']['gate_state'], 'OPEN')
        self.assertEqual(checkpoint['data']['state']['idle_seconds'], 5)
        self.assertEqual(checkpoint['data']['state']['effective_seconds'], 5)
        self.assertNotIn('contract_owners', checkpoint['data']['state'])
        registrations = [r for r in self.ledger.rows
                         if r['kind'] == 'global_activity_registered']
        self.assertEqual(len(registrations), 1)
        self.assertEqual(registrations[0]['data']['contract_count'], 3)
        self.assertFalse(any('contract_owners' in r['data']['state']
            for r in self.ledger.rows if r['kind']=='global_activity_state'))
        outer = self.stop.begin_exclusion('theory_initialization')
        inner = self.stop.begin_exclusion('seal')
        excluded = [r for r in self.ledger.rows
                    if r['kind'] == 'global_activity_state'][-1]
        self.assertEqual(excluded['data']['boundary'], 'exclusion_started')
        self.assertEqual(excluded['data']['state']['gate_state'], 'CLOSED')
        self.stop.end_exclusion(outer)
        self.assertEqual(self.state()['gate_state'], 'CLOSED')
        self.stop.end_exclusion(inner)
        self.assertEqual(self.state()['gate_state'], 'OPEN')

    def test_baseline_unknown_owner_and_wrong_theory_never_reset(self):
        self.advance(30)
        self.assertFalse(self.green('a1', 'A', baseline=True))
        self.assertEqual(self.state()['idle_seconds'], 30)
        self.advance(20)
        self.assertFalse(self.green('a2', 'B'))
        self.assertFalse(self.green('foreign', 'A'))
        self.assertEqual(self.state()['idle_seconds'], 50)
        self.assertFalse(self.green('a1', 'A'))
        self.assertEqual(self.state()['idle_seconds'], 50)

    def test_repeated_observation_is_not_relogged(self):
        self.stop.observe('a1', 'candidate', kind='draft', theory='A')
        rows = sum(r['kind'] == 'target_updated' for r in self.ledger.rows)
        states = sum(r['kind'] == 'global_activity_state' for r in self.ledger.rows)
        self.assertFalse(self.stop.observe('a1', 'candidate', kind='draft', theory='A'))
        self.assertEqual(sum(r['kind'] == 'target_updated' for r in self.ledger.rows), rows)
        self.assertEqual(sum(r['kind'] == 'global_activity_state' for r in self.ledger.rows), states)

    def test_recovery_replays_first_green_committed_after_last_checkpoint(self):
        self.advance(25)
        previous = [r for r in self.ledger.rows
                    if r['kind'] == 'global_activity_state'][-1]
        self.ledger.append('global_contract_first_green', theory='A', target_id='a1',
            previous_idle_seconds=25, cumulative_effective_seconds=25)
        self.ledger.append('target_updated', target_id='a1', update_kind='validation',
            theory='A', value_sha256='evidence', refreshes_inactivity=True,
            activity_policy='global-first-contract-green-effective-generation-v1')
        self.stop.closed.set()
        resumed = GlobalEffectiveActivityStop(self.ledger, 100,
            monotonic=lambda:self.now[0], resume_authorization='recover-tail-green')
        self.addCleanup(resumed.close)
        resumed.register({'A':['a1','a2'],'B':['b1']})
        self.assertIn('a1', resumed.snapshot()['green_contract_ids'])
        self.assertEqual(resumed.snapshot()['idle_seconds'], 0)
        self.assertEqual(resumed.snapshot()['effective_seconds'], 25)
        self.assertLess(previous['sequence'], self.ledger.rows[-1]['sequence'])

    def test_recovery_replays_timeout_committed_after_last_checkpoint(self):
        self.ledger.append('termination',
            reason='no_new_contract_global_effective_time', disposition='pause',
            activity_policy='global-first-contract-green-effective-generation-v1',
            seconds=100, idle_seconds=100, cumulative_effective_seconds=100,
            window_number=1)
        self.stop.closed.set()
        resumed = GlobalEffectiveActivityStop(self.ledger, 100,
            monotonic=lambda:self.now[0], resume_authorization='recover-tail-timeout')
        self.addCleanup(resumed.close)
        resumed.register({'A':['a1','a2'],'B':['b1']})
        state = resumed.snapshot()
        self.assertEqual(state['window_number'], 2)
        self.assertEqual(state['idle_seconds'], 0)
        self.assertEqual(state['effective_seconds'], 100)

    def test_monotonic_regression_is_rejected(self):
        self.advance(10)
        self.stop.check()
        self.now[0] = 9
        with self.assertRaisesRegex(ValueError, 'moved backwards'):
            self.stop.check()
        self.now[0] = 10

    def test_timeout_resume_requires_authorized_new_window_and_keeps_history(self):
        self.advance(100)
        with self.assertRaises(CheckCancelled):
            self.stop.check()
        paused = [r for r in self.ledger.rows
                  if r['kind'] == 'global_activity_state'][-1]
        self.assertEqual(paused['data']['boundary'], 'paused')
        self.assertEqual(paused['data']['state']['terminal_reason'],
                         'no_new_contract_global_effective_time')
        # Lost process after the durable pause, without a later clean close.
        self.stop.closed.set()
        with self.assertRaisesRegex(ValueError, 'explicit resume authorization'):
            GlobalEffectiveActivityStop(
                self.ledger, 100, monotonic=lambda: self.now[0])
        resumed = GlobalEffectiveActivityStop(
            self.ledger, 100, monotonic=lambda: self.now[0],
            resume_authorization='new-run-after-timeout')
        self.addCleanup(resumed.close)
        state=resumed.snapshot()
        self.assertEqual(state['idle_seconds'], 0)
        self.assertEqual(state['effective_seconds'], 100)
        self.assertEqual(state['window_number'], 2)
        authorization = [r for r in self.ledger.rows
                         if r['kind'] == 'global_activity_resume_authorized'][-1]
        self.assertTrue(authorization['data']['new_inactivity_window'])
        self.assertEqual(authorization['data']['previous_idle_seconds'], 100)
        self.assertEqual(authorization['data']['cumulative_effective_seconds'], 100)
        resumed.register({'A': ['a1', 'a2'], 'B': ['b1']})
        resumed.select('A')
        self.assertTrue(resumed.observe('a1', {'status': 'green'},
                                       kind='validation', theory='A'))

    def test_operator_pause_resume_preserves_current_window_and_green(self):
        self.advance(30)
        self.green('a1','A')
        self.advance(20)
        self.stop.cancel('operator_pause')
        self.stop.closed.set()
        resumed=GlobalEffectiveActivityStop(self.ledger,100,
            monotonic=lambda:self.now[0],resume_authorization='operator-resume-7')
        self.addCleanup(resumed.close)
        state=resumed.snapshot()
        self.assertEqual(state['idle_seconds'],20)
        self.assertEqual(state['effective_seconds'],50)
        self.assertEqual(state['green_contract_ids'],['a1'])
        self.assertEqual(state['window_number'],1)
        authorization=[r for r in self.ledger.rows
            if r['kind']=='global_activity_resume_authorized'][-1]
        self.assertFalse(authorization['data']['new_inactivity_window'])
        self.assertEqual(authorization['data']['previous_terminal_reason'],
                         'operator_pause')

    def test_legacy_full_state_recovers_with_explicit_authorization(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger=Ledger(tmp,'legacy-global')
            ledger.append('global_activity_state',boundary='checkpoint',state={
                'policy':GLOBAL_EFFECTIVE_POLICY,'seconds':100.,
                'contract_owners':{'a1':'A'},'green_contract_ids':[],
                'idle_seconds':23.,'effective_seconds':23.,'next_exclusion':0,
                'terminal_reason':None})
            recovered=GlobalEffectiveActivityStop(ledger,100,
                monotonic=lambda:500.,resume_authorization='legacy-migration')
            self.addCleanup(recovered.close)
            self.assertEqual(recovered.snapshot()['idle_seconds'],23)
            recovered.register({'A':['a1']})
            registrations=[r for r in ledger.rows
                           if r['kind']=='global_activity_registered']
            self.assertEqual(len(registrations),1)
            recovered.close()
            recovered_again=GlobalEffectiveActivityStop(ledger,100,
                monotonic=lambda:800.,resume_authorization='post-migration')
            self.addCleanup(recovered_again.close)
            recovered_again.register({'A':['a1']})

    def test_watchdog_fail_closed_pauses_and_aborts(self):
        import threading
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger(tmp, 'watchdog')
            stop = GlobalEffectiveActivityStop(
                ledger, .02, poll_seconds=.005)
            stop.register({'A': ['a1']})
            stop.select('A')
            aborted = threading.Event()
            stop.start(abort=aborted.set)
            self.assertTrue(stop.event.wait(1))
            self.assertTrue(aborted.wait(1))
            stop.close()
            self.assertEqual(stop.reason,
                             'no_new_contract_global_effective_time')
            self.assertEqual(sum(r['kind'] == 'termination'
                                 for r in ledger.rows), 1)


class GlobalExplorationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.now=[0.];self.ledger=Ledger(self.temp.name,'exploration')
        self.stop=GlobalExplorationStop(self.ledger,100,50,monotonic=lambda:self.now[0])
        self.stop.register({'A':['a1','a2'],'B':['b1']});self.stop.select('A')
        self.addCleanup(self.stop.close)

    def advance(self,n):self.now[0]+=n
    def usage(self,thread,total):
        self.ledger.append('usage_snapshot',thread_id=thread,
            total={'totalTokens':total,'cachedInputTokens':999999,
                   'reasoningOutputTokens':888888})
    def response(self,rid,thread,total):
        self.ledger.append('response',response_id=rid,thread_id=thread,turn_id=rid,
            usage=None if total is None else {'totalTokens':total})

    def test_time_first_waits_for_token_boundary(self):
        self.advance(50);self.stop.check();self.assertFalse(self.stop.event.is_set())
        self.usage('t1',99);self.stop.usage_updated();self.assertFalse(self.stop.event.is_set())
        self.usage('t1',100)
        with self.assertRaises(CheckCancelled):self.stop.usage_updated()
        self.assertEqual(self.stop.reason,'global_exploration_joint_limit_reached')
        state=[r for r in self.ledger.rows if r['kind']=='global_exploration_state'][-1]
        self.assertEqual(state['data']['boundary'],'finished')
        self.assertTrue(state['data']['state']['finished'])

    def test_tokens_first_waits_for_effective_boundary(self):
        self.usage('t1',100);self.stop.usage_updated();self.assertFalse(self.stop.event.is_set())
        self.advance(49);self.stop.check();self.assertFalse(self.stop.event.is_set())
        self.advance(1)
        with self.assertRaises(CheckCancelled):self.stop.check()

    def test_snapshot_replaces_unique_response_fallback_without_double_counting(self):
        self.response('r1','t1',30);self.response('r2','t1',20)
        self.response('r2','t1',20);self.response('r3','t2',500)
        self.usage('t2',40);self.stop.usage_updated();state=self.stop.snapshot()
        self.assertEqual(state['authoritative_total_tokens'],90)
        self.assertEqual(state['response_fallback_thread_ids'],['t1'])
        self.usage('t1',60);self.stop.usage_updated();state=self.stop.snapshot()
        self.assertEqual(state['authoritative_total_tokens'],100)
        self.assertEqual(state['response_fallback_thread_ids'],[])

    def test_unknown_total_never_claims_limit(self):
        self.response('unknown','t1',None);self.usage('t2',200);self.advance(60)
        self.stop.usage_updated();state=self.stop.snapshot()
        self.assertIsNone(state['authoritative_total_tokens'])
        self.assertEqual(state['unknown_token_thread_ids'],['t1'])
        self.assertFalse(state['tokens_reached']);self.assertFalse(self.stop.event.is_set())
        self.usage('t1',10)
        with self.assertRaises(CheckCancelled):self.stop.usage_updated()

    def test_green_only_records_progress(self):
        self.advance(30)
        self.assertTrue(self.stop.observe('a1',{'status':'green'},kind='validation',theory='A'))
        self.assertEqual(self.stop.snapshot()['effective_seconds'],30)
        self.advance(20);self.stop.check();self.assertFalse(self.stop.event.is_set())
        self.assertFalse(self.stop.observe('a1',{'status':'green'},kind='validation',theory='A'))

    def test_exclusion_recovery_and_usage_tail_replay(self):
        self.advance(20);handle=self.stop.begin_exclusion('seal')
        self.advance(100);self.stop.end_exclusion(handle)
        self.usage('t1',60);self.stop.usage_updated();self.stop.close();self.usage('t1',80)
        with self.assertRaisesRegex(ValueError,'explicit resume authorization'):
            GlobalExplorationStop(self.ledger,100,50,monotonic=lambda:self.now[0])
        resumed=GlobalExplorationStop(self.ledger,100,50,monotonic=lambda:self.now[0],
            resume_authorization='restart-1');self.addCleanup(resumed.close)
        state=resumed.snapshot();self.assertEqual(state['effective_seconds'],20)
        self.assertEqual(state['authoritative_total_tokens'],80)
        resumed.register({'A':['a1','a2'],'B':['b1']});resumed.select('B')
        self.advance(30);self.usage('t1',100)
        with self.assertRaises(CheckCancelled):resumed.usage_updated()
        resumed.closed.set()
        with self.assertRaisesRegex(ValueError,'durably finished'):
            GlobalExplorationStop(self.ledger,100,50,monotonic=lambda:self.now[0],
                resume_authorization='cannot-extend-limit')

    def test_lightweight_checkpoint_and_defaults(self):
        self.assertEqual(self.stop.snapshot()['policy'],GLOBAL_EXPLORATION_POLICY)
        self.advance(5);self.stop.check()
        saved=[r for r in self.ledger.rows if r['kind']=='global_exploration_state'][-1]
        self.assertEqual(saved['data']['boundary'],'checkpoint')
        self.assertNotIn('contract_owners',saved['data']['state'])
        self.assertEqual(sum(r['kind']=='global_exploration_registered'
            for r in self.ledger.rows),1)

    def test_progress_event_replays_if_state_write_crashes(self):
        def fail(boundary):raise RuntimeError('simulated state write crash')
        self.stop._write_state=fail
        with self.assertRaisesRegex(RuntimeError,'simulated'):
            self.stop.observe('a1',{'status':'green'},kind='validation',theory='A')
        self.stop.closed.set()
        resumed=GlobalExplorationStop(self.ledger,100,50,monotonic=lambda:self.now[0],
            resume_authorization='recover-progress');self.addCleanup(resumed.close)
        self.assertEqual(resumed.snapshot()['green_contract_ids'],['a1'])

    def test_pause_event_replays_evidence_and_disposition_if_state_write_crashes(self):
        self.advance(20)
        def fail(boundary):raise RuntimeError('simulated state write crash')
        self.stop._write_state=fail
        with self.assertRaisesRegex(RuntimeError,'simulated'):self.stop.cancel('operator_pause')
        termination=[r for r in self.ledger.rows if r['kind']=='termination'][-1]
        self.assertEqual(termination['data']['disposition'],'pause')
        self.stop.closed.set()
        resumed=GlobalExplorationStop(self.ledger,100,50,monotonic=lambda:self.now[0],
            resume_authorization='recover-pause');self.addCleanup(resumed.close)
        self.assertEqual(resumed.snapshot()['effective_seconds'],20)
        authorization=[r for r in self.ledger.rows
            if r['kind']=='global_exploration_resume_authorized'][-1]
        self.assertEqual(authorization['data']['previous_terminal_reason'],'operator_pause')

    def test_joint_and_finish_events_are_terminal_if_state_write_crashes(self):
        self.advance(50);self.usage('t1',100)
        def fail(boundary):raise RuntimeError('simulated state write crash')
        self.stop._write_state=fail
        with self.assertRaisesRegex(RuntimeError,'simulated'):self.stop.usage_updated()
        termination=[r for r in self.ledger.rows if r['kind']=='termination'][-1]
        self.assertEqual(termination['data']['disposition'],'stop')
        with self.assertRaisesRegex(ValueError,'durably finished'):
            GlobalExplorationStop(self.ledger,100,50,monotonic=lambda:self.now[0],
                resume_authorization='forbidden')
        with tempfile.TemporaryDirectory() as tmp:
            ledger=Ledger(tmp,'finish-crash');clock=GlobalExplorationStop(ledger,100,50,
                monotonic=lambda:0.);clock.register({'A':['a1']});clock.select('A')
            clock._write_state=fail
            with self.assertRaisesRegex(RuntimeError,'simulated'):clock.finish('complete')
            with self.assertRaisesRegex(ValueError,'durably finished'):
                GlobalExplorationStop(ledger,100,50,monotonic=lambda:0.,
                    resume_authorization='forbidden')


class ActivityCompatibilityTests(unittest.TestCase):

    def test_watchdog_cancels_without_tool_calls(self):
        import time
        with tempfile.TemporaryDirectory() as tmp:
            ledger=Ledger(tmp,'run');stop=ActivityStop(ledger,.02,poll_seconds=.005)
            stop.start()
            self.assertTrue(stop.event.wait(1))
            stop.close()
            self.assertEqual(stop.reason,'no_contract_update')

    def test_vero_rollout_wins_over_cli_total_even_in_one_poll(self):
        import json
        from regen.infrastructure.vero_experiment_observer import VeroObserver
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);ledger=Ledger(root/'run','test');stop=ActivityStop(ledger)
            observer=VeroObserver(ledger,stop,root/'workspace',{})
            observer.home=root/'home';session=observer.home/'sessions';session.mkdir(parents=True)
            thread='abc-123'
            entries=[{'type':'session_meta','payload':{'id':thread}}]
            for n in (10,100):
                entries.append({'type':'event_msg','payload':{'type':'token_count','info':{'total_token_usage':
                    {'input_tokens':n,'output_tokens':1,'total_tokens':n+1,'cached_input_tokens':0}}}})
            (session/(thread+'.jsonl')).write_text(''.join(json.dumps(e)+'\n' for e in entries))
            events=root/'cli.jsonl';events.write_text(json.dumps({'type':'thread.started','thread_id':thread})+'\n'+
                json.dumps({'type':'turn.completed','usage':{'input_tokens':100,'output_tokens':1}})+'\n')
            observer.usage_events(events,'proof-1',final=True)
            observer.usage_events(events,'proof-1',final=True)
            snapshots=[r for r in ledger.rows if r['kind']=='usage_snapshot']
            self.assertEqual(len(snapshots),2)
            self.assertEqual(experiment_summary(ledger)['curve'][-1]['total_tokens'],101)

    def test_vero_blocked_consumer_cannot_retain_old_green_count(self):
        from regen.infrastructure.vero_experiment_observer import VeroObserver
        with tempfile.TemporaryDirectory() as tmp:
            ledger=Ledger(tmp,'test');stop=ActivityStop(ledger)
            bench={'contracts':[{'id':'c1','module':'A'},{'id':'c2','module':'B'}]}
            observer=VeroObserver(ledger,stop,Path(tmp)/'workspace',bench)
            for theory,uid in [('A','c1'),('B','c2')]:
                nodes=[{'uid':uid,'kind':'contract','theory':theory,'status':'green'}]
                observer.nodes[theory]=nodes;ledger.append('proof_commit',theory=theory,nodes=nodes)
            observer.evaluated({'contracts':{'c1':{'status':'build_error'},'c2':{'status':'blocked_dependency'}},
                'modules':{'A':{'status':'build_error'},'B':{'status':'blocked_dependency'}}})
            self.assertEqual(experiment_summary(ledger)['curve'][-1]['contracts_green'],0)

    def test_vero_seal_failure_keeps_native_contract_green(self):
        from regen.infrastructure.vero_experiment_observer import VeroObserver
        with tempfile.TemporaryDirectory() as tmp:
            ledger=Ledger(tmp,'test');stop=ActivityStop(ledger)
            bench={'contracts':[{'id':'c1','module':'A'}]}
            observer=VeroObserver(ledger,stop,Path(tmp)/'workspace',bench)
            nodes=[{'uid':'c1','kind':'contract','theory':'A','status':'green'}]
            observer.nodes['A']=nodes;ledger.append('proof_commit',theory='A',nodes=nodes)
            observer.evaluated({'contracts':{'c1':{'status':'build_error'}},
                'modules':{'A':{'status':'build_error','contract_status':'ok',
                    'seal_status':'seal_failed'}}})
            self.assertEqual(observer.nodes['A'][0]['status'],'green')
            self.assertEqual(experiment_summary(ledger)['curve'][-1]['contracts_green'],1)
