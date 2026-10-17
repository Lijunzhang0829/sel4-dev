"""Model-free VERO CLI fault injection through application recovery boundaries.

The executable below is a local scripted CLI, never a provider request. A fake
runner publishes an already evaluated checkpoint; the production takeover,
classification, observer, ledger and wall budget make the recovery decision.
This deliberately does not substitute for an Isabelle/native-check or live
container acceptance test.
"""
import json
from pathlib import Path
import sys
import tempfile
import unittest

from regen.infrastructure.active_wall_budget import ActiveWallBudget
from regen.infrastructure.experiment_activity import observed_usage
from regen.infrastructure.experiment_ledger import Ledger
from regen.infrastructure.vero_experiment_observer import VeroObserver
from regen.tools.vero_serial_application import (
    ProviderRecoveryGate,ProviderRecoveryPause,classify_agent_invocation,
    create_fresh_plan,interrupted_result,run_with_agent_takeover,
)

VERO_SOURCE=Path(__file__).resolve().parents[3]/'vero-isabelle'/'src'
added_source=str(VERO_SOURCE) not in sys.path
if added_source:sys.path.insert(0,str(VERO_SOURCE))
try:
    from vero.isabelle.agent import CodexAgent
finally:
    if added_source:sys.path.remove(str(VERO_SOURCE))


FAKE_CODEX='''#!/usr/bin/env python3
import json
from pathlib import Path
import sys

workspace=Path.cwd()
case=json.loads((workspace/'fake-cli-case.json').read_text())
step_path=workspace/'.fake-cli-step'
step=int(step_path.read_text())+1 if step_path.exists() else 1
step_path.write_text(str(step))
sys.stdin.read()
thread=workspace.name+'-'+str(step)
print(json.dumps({'type':'thread.started','thread_id':thread}),flush=True)
print(json.dumps({'type':'turn.completed','usage':{
    'input_tokens':7 if step==1 else 14,
    'output_tokens':3 if step==1 else 6,
    'total_tokens':10 if step==1 else 20}}),flush=True)
if case['mode']=='generation' and step==1:
    accepted=workspace/'accepted-tool-calls.txt'
    accepted.write_text((accepted.read_text() if accepted.exists() else '')+'accepted\\n')
if step==1 or case['mode']=='auth':
    code=401 if case['mode']=='auth' else 503
    print(json.dumps({'type':'turn.failed','error':{
        'message':'HTTP '+str(code)+' fake provider failure'}}),flush=True)
    sys.exit(1)
if case['mode']=='planning':
    (workspace/'PLAN.json').write_text(json.dumps({'version':1,
        'source_benchmark_id':'bench','modules':[{'name':'Vero_A',
            'contracts':['c1'],'imports':[],'rationale':'fake fixture'}],
        'execution_order':['Vero_A']}))
sys.exit(0)
'''


class VeroCLIFaultInjectionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.cli=self.root/'fake-codex'
        self.cli.write_text(FAKE_CODEX)
        self.cli.chmod(0o700)
        self.tick=[0.0]
        self.ledger=Ledger(self.root/'ledger','fault-injection')
        self.ledger.append('experiment_started',model='scripted',reasoning_effort='none')
        self.stop=ActiveWallBudget(self.ledger,3600,
            monotonic=lambda:self.tick[0])
        self.addCleanup(self.stop.close)
        self.bench={'benchmark_id':'bench','contracts':[
            {'id':'c1','module':'Vero_A'}]}
        self.observer=VeroObserver(self.ledger,self.stop,self.root/'workspace',self.bench)

    def agent_class(self,gate=None):
        owner=self
        class ScriptedAgent(CodexAgent):
            def run(self,**kwargs):
                workspace=Path(kwargs['sandbox_dir'])
                case_path=workspace/'fake-cli-case.json'
                if not case_path.exists():
                    case_path.write_text(json.dumps({'mode':'planning'}))
                mode=json.loads(case_path.read_text())['mode']
                if gate and mode!='planning':gate.generation_started()
                result=super().run(**kwargs)
                if not result['ok']:
                    result['provider_failure']=classify_agent_invocation(self.log_dir)
                owner.observer.usage_events(self.log_dir/'agent_events.jsonl',
                    self.log_dir.parent.name,final=True)
                step=int((workspace/'.fake-cli-step').read_text())
                owner.tick[0]+=({1:12,2:8}[step] if mode=='planning'
                    else {1:11,2:7}.get(step,0))
                owner.stop.check()
                return result
        return ScriptedAgent

    def config(self):
        home=self.root/'home';home.mkdir(exist_ok=True)
        (home/'.isabelle').mkdir(exist_ok=True)
        return {'codex':str(self.cli),'home':str(home),'model':'scripted',
            'reasoning_effort':'medium','timeout_seconds':10,
            'sandbox_mode':'workspace-write'}

    def test_503_keeps_planning_and_accepted_work_then_recovers_checkpoint(self):
        config={'agent':self.config()}
        plan,planned=create_fresh_plan(root=self.root,bench=self.bench,
            config=config,Agent=self.agent_class(),stop=self.stop,
            ledger=self.ledger,compile_plan=lambda bench,plan:{**bench,
                'contracts':[{'id':'c1','module':'Vero_A'}]},
            clock=lambda:self.tick[0],
            sleep=lambda delay:self.tick.__setitem__(0,self.tick[0]+delay))
        self.assertEqual(plan['execution_order'],['Vero_A'])
        self.assertEqual(planned['contracts'][0]['module'],'Vero_A')
        self.assertEqual(self.stop.snapshot()['active_wall_seconds'],20)
        self.assertEqual(observed_usage(self.ledger)['total_tokens'],30)

        workspace=self.root/'workspace';workspace.mkdir()
        (workspace/'fake-cli-case.json').write_text('{"mode":"generation"}')
        instruction=workspace/'INSTRUCTION.md';instruction.write_text('scripted proof')
        journal=self.root/'iterations.json'
        self.stop.register({'Vero_A':['c1']});self.stop.select('Vero_A')
        gate=ProviderRecoveryGate(self.stop,self.ledger)
        Agent=self.agent_class(gate)
        calls=[];checks=[]
        def run_once(recovering):
            calls.append(recovering)
            if recovering:
                checkpoint=json.loads(journal.read_text())
                self.assertEqual(checkpoint['status'],'running')
                self.assertEqual(checkpoint['iterations'][0]['proved_specs'],1)
                self.assertEqual(checkpoint['formal_takeover_count'],1)
                self.assertEqual((workspace/'accepted-tool-calls.txt').read_text(),
                    'accepted\n')
            agent=Agent(config['agent'],self.root/f'gen-{len(calls):04d}'/'agent')
            result=agent.run(sandbox_dir=workspace,instruction_file=instruction)
            if not recovering:
                checks.append('evaluated-checkpoint')
                journal.write_text(json.dumps({'status':'agent_error','iterations':[
                    {'index':0,'proved_specs':1,'agent':result}]}))
                return {'status':'agent_error'}
            return {'status':'complete'}
        result=run_with_agent_takeover(run_once,journal_path=journal,
            ledger=self.ledger,stop=self.stop,
            load=lambda path:json.loads(Path(path).read_text()),
            save=lambda path,value:Path(path).write_text(json.dumps(value)),
            recovery_gate=gate,clock=lambda:self.tick[0],
            sleep=lambda delay:self.tick.__setitem__(0,self.tick[0]+delay))
        self.assertEqual(result['status'],'complete')
        self.assertEqual(calls,[False,True])
        self.assertEqual(checks,['evaluated-checkpoint'])
        self.assertEqual((workspace/'accepted-tool-calls.txt').read_text(),'accepted\n')
        self.assertEqual(self.stop.snapshot()['active_wall_seconds'],38)
        self.assertEqual(observed_usage(self.ledger)['total_tokens'],60)
        self.assertEqual(sum(row['kind']=='agent_provider_takeover'
            for row in self.ledger.rows),1)
        self.assertEqual(sum(row['kind']=='provider_recovery_scheduled'
            for row in self.ledger.rows),1)

    def test_401_pauses_without_second_cli_or_checkpoint_replay(self):
        workspace=self.root/'workspace';workspace.mkdir()
        (workspace/'fake-cli-case.json').write_text('{"mode":"auth"}')
        instruction=workspace/'INSTRUCTION.md';instruction.write_text('scripted proof')
        journal=self.root/'iterations.json';config=self.config()
        gate=ProviderRecoveryGate(self.stop,self.ledger)
        Agent=self.agent_class(gate)
        calls=[]
        def run_once(recovering):
            calls.append(recovering)
            agent=Agent(config,self.root/f'auth-{len(calls):04d}'/'agent')
            result=agent.run(sandbox_dir=workspace,instruction_file=instruction)
            journal.write_text(json.dumps({'status':'agent_error','iterations':[
                {'index':0,'proved_specs':0,'agent':result}]}))
            return {'status':'agent_error'}
        with self.assertRaises(ProviderRecoveryPause) as raised:
            run_with_agent_takeover(run_once,journal_path=journal,
                ledger=self.ledger,stop=self.stop,
                load=lambda path:json.loads(Path(path).read_text()),
                save=lambda path,value:Path(path).write_text(json.dumps(value)),
                recovery_gate=gate,clock=lambda:self.tick[0],
                sleep=lambda delay:self.tick.__setitem__(0,self.tick[0]+delay))
        self.assertEqual(raised.exception.reason,'provider_authentication_required')
        paused=interrupted_result(self.stop,raised.exception)
        self.assertEqual(paused['status'],'paused')
        self.assertTrue(paused['resumable'])
        self.assertEqual(calls,[False])
        self.assertEqual((workspace/'.fake-cli-step').read_text(),'1')
        self.assertEqual(self.stop.snapshot()['active_wall_seconds'],11)
        self.assertEqual(observed_usage(self.ledger)['total_tokens'],10)


if __name__=='__main__':unittest.main()
