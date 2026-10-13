"""Active wall budget charges normal setup/check/seal and excludes interruptions."""
import tempfile
import unittest
from types import SimpleNamespace

from regen.infrastructure.active_wall_budget import ActiveWallBudget, POLICY, LIMIT_REASON
from regen.infrastructure.experiment_ledger import Ledger
from regen.infrastructure.generation_activity_bridge import GenerationActivityBridge
from regen.execution.check_tasks import CheckCancelled
from regen.planning.generic_codex_agent import instructions
from regen.tools.generic_serial_application import budget_options


class ActiveWallBudgetTests(unittest.TestCase):
    def test_all_live_phases_count_but_confirmed_recovery_does_not(self):
        with tempfile.TemporaryDirectory() as root:
            now=[0.]
            clock=lambda:now[0]
            ledger=Ledger(root,'wall',clock=clock)
            budget=ActiveWallBudget(ledger,100,monotonic=clock,checkpoint_seconds=5)
            recovery=budget.begin_exclusion('provider_recovery')
            now[0]+=10
            self.assertEqual(budget.snapshot()['active_wall_seconds'],10)
            budget.note_provider_started()
            budget.end_exclusion(recovery)
            budget.register({'T':['c']})
            budget.select('T')
            with budget.excluded('prefix_preparation'):
                now[0]+=20
            with budget.excluded('seal'):
                now[0]+=20
            self.assertEqual(budget.snapshot()['active_wall_seconds'],50)
            outage=budget.begin_exclusion('provider_recovery')
            now[0]+=30
            self.assertEqual(budget.snapshot()['active_wall_seconds'],50)
            budget.end_exclusion(outage)
            now[0]+=50
            with self.assertRaises(CheckCancelled):budget.check()
            self.assertEqual(budget.reason,LIMIT_REASON)
            self.assertEqual(budget.snapshot()['active_wall_seconds'],100)
            budget.close()
            self.assertEqual(budget.snapshot()['policy'],POLICY)

    def test_confirmed_http_failure_retains_charged_time_and_excludes_recovery(self):
        with tempfile.TemporaryDirectory() as root:
            now=[0.]
            clock=lambda:now[0]
            ledger=Ledger(root,'wall',clock=clock)
            budget=ActiveWallBudget(ledger,100,monotonic=clock)
            bridge=GenerationActivityBridge(budget)
            try:
                budget.register({'T':['c']});budget.select('T')
                bridge.recovering()
                now[0]=10;bridge.handle('theory_generation_started',{})
                now[0]=30;bridge.handle('generation_provider_http_failure',{
                    'generation_id':'g','thread_id':'t','turn_id':'u',
                    'failure':{'kind':'transport','http_status':None}})
                now[0]=35;bridge.handle('generation_items_interrupted',{
                    'generation_id':'g','thread_id':'t','turn_id':'u',
                    'reason':'ProviderHTTPError','item_ids':['r1','r2']})
                now[0]=40;bridge.handle('theory_generation_stopped',{})
                now[0]=80
                self.assertEqual(budget.snapshot()['active_wall_seconds'],40)
                now[0]=90;bridge.handle('theory_generation_started',{})
                now[0]=100
                self.assertEqual(budget.snapshot()['active_wall_seconds'],50)
                self.assertEqual([r['kind'] for r in ledger.rows if r['kind'] in
                    ('generation_provider_http_failure','generation_items_interrupted')],
                    ['generation_provider_http_failure','generation_items_interrupted'])
                self.assertFalse(any(r['kind']=='generation_clock_untrusted' for r in ledger.rows))
            finally:
                bridge.close();budget.close()

    def test_restart_excludes_offline_time_and_preserves_budget(self):
        with tempfile.TemporaryDirectory() as root:
            now=[0.]
            clock=lambda:now[0]
            ledger=Ledger(root,'wall',clock=clock)
            first=ActiveWallBudget(ledger,100,monotonic=clock)
            now[0]=40
            first.cancel('provider_failure')
            first.close()
            now[0]=50000
            resumed=ActiveWallBudget(ledger,100,monotonic=clock,
                resume_authorization='resume after provider failure')
            self.assertEqual(resumed.snapshot()['active_wall_seconds'],40)
            now[0]+=60
            with self.assertRaises(CheckCancelled):resumed.check()
            self.assertEqual(resumed.reason,LIMIT_REASON)
            resumed.close()

    def test_seal_invalidation_replays_without_resetting_first_green_or_time(self):
        with tempfile.TemporaryDirectory() as root:
            now=[0.]
            clock=lambda:now[0]
            ledger=Ledger(root,'wall-seal',clock=clock)
            first=ActiveWallBudget(ledger,100,monotonic=clock)
            first.register({'A':['a'],'T':['t']})
            first.observe('t',{'status':'green'},theory='T')
            first.mark_sealed('T')
            now[0]=25
            first.invalidate_seal('T',old_source_identity='a'*64,
                current_source_identity='b'*64,reason='import_source_changed',
                changed_sources=['A.thy'])
            first.cancel('infrastructure_error');first.close()
            now[0]=5000
            resumed=ActiveWallBudget(ledger,100,monotonic=clock,
                resume_authorization='resume after audited seal migration')
            state=resumed.snapshot()
            self.assertFalse(state['theories']['T']['sealed'])
            self.assertEqual(state['green_contract_ids'],['t'])
            self.assertEqual(state['active_wall_seconds'],25)
            self.assertEqual(sum(r['kind']=='time_budget_green' for r in ledger.rows),1)
            self.assertEqual(sum(r['kind']=='seal_invalidated' for r in ledger.rows),1)
            resumed.close()

    def test_prompt_and_budget_options_are_wall_specific(self):
        value=instructions(3600,wall_seconds=172800)
        self.assertIn('active wall-clock budget of 172800',value)
        self.assertIn('Planning, model work, native preparation',value)
        self.assertNotIn('any theory accumulates 3600',value)
        args=SimpleNamespace(budget_mode='active-wall',wall_seconds=172800,
            effective_seconds=600,continue_from=None,resume_authorization=None,
            extend_wall_seconds=None,extend_effective_seconds=None)
        self.assertEqual(budget_options(args),('active-wall',172800,None,None))
        args.continue_from='old'
        with self.assertRaisesRegex(ValueError,'resume-authorization'):
            budget_options(args)


if __name__=='__main__':
    unittest.main()
