from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from regen.execution.proof_commits import durable,digest
from regen.infrastructure.experiment_ledger import Ledger
from regen.infrastructure.effective_time_budget import EffectiveTimeBudget,POLICY
from regen.infrastructure.checkpoint_budget import carry_checkpoint_budget,state_at_cutoff


class CheckpointBudgetTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.source=Path(self.tmp.name)/'old';self.target=Path(self.tmp.name)/'new'
        self.now=[0.];self.run={'run_id':'old'};self.policy={'native_slots':2}
        durable(self.source/'run.json',self.run);durable(self.target/'run.json',{'run_id':'new'})
        durable(self.source/'parallel-workspace-policy.json',self.policy)
        self.ledger=Ledger(self.source/'effective-generation-budget','old',clock=lambda:self.now[0])
        self.ledger.append('parallel_budget_binding',policy=POLICY,run_sha256=digest(self.run),
            configuration_sha256=digest(self.policy),contracts_sha256=digest({'A':['a'],'B':['b']}))
        stage=EffectiveTimeBudget(self.ledger,seconds=100,monotonic=lambda:self.now[0])
        stage.register({'A':['a'],'B':['b']},baseline_green=['a'])
        stage.select('A',worker_id='A');stage.select('B',worker_id='B')
        self.now[0]=5;excluded=stage.begin_exclusion('prefix_preparation',worker_id='B')
        self.now[0]=15;stage.end_exclusion(excluded)
        self.now[0]=20;stage.observe('b',{'status':'green'},kind='validation',theory='B')
        self.now[0]=30;stage.cancel('provider_network_failure');stage.close()
        self.original=deepcopy(self.ledger.rows)
        self.checkpoint={'source_run':str(self.source),'theories':{'A':{'commit_id':'pre-fault'}}}
        durable(self.source/'theories/A/acceptance/commits/000001.json',
                {'commit_id':'pre-fault','state':{'proofs':{'a':'by simp'}}})
        durable(self.target/'checkpoint.json',self.checkpoint)
        durable(self.target/'workspace-continuation.json',{'source_run':str(self.source)})
        self.review={'checkpoint_sha256':digest(self.checkpoint),
            'source_tail_event_id':self.ledger.rows[-1]['event_id'],
            'disposition':'exclude_entire_post_fault_branch_and_results',
            'fault_start_unix':10.,'proof_cutoff_unix':9.,'reviewed_retained_uncertainty_ids':[]}

    def carry(self,**kw):
        return carry_checkpoint_budget(self.source,self.target,authorization='user',review={**self.review,**kw})

    def test_restore_keeps_prefix_cost_not_future_green_or_downtime(self):
        result=self.carry()
        self.assertEqual(result['carried_effective_seconds'],10)
        self.assertEqual(result['carried_worker_seconds'],15)
        self.assertEqual(result['excluded_branch_effective_seconds'],20)
        self.assertEqual(self.ledger.rows,self.original)
        now=[1000.]
        stage=EffectiveTimeBudget(Ledger(self.target/'effective-generation-budget','new'),
            seconds=100,resume_authorization='user',monotonic=lambda:now[0])
        self.addCleanup(stage.close)
        self.assertEqual(stage.green_contract_ids,{'a'})
        self.assertEqual(stage.unclean_recoveries,0)
        now[0]+=1000
        self.assertEqual(stage.snapshot()['effective_seconds'],10)
        stage.select('A',worker_id='A');now[0]+=7
        self.assertEqual(stage.snapshot()['effective_seconds'],17)
        with self.assertRaisesRegex(ValueError,'already exists'):self.carry()

    def test_rejects_future_results_stale_review_and_unknown_cutoff(self):
        for changes in ({'source_tail_event_id':'wrong'}, {'checkpoint_sha256':'wrong'},
                        {'fault_start_unix':25.}, {'proof_cutoff_unix':11.},
                        {'fault_start_unix':100.}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):self.carry(**changes)
        self.assertFalse((self.target/'effective-generation-budget').exists())

    def test_rejects_wall_clock_jump(self):
        rows=deepcopy(self.ledger.rows)
        for row in rows:
            if row['time_unix']==15:row['time_unix']=18.
        with self.assertRaisesRegex(ValueError,'drift'):state_at_cutoff(rows,10.)

    def test_cutoff_does_not_mutate_source(self):
        state,report=state_at_cutoff(self.ledger.rows,10.)
        self.assertEqual(state['theory_seconds'],{'A':10.,'B':5.})
        self.assertTrue(state['paused']);self.assertFalse(state['gate_open'])
        self.assertEqual(report['wall_monotonic_bracket_residual_seconds'],0)
        self.assertEqual(self.original,self.ledger.rows)
