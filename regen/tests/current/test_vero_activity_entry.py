"""Original VERO runner integration with a scripted model, no provider calls."""
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


@unittest.skipUnless(os.environ.get('VERO_ACTIVITY_SOURCE'),'requires prepared external VERO source')
class VeroEntryTests(unittest.TestCase):
    def test_plan_retry_precedes_generation_and_all_costs_are_recorded(self):
        sys.path.insert(0,os.environ['VERO_ACTIVITY_SOURCE'])
        from vero.isabelle import runner
        from vero.isabelle.benchmark import prepare,render
        from regen.tools.vero_serial_application import run
        calls=[]
        class ScriptedAgent:
            def __init__(self,config,log_dir):self.config=config;self.log_dir=Path(log_dir)
            def run(self,*,sandbox_dir,instruction_file,monitor=None):
                self.log_dir.mkdir(parents=True)
                (self.log_dir/'agent_events.jsonl').write_text(
                    json.dumps({'type':'thread.started','thread_id':str(len(calls)+1)})+'\n'+
                    json.dumps({'type':'turn.started'})+'\n')
                if monitor:monitor()
                w=Path(sandbox_dir);bench=json.loads((w/'benchmark.json').read_text())
                planning=w.name=='planning-workspace';calls.append('plan' if planning else 'prove')
                if planning:
                    plan={'version':1,'source_benchmark_id':bench['benchmark_id'],
                        'modules':[{'name':'Vero_Chosen','contracts':[c['id'] for c in bench['contracts']],
                        'imports':[],'rationale':'model-chosen test grouping'}],'execution_order':['Vero_Chosen']}
                    if calls==['plan']:plan['modules'][0]['contracts']=[]
                    (w/'PLAN.json').write_text(json.dumps(plan))
                else:
                    assert (w.parent/'accepted-plan.json').is_file()
                    assert {c['module'] for c in bench['contracts']}=={'Vero_Chosen'}
                    # The real renderer/extractor/evaluator remains in the loop.
                    for p in w.glob('*.thy'):p.unlink()
                    render(bench,w,{c['id']+':prove':'by simp' for c in bench['contracts']})
                events=[{'type':'thread.started','thread_id':str(len(calls))},
                    {'type':'turn.started'},
                    {'type':'turn.completed','usage':{'input_tokens':100,'output_tokens':10,'cached_input_tokens':80}}]
                (self.log_dir/'agent_events.jsonl').write_text(''.join(json.dumps(e)+'\n' for e in events))
                return {'ok':True,'elapsed_seconds':.01,'timed_out':False,'usage':events[-1]['usage']}
        class Checker:
            def __init__(self,dataset,output,*,timeout,observer):self.observer=observer
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def set_frozen_plan(self,binding):self.frozen_plan=binding
            def check(self,directory,theory,timeout=None):
                audited=[c['id']+':prove' for c in self.observer.bench['contracts'] if c['module']==theory]
                return {'status':'ok','log':'','audited':audited}
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);data=root/'contracts.jsonl'
            contract_ids=[f'truth_{i:03d}' for i in range(287)]
            data.write_text(''.join(json.dumps({'contract_id':uid,'name':uid,
                'owner_theory':'Original','exact_statement':f'lemma {uid}: "True"'})+'\n'
                for uid in contract_ids))
            bench=prepare(data,root/'prepared',base_import='Main')
            benchmark_ids=[c['id'] for c in bench['contracts']]
            bp=root/'benchmark.json';bp.write_text(json.dumps(bench));auth=root/'auth.json';auth.write_text('{}')
            args=SimpleNamespace(vero_source=os.environ['VERO_ACTIVITY_SOURCE'],benchmark=str(bp),output=str(root/'run'),
                dataset=str(root),client=sys.executable,model='gpt-6-astra',reasoning='medium',
                expected_contracts=287,effective_seconds=172800,
                auth=str(auth),check_timeout=3,image='sha256:'+'0'*64,proxy=None)
            with patch.object(runner,'CodexAgent',ScriptedAgent),\
                    patch('regen.tools.vero_serial_application.VeroCheckerAdapter',Checker),\
                    patch('regen.tools.vero_serial_application.load_context',
                        return_value={'checker_context_id':'scripted-test-context'}):
                result=run(args)
            self.assertEqual(result['status'],'complete',result)
            self.assertEqual(calls,['plan','plan','prove'])
            config=json.loads((root/'run/config.json').read_text())
            self.assertIsNone(config['total_agent_seconds']);self.assertIsNone(config['max_iterations'])
            self.assertEqual(config['frozen_plan']['allocation'],{'Vero_Chosen':benchmark_ids})
            binding=json.loads((root/'run/binding.json').read_text())
            self.assertEqual(binding['frozen_plan'],config['frozen_plan'])
            self.assertEqual({k:binding[k] for k in ('model','reasoning_effort',
                'expected_contract_count','fixed_contract_count','token_limit',
                'effective_seconds_limit')},
                {'model':'gpt-6-astra','reasoning_effort':'medium',
                 'expected_contract_count':287,'fixed_contract_count':287,
                 'token_limit':None,'effective_seconds_limit':172800})
            rows=[json.loads(p.read_text()) for p in sorted((root/'run/events').glob('*.json'))]
            self.assertEqual(sum(r['kind']=='plan_rejected' for r in rows),1)
            self.assertEqual(sum(r['kind']=='plan_accepted' for r in rows),1)
            accepted=next(r['data'] for r in rows if r['kind']=='plan_accepted')
            self.assertEqual(accepted['allocation_sha256'],config['frozen_plan']['allocation_sha256'])
            registered_state=next(r for r in rows
                if r['kind']=='time_budget_checkpoint' and r['data']['boundary']=='registered')
            self.assertEqual(registered_state['data']['state']['policy'],
                'global-effective-generation-budget-v1')
            registered=next(r for r in rows if r['kind']=='time_budget_registered')
            self.assertEqual(registered['data']['contract_owners'],
                {uid:'Vero_Chosen' for uid in benchmark_ids})
            summary=json.loads((root/'run/experiment-summary.json').read_text())
            self.assertEqual(sum(v['totalTokens'] for v in summary['usage_by_thread'].values()),330)
