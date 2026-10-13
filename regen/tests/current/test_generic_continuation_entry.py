import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from regen.infrastructure.incremental_log import enable,write_artifact,read_artifact
from regen.infrastructure.experiment_ledger import Ledger
from regen.infrastructure.generic_continuation import prepare,prompt

class ContinuationTests(unittest.TestCase):
    def fixture(self,tmp):
        source=Path(tmp)/'old';source.mkdir();enable(source)
        dataset=Path(tmp)/'dataset';dataset.mkdir()
        config=dict(model='gpt-6-astra',effort='medium',threads=4,transport_idle_timeout=900,preflight_seconds=900,check_timeout=900,candidate_timeout=120,run_id='old')
        current={'epoch':4,'run':str(source/'proof-runs/0004'),'plan_id':'plan','allocation':{'T':['c']}}
        for name,value in [('launch.json',{'config':config,'dataset':str(dataset),'inactivity_seconds':7200}),('current-plan.json',current),('result.json',{'passed':False,'error':{'message':'invalid limit60'}}),('planning-draft.json',{'epoch':4}),('plans/0004/control/state.json',{'sequence':1})]:
            write_artifact(source/name,value,immutable=True)
        ledger=Ledger(source,'old',clock=lambda:10)
        ledger.append('experiment_started');ledger.append('plan_accepted',**current)
        ledger.append('usage_snapshot',thread_id='old-thread',total={'totalTokens':100},delta={'totalTokens':100},scope={})
        ledger.append('experiment_finished',passed=False)
        out=Path(tmp)/'new';out.mkdir();enable(out)
        return source,out,dataset,config,ledger
    def test_authorized_fork_preserves_event_chain_and_operational_mapping(self):
        with tempfile.TemporaryDirectory() as tmp:
            source,out,dataset,config,old=self.fixture(tmp)
            def fork(a,b):self.assertEqual(a,source/'proof-runs/0004');b.mkdir(parents=True)
            with patch('regen.execution.repository_execution.fork_workspace_continuation',side_effect=fork):
                ledger,binding=prepare(out,source,dataset,{**config,'run_id':'new'},7200)
            self.assertEqual(ledger.rows,old.rows);self.assertEqual(ledger.run_id,'old')
            self.assertEqual(read_artifact(out/'current-plan.json')['run'],str(out/'proof-runs/0004'))
            self.assertEqual(read_artifact(source/'current-plan.json')['run'],str(source/'proof-runs/0004'))
            self.assertEqual(read_artifact(out/'plans/0004/control/state.json'),{'sequence':1})
            self.assertFalse(binding['pending_tool_replayed']);self.assertFalse(binding['old_thread_restored'])
            self.assertIn('1..50',prompt(binding))
            ledger.append('experiment_resumed');self.assertEqual(len(Ledger(source,'old').rows),4)
    def test_continuation_of_continuation_validates_original_pointer_binding(self):
        with tempfile.TemporaryDirectory() as tmp:
            source,out,dataset,config,_=self.fixture(tmp)
            with patch('regen.execution.repository_execution.fork_workspace_continuation',side_effect=lambda a,b:b.mkdir(parents=True)):
                ledger,_=prepare(out,source,dataset,config,7200)
                write_artifact(out/'launch.json',read_artifact(source/'launch.json'))
                write_artifact(out/'result.json',{'passed':False})
                third=Path(tmp)/'third';third.mkdir();enable(third)
                again,_=prepare(third,out,dataset,config,7200)
                self.assertEqual(again.rows,ledger.rows)
                self.assertEqual(read_artifact(third/'current-plan.json')['epoch'],4)

    def test_reject_changed_model_or_dataset_before_fork(self):
        with tempfile.TemporaryDirectory() as tmp:
            source,out,dataset,config,_=self.fixture(tmp)
            with patch('regen.execution.repository_execution.fork_workspace_continuation') as fork:
                with self.assertRaisesRegex(ValueError,'model'):prepare(out,source,dataset,{**config,'model':'other'},7200)
                with self.assertRaisesRegex(ValueError,'dataset'):prepare(out,source,Path(tmp)/'different',config,7200)
                fork.assert_not_called()
    def test_current_plan_tamper_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            source,out,dataset,config,_=self.fixture(tmp)
            current=read_artifact(source/'current-plan.json');current['allocation']={'T':['different']}
            write_artifact(source/'current-plan.json',current)
            with self.assertRaisesRegex(ValueError,'accepted ledger'):prepare(out,source,dataset,config,7200)
