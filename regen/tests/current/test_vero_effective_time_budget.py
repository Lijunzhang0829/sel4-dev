"""VERO active-wall experiment policy; no external model or native checker."""
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest

from regen.execution.check_tasks import CheckCancelled
from regen.infrastructure.active_wall_budget import (
    ActiveWallBudget, LIMIT_REASON, POLICY,
)
from regen.infrastructure.experiment_ledger import Ledger
from regen.tools import vero_serial_application as entry


class VeroActiveWallBudgetTests(unittest.TestCase):
    def args(self, **changes):
        return SimpleNamespace(**{'model':'gpt-5.6-sol','reasoning':'medium',
            'expected_contracts':287,'active_wall_seconds':172800,
            'resume':False,**changes})

    def test_formal_binding_is_fixed_and_has_no_token_gate(self):
        binding=entry.formal_configuration_binding(self.args())
        self.assertEqual(binding,{'model':'gpt-5.6-sol',
            'reasoning_effort':'medium','expected_contract_count':287,
            'token_limit':None,'active_wall_seconds_limit':172800})
        for field,value in (('model','gpt-6-astra'),('reasoning','high'),
                ('expected_contracts',286),('active_wall_seconds',172799)):
            args=self.args();setattr(args,field,value)
            with self.assertRaisesRegex(ValueError,'formal VERO experiment requires'):
                entry.formal_configuration_binding(args)

    def test_prompt_states_online_wall_clock_and_no_extension(self):
        text=entry.active_wall_budget_instruction(172800)
        self.assertIn('active online wall time reaches 172800 seconds',text)
        self.assertIn('Planning, initialization, preparation',text)
        self.assertIn('seal, integration and ordinary waits all count',text)
        self.assertIn('cannot be extended',text)
        self.assertIn('no token stopping limit',text)

    def test_all_normal_phases_count_but_confirmed_recovery_does_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            tick=[0.];ledger=Ledger(tmp,'wall')
            stop=ActiveWallBudget(ledger,100,monotonic=lambda:tick[0])
            stop.register({'T':['c']});stop.select('T')
            for reason in ('planning','runtime_setup','seal','integration'):
                handle=stop.begin_exclusion(reason);tick[0]+=3;stop.end_exclusion(handle)
            self.assertEqual(stop.snapshot()['active_wall_seconds'],12)
            stop.note_provider_failure()
            handle=stop.begin_exclusion('provider_recovery')
            tick[0]+=100
            self.assertEqual(stop.snapshot()['active_wall_seconds'],12)
            stop.note_provider_started();stop.end_exclusion(handle)
            tick[0]+=2
            self.assertEqual(stop.snapshot()['active_wall_seconds'],14)
            stop.close()

    def test_limit_is_terminal_but_operational_pause_is_resumable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);tick=[0.];ledger=Ledger(root/'limit','limit')
            stop=ActiveWallBudget(ledger,10,monotonic=lambda:tick[0])
            tick[0]=10
            with self.assertRaises(CheckCancelled):stop.check()
            state=stop.snapshot()
            self.assertEqual(state['reason'],LIMIT_REASON)
            self.assertTrue(state['finished']);self.assertFalse(state['paused'])
            termination=[r for r in ledger.rows if r['kind']=='termination'][-1]
            self.assertEqual(termination['data']['disposition'],'stop')
            stop.close()
            with self.assertRaisesRegex(ValueError,'cannot be resumed'):
                entry.validate_resume_ledger(ledger)

            pause_ledger=Ledger(root/'pause','pause')
            paused=ActiveWallBudget(pause_ledger,10,monotonic=lambda:0)
            paused.cancel('infrastructure_error');paused.close()
            self.assertFalse(entry.validate_resume_ledger(pause_ledger)['finished'])

    def test_resume_preserves_consumed_clock_and_excludes_offline_gap(self):
        with tempfile.TemporaryDirectory() as tmp:
            tick=[0.];ledger=Ledger(tmp,'resume')
            first=ActiveWallBudget(ledger,100,monotonic=lambda:tick[0])
            tick[0]=9;first.cancel('operator_pause');first.close()
            authorization=ledger.append('operator_resume_authorized',authorization='test')
            tick[0]=10009
            resumed=ActiveWallBudget(ledger,100,monotonic=lambda:tick[0],
                resume_authorization=authorization['event_id'])
            self.assertEqual(resumed.snapshot()['active_wall_seconds'],9)
            tick[0]+=1
            self.assertEqual(resumed.snapshot()['active_wall_seconds'],10)
            resumed.close()

    def test_provider_takeover_excludes_only_confirmed_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);tick=[0.];ledger=Ledger(root/'ledger','recovery')
            stop=ActiveWallBudget(ledger,100,monotonic=lambda:tick[0])
            gate=entry.ProviderRecoveryGate(stop,ledger);calls=[]
            def run_once(recovering):
                calls.append(recovering)
                if not recovering:
                    tick[0]+=10
                    raise entry.RecoverableAgentFailure('provider failed')
                tick[0]+=1000
                self.assertEqual(stop.snapshot()['active_wall_seconds'],10)
                gate.generation_started();tick[0]+=1
                return {'status':'complete'}
            result=entry.run_with_agent_takeover(run_once,
                journal_path=root/'unused',ledger=ledger,stop=stop,
                load=lambda p:None,save=lambda *a:None,recovery_gate=gate)
            self.assertEqual(result['status'],'complete')
            self.assertEqual(calls,[False,True])
            self.assertEqual(stop.snapshot()['active_wall_seconds'],11)
            stop.close()

    def test_limit_interruption_is_reported_nonresumable(self):
        with tempfile.TemporaryDirectory() as tmp:
            tick=[0.];ledger=Ledger(tmp,'terminal')
            stop=ActiveWallBudget(ledger,1,monotonic=lambda:tick[0])
            tick[0]=1
            with self.assertRaises(CheckCancelled):stop.check()
            result=entry.interrupted_result(stop,RuntimeError('cancelled'))
            self.assertEqual(result['status'],'stopped')
            self.assertEqual(result['disposition'],'stop')
            self.assertFalse(result['resumable'])
            stop.close()

    def test_policy_identity(self):
        self.assertEqual(POLICY,'global-active-wall-budget-v1')


if __name__=='__main__':unittest.main()
