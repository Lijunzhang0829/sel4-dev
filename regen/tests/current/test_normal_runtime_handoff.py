"""Model-free integration of reviewed planning, generation and paused resume."""
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from regen.execution.proof_commits import digest
from regen.infrastructure.experiment_ledger import Ledger
from regen.infrastructure.normal_runtime_budget import NormalRuntimeBudget, POLICY
from regen.infrastructure.parallel_generation_budget import initialize_parallel_budget


class NormalRuntimeHandoffTests(unittest.TestCase):
    def test_planning_handoff_counts_transition_and_resume_preserves_spent(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);planning=base/'planning';root=base/'proof';dataset=base/'dataset'
            now=[0.]

            def write(path,value):
                path.parent.mkdir(parents=True,exist_ok=True)
                path.write_text(json.dumps(value,sort_keys=True)+'\n')

            def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()

            settings={'reasoning_effort':'low','role_models':{'File Planner':'test-model'}}
            config=base/'models.json';write(config,settings)
            inputs=dataset/'repository-planning-input.json'
            write(inputs,{'contracts':[{'contract_id':'a'}]})
            plan={'plan_id':'test','revision':1,'theories':[{'name':'A','contract_ids':['a']}]}
            listing={'plan_id':'test','plan_revision':1,'files':[{'theory':'A','contract_ids':['a']}]}
            plan_path=planning/'plan.json';write(plan_path,plan)
            list_path=planning/'file-list.json';write(list_path,listing)
            frozen=planning/'frozen-plan.json'
            write(frozen,{'state':'frozen','plan':str(plan_path),'file_generation_list':str(list_path),
                          'plan_sha256':sha(plan_path)})
            source=Ledger(planning/'budget',planning.name,clock=lambda:now[0])
            stage=NormalRuntimeBudget(source,100,monotonic=lambda:now[0])
            stage.begin_planning();now[0]=20;stage.end_planning();stage.close()
            write(planning/'budget-receipt.json',{'schema_version':1,'state':'complete','policy':POLICY,
                'include_planning':True,'seconds':100,'input_sha256':sha(inputs),
                'frozen_plan_sha256':sha(frozen),'repository_plan_sha256':sha(plan_path),
                'file_generation_list_sha256':sha(list_path),'ledger_run_id':planning.name,
                'ledger_tail_event_id':source.rows[-1]['event_id'],
                'model':'test-model','reasoning_effort':'low'})
            run={'run_id':'proof','execution_input_kind':'refine-dataset',
                'checker_context_root':str(dataset),'model_config_path':str(config),
                'execution_models':settings['role_models'],'session_schedule':{
                    'source':str(list_path),'file_list_sha256':digest(listing),
                    'plan_id':'test','plan_revision':1}}
            overview=SimpleNamespace(run=run,list_workspace=lambda:{'theories':[
                {'theory':'A','contracts':[{'id':'a'}],'green_count':0}]})

            def ledger_factory(*args,**kwargs):return Ledger(*args,clock=lambda:now[0],**kwargs)
            def budget_factory(*args,**kwargs):return NormalRuntimeBudget(*args,monotonic=lambda:now[0],**kwargs)

            with patch('regen.infrastructure.parallel_generation_budget.Ledger',side_effect=ledger_factory), \
                    patch('regen.infrastructure.normal_runtime_budget.NormalRuntimeBudget',side_effect=budget_factory), \
                    patch('regen.infrastructure.audited_llm.model_settings',return_value=settings):
                now[0]=30
                generation,bridge=initialize_parallel_budget(root,overview,{'model':'test-model'},
                    seconds=100,planning_budget=planning,clock_mode='normal-runtime')
                self.addCleanup(generation.close)
                self.addCleanup(bridge.close)
                self.assertEqual(generation.snapshot()['effective_seconds'],30)
                self.assertEqual(generation.snapshot()['planning_seconds'],30)
                bridge.handle('theory_generation_started',{'theory':'A'})
                bridge.handle('generation_exclusion_started',{
                    'theory':'A','phase_id':'prefix','category':'prefix_preparation'})
                now[0]=40
                bridge.handle('generation_exclusion_finished',{'theory':'A','phase_id':'prefix'})
                self.assertEqual(generation.snapshot()['effective_seconds'],40)
                now[0]=45;generation.cancel('operator_pause');generation.close()
                now[0]=100
                resumed,second_bridge=initialize_parallel_budget(root,overview,{'model':'test-model'},
                    seconds=100,clock_mode='normal-runtime',resume_authorization='resume approved')
                self.addCleanup(resumed.close)
                self.addCleanup(second_bridge.close)
                self.assertEqual(resumed.snapshot()['effective_seconds'],45)
                self.assertEqual(resumed.snapshot()['remaining_seconds'],55)
                self.assertEqual(resumed.snapshot()['excluded_experiment_seconds'],55)


if __name__=='__main__':unittest.main()
