"""Model-free VERO recovery acceptance through the real runner and tool service.

The Codex CLI is a local scripted subprocess and the native checker is fake.
No provider, Docker container, or Isabelle process is used.
"""

import hashlib, json, sys, tempfile, threading, unittest
from pathlib import Path
from regen.infrastructure.active_wall_budget import ActiveWallBudget
from regen.infrastructure.experiment_activity import experiment_summary, observed_usage
from regen.infrastructure.experiment_ledger import Ledger
from regen.infrastructure.vero_experiment_observer import VeroObserver
from regen.tools.vero_serial_application import (ProviderRecoveryGate,ProviderRecoveryPause,Service,bind_runner,classify_agent_invocation,frozen_plan_binding,interrupted_result,run_with_agent_takeover)

SOURCE=Path(__file__).resolve().parents[3]/'vero-isabelle'/'src'
sys.path.insert(0,str(SOURCE))
from vero.isabelle import runner
from vero.isabelle.agent import CodexAgent
from vero.isabelle.benchmark import prepare,read,save
from vero.isabelle.planning import compile_plan

CLI=r'''#!/usr/bin/env python3
import json,os,re,socket,sys
from pathlib import Path
w=Path.cwd(); f=w/'.step'; step=int(f.read_text())+1 if f.exists() else 1; f.write_text(str(step))
sys.stdin.read()
print(json.dumps({'type':'thread.started','thread_id':'scripted-'+str(step)}),flush=True)
if step==1:
  for request in ({'cmd':'scope','target_ids':['c0001'],'theories':['Vero_A'],'phase':'proof'}, {'cmd':'check','module':'Vero_A'}):
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as s:
      s.connect(os.environ['FAKE_SERVICE_SOCKET']); s.sendall((json.dumps(request)+'\n').encode())
      with s.makefile('rb') as stream: raw=stream.readline()
      if not raw: raise RuntimeError('empty tool response')
      response=json.loads(raw)
      if 'error' in response: raise RuntimeError('tool error '+response['error'])
  code=401 if os.environ['FAKE_PROVIDER_CASE']=='auth' else 503
else:
  p=w/'Vero_A.thy'; old=p.read_text()
  new,n=re.subn(r'(\(\* VERO START c0001 prove \*\)\s*)oops(\s*\(\* VERO END c0001 prove \*\))',r'\g<1>by simp\g<2>',old,count=1)
  if n!=1: raise RuntimeError('missing open proof slot')
  p.write_text(new)
print(json.dumps({'type':'turn.completed','usage':{'input_tokens':7 if step==1 else 14,'output_tokens':3 if step==1 else 6,'total_tokens':10 if step==1 else 20}}),flush=True)
if step==1:
  print(json.dumps({'type':'turn.failed','error':{'message':'HTTP '+str(code)+' scripted provider failure'}}),flush=True)
  sys.exit(1)
'''

class FakeNative:
  config={}
  def __init__(self,observer): self.observer=observer; self.checked=[]
  def set_frozen_plan(self,binding): self.binding=binding
  def check(self,directory,theory,timeout=None):
    self.checked.append(theory)
    if theory=='Vero_All': return {'status':'ok','audited':[],'log':'scripted integration','elapsed_seconds':0.1}
    source=(Path(directory)/(theory+'.thy')).read_text(); audited=['c0001:prove'] if 'by simp' in source else []
    start='(* VERO START c0001 prove *)';end='(* VERO END c0001 prove *)'
    begin=source.index(start);body_start=begin+len(start);body_end=source.index(end,body_start)
    body=source[body_start:body_end].strip()
    sha=lambda value:hashlib.sha256(value.encode()).hexdigest()
    receipt={'polarity':'prove','filled':body not in ('','oops'),
      'pre_proof_sha256':sha(source[:begin]),'proof_body_sha256':sha(body),
      'prefix_sha256':sha(source[:body_end+len(end)]),'context_identity':'c'*64}
    result={'status':'ok','contract_status':'ok','audited':audited,
      'contract_receipts':{'c0001':receipt},
      'log':'VERO_RESULT c0001 prove clean' if audited else '', 'elapsed_seconds':0.1,
      'native_evidence':{'returncode':0,'scripted':True}}
    self.observer.checked(theory,result,source)
    return result
  def close(self): pass

class CountingService(Service):
  def __init__(self,*args): self.accepted=[]; super().__init__(*args)
  def dispatch(self,request):
    result=super().dispatch(request)
    if request.get('cmd') in {'scope','check'}: self.accepted.append((request['cmd'],request.get('module')))
    return result

def run(case):
  with tempfile.TemporaryDirectory(prefix='vero-fi-') as tmp:
    root=Path(tmp); cli=root/'fake-codex';cli.write_text(CLI);cli.chmod(0o700)
    home=root/'home';(home/'.isabelle').mkdir(parents=True)
    sourcefile=root/'contracts.jsonl'; sourcefile.write_text(json.dumps({'contract_id':'fixture-c1','name':'tiny','owner_theory':'A','exact_statement':'lemma tiny: "True"'})+'\n')
    source=prepare(sourcefile,root/'source',base_import='Main')
    plan={'version':1,'source_benchmark_id':source['benchmark_id'],'modules':[{'name':'Vero_A','contracts':['c0001'],'imports':[],'rationale':'one fixture'}],'execution_order':['Vero_A']}
    bench=compile_plan(source,plan); benchmark=root/'planned.json';save(benchmark,bench)
    workspace=root/'workspace'; tick=[0.0]
    ledger=Ledger(root/'ledger','fi',clock=lambda:tick[0]);ledger.append('experiment_started')
    stop=ActiveWallBudget(ledger,100,monotonic=lambda:tick[0]);stop.register({'Vero_A':['c0001']});stop.select('Vero_A')
    observer=VeroObserver(ledger,stop,workspace,bench); checker=FakeNative(observer)
    service=CountingService(root/'tool.sock',bench,workspace,root,root/'requests',checker)
    service.freeze_plan(frozen_plan_binding(bench,plan))
    thread=threading.Thread(target=service.serve_forever,daemon=True);thread.start()
    try:
      config={'sandbox':str(workspace),'total_agent_seconds':60,'max_iterations':3,'feedback':True,'resume_artifact':True,'completion_metric':'positive','checker':{},'agent':{'codex':str(cli),'home':str(home),'model':'scripted','reasoning_effort':'medium','timeout_seconds':10,'sandbox_mode':'workspace-write','env':{'FAKE_SERVICE_SOCKET':str(root/'tool.sock'),'FAKE_PROVIDER_CASE':case}}}
      cfg=root/'config.json';save(cfg,config);gate=ProviderRecoveryGate(stop,ledger)
      class Agent(CodexAgent):
        def run(self,**kwargs):
          if gate.handles: gate.generation_started()
          result=super().run(**kwargs)
          if not result['ok']: result['provider_failure']=classify_agent_invocation(self.log_dir)
          observer.usage_events(self.log_dir/'agent_events.jsonl',self.log_dir.parent.name,final=True)
          step=int((workspace/'.step').read_text());tick[0]+=11 if step==1 else 7;stop.check()
          return result
      calls=[];journal=root/'run'/'iterations.json'
      def invoke(recovering):
        calls.append(recovering)
        if recovering:
          checkpoint=read(journal)
          if (checkpoint['status']!='running'
              or len(checkpoint['iterations'])!=1
              or checkpoint.get('formal_takeover_count')!=1):
            raise AssertionError('runner did not resume the accepted checkpoint')
          if service.accepted!=[('scope',None),('check','Vero_A')]:
            raise AssertionError('accepted tools were replayed before resume')
        return runner.run(benchmark,cfg,root/'run',resume=recovering)
      with bind_runner(runner,checker,Agent):
        if case=='auth':
          try:run_with_agent_takeover(invoke,journal_path=journal,ledger=ledger,stop=stop,load=read,save=save,recovery_gate=gate,clock=lambda:tick[0],sleep=lambda delay:tick.__setitem__(0,tick[0]+delay))
          except ProviderRecoveryPause as error:result=interrupted_result(stop,error)
          else:raise AssertionError('auth failure did not pause')
        else:
          result=run_with_agent_takeover(invoke,journal_path=journal,ledger=ledger,stop=stop,load=read,save=save,recovery_gate=gate,clock=lambda:tick[0],sleep=lambda delay:tick.__setitem__(0,tick[0]+delay))
      observed={'case':case,'result':result['status'],'calls':calls,'journal_status':read(journal)['status'],'iterations':len(read(journal)['iterations']),'proved':[r['proved_specs'] for r in read(journal)['iterations']],'accepted':service.accepted,'active_wall_seconds':stop.snapshot()['active_wall_seconds'],'tokens':observed_usage(ledger)['total_tokens'],'current_green':experiment_summary(ledger)['current_valid_contract_ids']}
      return observed
    finally:
      service.shutdown();service.server_close();thread.join(timeout=3);stop.close()

class VeroRealRunnerFaultInjectionTests(unittest.TestCase):
  def test_503_resumes_checkpoint_without_replaying_accepted_tools(self):
    observed=run('transient')
    self.assertEqual(observed['result'],'complete')
    self.assertEqual(observed['accepted'],[('scope',None),('check','Vero_A')])
    self.assertEqual(observed['calls'],[False,True])
    self.assertEqual(observed['journal_status'],'complete')
    self.assertEqual(observed['iterations'],2)
    self.assertEqual(observed['proved'],[0,1])
    self.assertEqual(observed['current_green'],['c0001'])
    self.assertEqual(observed['active_wall_seconds'],18)
    self.assertEqual(observed['tokens'],30)

  def test_401_pauses_after_checkpoint_without_replaying_accepted_tools(self):
    observed=run('auth')
    self.assertEqual(observed['result'],'paused')
    self.assertEqual(observed['accepted'],[('scope',None),('check','Vero_A')])
    self.assertEqual(observed['calls'],[False])
    self.assertEqual(observed['journal_status'],'agent_error')
    self.assertEqual(observed['iterations'],1)
    self.assertEqual(observed['current_green'],[])
    self.assertEqual(observed['active_wall_seconds'],11)
    self.assertEqual(observed['tokens'],10)


if __name__=='__main__': unittest.main()
