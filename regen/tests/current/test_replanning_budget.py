"""Recovery planning adds measured cost without replacing the proof clock."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from regen.execution.proof_commits import digest
from regen.infrastructure.effective_time_budget import EffectiveTimeBudget, CHECKPOINT_KIND, POLICY
from regen.infrastructure.experiment_ledger import Ledger
from regen.infrastructure.parallel_generation_budget import (
    carry_parallel_budget, review_parallel_budget_clock, initialize_parallel_budget)
from regen.infrastructure import reviewed_planning_budget as planning_module
from regen.infrastructure.replanning_budget import (
    append_replanning_budget, validate_replanning_imports, COMPLETED, EVIDENCE)


class ReplanningBudgetTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source, self.target = self.root/'old', self.root/'new'
        self.planning = self.root/'planning'
        for path in (self.source, self.target, self.planning):
            path.mkdir()
        self.settings = {'reasoning_effort':'low', 'role_models':{'File Planner':'test-model'}}
        self.config = self.root/'models.json'
        self.write(self.config, self.settings)
        self.input = self.root/'dataset/repository-planning-input.json'
        self.write(self.input, {'contracts':[{'contract_id':'a'}]})
        self.policy = {'model':'test-model'}
        self.old_run = {'run_id':'old'}
        self.write(self.source/'run.json', self.old_run)
        self.write(self.source/'parallel-workspace-policy.json', self.policy)
        ledger = Ledger(self.source/'effective-generation-budget', 'old')
        ledger.append('parallel_budget_binding', policy=POLICY, run_sha256=digest(self.old_run),
            contracts_sha256=digest({'A':['a']}), configuration_sha256=digest(self.policy))
        now = [0.]
        budget = EffectiveTimeBudget(ledger, seconds=100, include_planning=True,
                                    monotonic=lambda:now[0])
        budget.begin_planning(); now[0] += 2; budget.end_planning()
        budget.register({'A':['a']}); budget.select('A'); now[0] += 3
        budget.begin_provider_request('default', 'old-pending')
        now[0] += 1
        budget.finish_provider_request('default', 'old-pending', outcome='failed')
        budget.cancel('operator_pause'); budget.close()
        self.before = deepcopy(ledger.rows[-1]['data']['state'])
        self.source_tail = ledger.rows[-1]['event_id']
        self.remaining = 100-self.before['effective_seconds']
        self.patch = patch('regen.infrastructure.audited_llm.model_settings', return_value=self.settings)
        self.patch.start(); self.addCleanup(self.patch.stop)

    @staticmethod
    def write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, sort_keys=True)+'\n')

    def prepare(self, *, cap=None, source_tail=None, charge=7., retry_credit=False):
        now = [0.]
        def create(ledger, **kwargs):
            return EffectiveTimeBudget(ledger, monotonic=lambda:now[0], **kwargs)
        binding = dict(model='test-model', reasoning_effort='low',
            input_sha256=planning_module.file_hash(self.input),
            recovery_source_run=str(self.source),
            recovery_source_tail_event_id=source_tail or self.source_tail)
        with patch.object(planning_module, 'EffectiveTimeBudget', side_effect=create):
            budget = planning_module.ReviewedPlanningBudget(self.planning,
                seconds=self.remaining if cap is None else cap, binding=binding)
        plan = {'plan_id':'replanned', 'revision':2, 'theories':[{'name':'A','contract_ids':['a']}]}
        listing = {'plan_id':'replanned','plan_revision':2,'files':[{'theory':'A','contract_ids':['a']}]}
        plan_path, list_path = self.planning/'plan.json', self.planning/'files.json'
        self.write(plan_path, plan); self.write(list_path, listing)
        frozen = dict(state='frozen', plan=str(plan_path), file_generation_list=str(list_path),
                      plan_sha256=planning_module.file_hash(plan_path))
        budget.stage.begin_planning()
        if retry_credit:
            budget.request_event('planning_request_started', {'request_id':'failed'})
            now[0] += 2
            budget.request_event('planning_request_finished', {'request_id':'failed','outcome':'timeout'})
            budget.request_event('planning_request_started', {'request_id':'success','retry_of':'failed'})
            now[0] += charge
            budget.request_event('planning_request_finished', {'request_id':'success','outcome':'success'})
            budget.request_event('planning_request_retry_succeeded',
                {'failed_request_id':'failed','successful_request_id':'success'})
        else:
            now[0] += charge
        budget.complete(frozen)
        self.new_run = dict(run_id='new', execution_models={'File Planner':'test-model'},
            model_config_path=str(self.config), execution_input_kind='refine-dataset',
            checker_context_root=str(self.input.parent),
            session_schedule=dict(source=str(list_path), file_list_sha256=digest(listing),
                                  plan_id='replanned', plan_revision=2))
        self.write(self.target/'run.json', self.new_run)
        self.write(self.target/'parallel-workspace-policy.json', self.policy)
        self.write(self.target/'workspace-continuation.json', {'source_run':str(self.source)})
        review = review_parallel_budget_clock(self.source)
        carry_parallel_budget(self.source,self.target,authorization='test recovery',
                              clock_review=review['clock_review'])
        return budget

    def append(self):
        return append_replanning_budget(self.source,self.target,self.planning,
                                       authorization='authorized replan')

    def test_adds_only_new_planning_cost_and_preserves_failed_request_history(self):
        self.prepare()
        result = self.append()
        ledger = Ledger(self.target/'effective-generation-budget','new')
        after = [r['data']['state'] for r in ledger.rows if r['kind']==CHECKPOINT_KIND][-1]
        self.assertEqual(after['seconds'],100)
        self.assertEqual(after['effective_seconds'],self.before['effective_seconds']+7)
        self.assertEqual(after['planning_seconds'],self.before['planning_seconds']+7)
        self.assertEqual(after['worker_seconds'],self.before['worker_seconds'])
        self.assertEqual(after['theory_seconds'],self.before['theory_seconds'])
        self.assertEqual(after['provider_request_accounting']['requests'],
                         self.before['provider_request_accounting']['requests'])
        self.assertEqual(after['provider_request_accounting']['segments'],
                         self.before['provider_request_accounting']['segments'])
        self.assertEqual(result['refund_seconds'],0)
        self.assertEqual(validate_replanning_imports(self.target),1)
        resumed = EffectiveTimeBudget(ledger,seconds=100,include_planning=True,
                                     resume_authorization='resume')
        self.assertEqual(resumed.snapshot()['effective_seconds'],after['effective_seconds'])
        resumed.cancel('test complete');resumed.close()

    def test_retry_exclusion_only_affects_new_planning_raw_cost(self):
        self.prepare(retry_credit=True)
        self.append()
        ledger=Ledger(self.target/'effective-generation-budget','new')
        state=[r['data']['state'] for r in ledger.rows if r['kind']==CHECKPOINT_KIND][-1]
        self.assertEqual(state['effective_seconds'],self.before['effective_seconds']+7)
        self.assertEqual(state['raw_effective_seconds'],self.before['raw_effective_seconds']+9)
        self.assertEqual(state['excluded_infrastructure_seconds'],2)

    def test_duplicate_import_cannot_charge_twice(self):
        self.prepare();self.append()
        ledger=Ledger(self.target/'effective-generation-budget','new')
        tail=ledger.rows[-1]['event_id']
        with self.assertRaises(ValueError):self.append()
        self.assertEqual(Ledger(ledger.root,'new').rows[-1]['event_id'],tail)

    def test_full_allowance_cannot_be_used_for_new_planning(self):
        self.prepare(cap=100)
        with self.assertRaisesRegex(ValueError,'allowance mismatch'):self.append()
        self.assertFalse((self.planning/'budget-transfer.json').exists())

    def test_recovery_binding_requires_exact_paused_source_tail(self):
        self.prepare(source_tail='wrong')
        with self.assertRaisesRegex(ValueError,'paused source'):self.append()

    def test_plan_tampering_is_rejected_before_claim(self):
        self.prepare()
        self.write(self.planning/'plan.json', {'changed':True})
        with self.assertRaises(ValueError):self.append()
        self.assertFalse((self.planning/'budget-transfer.json').exists())

    def test_incomplete_import_fails_closed_at_startup(self):
        self.prepare()
        original=Ledger.append
        def fail(ledger,kind,**data):
            if kind==COMPLETED:raise OSError('simulated publication failure')
            return original(ledger,kind,**data)
        with patch.object(Ledger,'append',new=fail),self.assertRaises(OSError):self.append()
        with self.assertRaisesRegex(ValueError,'incomplete replanning'):validate_replanning_imports(self.target)
        with self.assertRaisesRegex(ValueError,'incomplete replanning'):
            initialize_parallel_budget(self.target,SimpleNamespace(run=self.new_run),
                seconds=100,binding=self.policy,rows=[dict(theory='A',contracts=['a'])])
        with self.assertRaises(ValueError):self.append()

    def test_inherited_replanning_import_is_verified_after_another_continuation(self):
        self.prepare();self.append()
        third=self.root/'third';third.mkdir()
        self.write(third/'run.json',{**self.new_run,'run_id':'third'})
        self.write(third/'workspace-continuation.json',{'source_run':str(self.target)})
        self.write(third/'parallel-workspace-policy.json',self.policy)
        review=review_parallel_budget_clock(self.target)
        carry_parallel_budget(self.target,third,authorization='continue',clock_review=review['clock_review'])
        self.assertEqual(validate_replanning_imports(third),1)
        self.write(self.planning/'plan.json',{'tampered':True})
        with self.assertRaisesRegex(ValueError,'evidence changed'):validate_replanning_imports(third)


if __name__=='__main__':unittest.main()
