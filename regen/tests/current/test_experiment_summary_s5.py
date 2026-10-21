"""Method-neutral ledger projection cases needed for comparable experiments."""
import tempfile
import unittest

from regen.infrastructure.experiment_activity import UsageSnapshots, experiment_summary
from regen.infrastructure.experiment_ledger import Ledger


class ExperimentSummaryS5Tests(unittest.TestCase):
    def test_integration_is_current_only_until_a_proof_or_source_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger=Ledger(tmp,'run')
            self.assertIsNone(experiment_summary(ledger)['current_integration']['verified'])
            ledger.append('proof_commit',theory='T',nodes=[
                {'uid':'c','kind':'contract','status':'green'}])
            ledger.append('integrated',verified=True)
            self.assertTrue(experiment_summary(ledger)['current_integration']['verified'])
            ledger.append('source_missing',affected_theories=['T'],
                          missing_sources=['T.thy'])
            invalidated=experiment_summary(ledger)
            self.assertFalse(invalidated['current_integration']['verified'])
            self.assertEqual(invalidated['current_integration']['invalidated_by'],
                             ledger.rows[-1]['event_id'])
            ledger.append('integrated',verified=True)
            self.assertTrue(experiment_summary(ledger)['current_integration']['verified'])
            ledger.append('proof_commit',theory='T',nodes=[
                {'uid':'c','kind':'contract','status':'open'}])
            self.assertFalse(experiment_summary(ledger)['current_integration']['verified'])

    def test_codex_acceptance_seal_survives_telemetry_then_is_revoked(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger=Ledger(tmp,'run')
            nodes=[{'uid':'c','kind':'contract','status':'green'},
                   {'uid':'helper:T:h','kind':'helper','status':'green'}]
            ledger.append('proof_commit',theory='T',event='batch',nodes=nodes)
            ledger.append('proof_commit',theory='T',event='seal',
                          nodes=[{**n,'status':'sealed'} for n in nodes])
            ledger.append('seal_result',theory='T',result_status='sealed',
                          authority='consult proof_commit seal evidence')
            sealed=experiment_summary(ledger)
            self.assertEqual(sealed['current_valid_sealed_theories'],['T'])
            self.assertEqual(sealed['current_valid_contract_ids'],['c'])
            self.assertEqual(sealed['historical_first_green_helper_ids'],['helper:T:h'])
            ledger.append('seal_invalidated',theory='T',reason='import_source_changed')
            revoked=experiment_summary(ledger)
            self.assertEqual(revoked['current_valid_sealed_theories'],[])
            self.assertEqual(revoked['current_valid_contract_ids'],[])
            self.assertEqual(revoked['historical_first_green_contract_ids'],['c'])
            ledger.append('experiment_paused',reason='infrastructure_error')
            ledger.append('experiment_resumed')
            ledger.append('proof_commit',theory='T',event='batch',nodes=nodes)
            self.assertEqual(experiment_summary(ledger)['current_valid_sealed_theories'],[])
            ledger.append('proof_commit',theory='T',event='seal',
                          nodes=[{**n,'status':'sealed'} for n in nodes])
            self.assertEqual(experiment_summary(ledger)['current_valid_sealed_theories'],['T'])

    def test_codex_theory_ids_are_shared_and_response_only_usage_is_visible(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger=Ledger(tmp,'run')
            ledger.append('experiment_started')
            scope={'target_ids':['c'],'theory_ids':['A','B'],
                   'basis':'agent-declared-intended-work'}
            for number,amount in enumerate((12,8),1):
                ledger.response({'responseId':f'r{number}','threadId':'t',
                                 'turnId':f'turn{number}',
                                 'usage':{'inputTokens':amount-2,'outputTokens':2,
                                          'totalTokens':amount}},
                                scope=scope,invocation='test',model='test',effort='medium')
            summary=experiment_summary(ledger)
            self.assertEqual(summary['usage_accounting']['total_tokens'],20)
            self.assertEqual(summary['curve'][-1]['total_tokens'],20)
            self.assertEqual([r['usage']['totalTokens'] for r in summary['cost_intervals']],
                             [12,8])
            self.assertEqual([r['allocation'] for r in summary['cost_intervals']],
                             ['shared','shared'])
            self.assertEqual([r['usage_basis'] for r in summary['cost_intervals']],
                             ['response_receipt_only']*2)

    def test_response_receipts_do_not_duplicate_a_thread_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger=Ledger(tmp,'run');usage=UsageSnapshots(ledger)
            ledger.response({'responseId':'r1','threadId':'t','turnId':'turn1',
                             'usage':{'inputTokens':10,'outputTokens':2,'totalTokens':12}},
                            scope={'target_ids':['c']},invocation='test',model='test',effort='medium')
            usage.record('t',{'totalTokens':30},scope={'target_ids':['c']},source='test')
            summary=experiment_summary(ledger)
            self.assertEqual(summary['usage_accounting']['total_tokens'],30)
            self.assertEqual(len(summary['cost_intervals']),1)
            self.assertEqual(summary['cost_intervals'][0]['usage']['totalTokens'],30)

    def test_prestart_budget_setup_is_not_generation_time_and_plan_reset_is_on_curve(self):
        with tempfile.TemporaryDirectory() as tmp:
            now=[100.]
            ledger=Ledger(tmp,'run',clock=lambda:now[0])
            ledger.append('time_budget_started')
            now[0]=120;ledger.append('experiment_started')
            now[0]=130;ledger.append('proof_commit',theory='T',nodes=[
                {'uid':'c','kind':'contract','status':'green'}])
            now[0]=140;ledger.append('plan_accepted',allocation={})
            summary=experiment_summary(ledger)
            self.assertEqual(summary['elapsed_seconds'],20)
            self.assertEqual(summary['wall_elapsed_seconds'],20)
            self.assertEqual([r['contracts_green'] for r in summary['curve']],[1,0])
            self.assertEqual(summary['historical_first_green_contract_ids'],['c'])
            self.assertEqual(summary['current_valid_contract_ids'],[])

    def test_observed_missing_import_revokes_affected_current_proofs_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger=Ledger(tmp,'run')
            usage=UsageSnapshots(ledger)
            usage.record('t',{'totalTokens':50},scope={'target_ids':['a']},source='test')
            for theory,contract in (('A','a'),('B','b'),('C','c')):
                ledger.append('proof_commit',theory=theory,nodes=[
                    {'uid':contract,'kind':'contract','status':'green'},
                    {'uid':'helper:'+theory+':h','kind':'helper','status':'green'}])
                ledger.append('seal_result',theory=theory,status='sealed')
            ledger.append('source_missing',missing_sources=['A.thy'],
                          affected_theories=['A','B'])
            summary=experiment_summary(ledger)
            self.assertEqual(summary['historical_first_green_contract_ids'],['a','b','c'])
            self.assertEqual(summary['current_valid_contract_ids'],['c'])
            self.assertEqual(summary['current_valid_helper_ids'],['helper:C:h'])
            self.assertEqual(summary['current_valid_sealed_theories'],['C'])
            self.assertEqual(summary['targets']['b']['status'],'stale_after_source_missing')
            self.assertEqual(summary['curve'][-1]['contracts_green'],1)
            self.assertEqual(summary['usage_accounting']['total_tokens'],50)


if __name__=='__main__':
    unittest.main()
