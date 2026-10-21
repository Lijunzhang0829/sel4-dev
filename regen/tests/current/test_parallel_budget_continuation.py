"""No budget refund, no duplicate GREEN, no charge for stopped continuation gaps."""
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from regen.execution.proof_commits import durable, digest
from regen.infrastructure.experiment_ledger import Ledger
from regen.infrastructure.effective_time_budget import EffectiveTimeBudget, POLICY
from regen.infrastructure.parallel_generation_budget import carry_parallel_budget, initialize_parallel_budget


class ParallelBudgetContinuationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.source=Path(self.tmp.name)/'source';self.target=Path(self.tmp.name)/'target'
        self.run={'run_id':'source'};self.new={'run_id':'target'}
        self.policy={'strategy':'helper-closure','native_slots':2,
                     'generation_strategy':{'name':'helper-closure-v1','instructions':'old frozen policy'}}
        self.theories={'A':['a'],'B':['b']}
        durable(self.source/'run.json',self.run)
        durable(self.target/'run.json',self.new)
        durable(self.source/'parallel-workspace-policy.json',self.policy)
        durable(self.target/'workspace-continuation.json',{'source_run':str(self.source)})
        ledger=Ledger(self.source/'effective-generation-budget','source')
        ledger.append('parallel_budget_binding',policy=POLICY,run_sha256=digest(self.run),
            contracts_sha256=digest(self.theories),configuration_sha256=digest(self.policy))
        now=[0.]
        stage=EffectiveTimeBudget(ledger,seconds=100,monotonic=lambda:now[0])
        stage.register(self.theories)
        stage.select('A',worker_id='A');stage.select('B',worker_id='B')
        now[0]=20.
        stage.observe('a',{'status':'green'},kind='validation',theory='A')
        event=ledger.append('generation_clock_untrusted',evidence={
            'reason':'tool_callback_finished_with_open_model_item'})
        stage.cancel('effective_clock_evidence_error');stage.close()
        self.review={'source_tail_event_id':ledger.rows[-1]['event_id'],
            'resolved_event_ids':[event['event_id']],
            'disposition':'retain_all_charged_time_no_step_refund'}
        self.source_rows=ledger.rows

    def carry(self,**kw):
        return carry_parallel_budget(self.source,self.target,authorization='user-approved',
                                     clock_review={**self.review,**kw})

    def test_carry_restores_union_worker_green_and_no_offline_charge(self):
        result=self.carry()
        self.assertEqual(result['carried_effective_seconds'],20)
        self.assertEqual(result['carried_worker_seconds'],40)
        ledger=Ledger(self.target/'effective-generation-budget','target')
        now=[10000.]
        resumed=EffectiveTimeBudget(ledger,seconds=100,resume_authorization='user-approved',
                                    monotonic=lambda:now[0])
        self.addCleanup(resumed.close)
        resumed.register(self.theories,baseline_green=['a'])
        now[0]+=500
        self.assertEqual(resumed.snapshot()['effective_seconds'],20)
        self.assertEqual(resumed.snapshot()['green_contract_ids'],['a'])
        resumed.select('B',worker_id='B');now[0]+=5
        self.assertEqual(resumed.snapshot()['effective_seconds'],25)
        self.assertEqual(resumed.snapshot()['seconds'],100)
        self.assertEqual(sum(r['kind']=='time_budget_green' for r in ledger.rows),1)
        self.assertEqual(Ledger(self.source/'effective-generation-budget','source').rows,self.source_rows)
        with self.assertRaisesRegex(ValueError,'already exists'):self.carry()

    def test_review_cannot_silently_drop_uncertainty_or_refund_step(self):
        for kw in ({'resolved_event_ids':[]},{'source_tail_event_id':'wrong'},
                   {'disposition':'refund_last_step'}):
            with self.subTest(kw=kw),self.assertRaisesRegex(ValueError,'exact source history'):
                self.carry(**kw)
        self.assertFalse((self.target/'effective-generation-budget').exists())

    def test_checkpoint_rollback_is_not_full_continuation(self):
        durable(self.target/'checkpoint.json',{})
        with self.assertRaisesRegex(ValueError,'full latest'):self.carry()

    def test_partial_import_cannot_start_generation(self):
        ledger=Ledger(self.target/'effective-generation-budget','target')
        ledger.append('parallel_budget_continuation_started',source_run=str(self.source))
        with self.assertRaisesRegex(ValueError,'incomplete'):
            initialize_parallel_budget(self.target,SimpleNamespace(run=self.new),self.policy)

    def test_authorized_strategy_update_preserves_budget_and_records_binding(self):
        from regen.planning.parallel_file_planner import generation_strategy
        strategy=generation_strategy('helper-closure-v1')
        result=carry_parallel_budget(self.source,self.target,authorization='user-approved',
            clock_review=self.review,target_strategy=strategy)
        self.assertEqual(result['carried_effective_seconds'],20)
        rows=Ledger(self.target/'effective-generation-budget','target').rows
        binding=next(r['data'] for r in rows if r['kind']=='parallel_budget_binding')
        self.assertEqual(binding['configuration_sha256'],digest({**self.policy,'generation_strategy':strategy}))
        change=next(r['data'] for r in rows if r['kind']=='parallel_budget_strategy_update')
        self.assertEqual(change['seconds_unchanged'],100)
        self.assertEqual(change['effective_seconds_unchanged'],20)

    def test_strategy_update_cannot_smuggle_another_policy(self):
        with self.assertRaisesRegex(ValueError,'frozen implementation'):
            carry_parallel_budget(self.source,self.target,authorization='user-approved',
                clock_review=self.review,target_strategy={'name':'baseline'})


if __name__=='__main__':unittest.main()
