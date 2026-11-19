"""Reproduce the user-confirmed fresh Refine inputs for the 20261110 B pair.

Run from the repository root; staging must not already exist. No credentials
or historical generated proofs are copied.
"""
import hashlib,json,subprocess,tarfile,io,shutil
from pathlib import Path
P=Path.cwd();S=Path('/tmp/refine-astra-low48h-machine-b-20261110-001');S.mkdir(exist_ok=False)
# Freeze the reviewed VERO commit, not unrelated working-tree edits.
raw=subprocess.check_output(['git','-C','vero-isabelle','archive','876a558','src/vero'])
with tarfile.open(fileobj=io.BytesIO(raw)) as t:t.extractall(S/'snapshot',filter='data')
(S/'backend').mkdir();shutil.move(str(S/'snapshot/src/vero'),S/'backend/vero');shutil.rmtree(S/'snapshot')
(S/'control').mkdir();(S/'input').mkdir()
for f in ['official_codex_runtime.py','official_codex_observer.py','official_runtime_clock.py']:
 shutil.copyfile(P/'c-algorithms/binomial/tools'/f,S/'control'/('run.py' if f=='official_codex_runtime.py' else f))
source=json.loads((P/'vero-isabelle/benchmarks/RefineIsabelleV2/benchmark.json').read_text());records=[]
for c in source['contracts']:
 records.append(dict(contract_id=c['source_id'],name=c['name'],exact_statement=c['statement'],contract_owned_context=c['context'],owner_theory=c['module'][5:]))
(S/'input/contracts.jsonl').write_text(''.join(json.dumps(v)+'\n' for v in records))
(S/'input/reference-binding.json').write_text(json.dumps({'original_benchmark_id':source['benchmark_id'],'provenance':source['provenance'],'contracts':287,'premises':84281,'inherited_proofs':0},indent=2)+'\n')
import sys;sys.path.insert(0,str(S/'backend'))
from vero.isabelle.benchmark import prepare,render
for method,short in [('vero','vero'),('official-codex','codex')]:
 name=f'refine-{short}-astra-low48h-machine-b-20261110-001';root=S/'runs'/name
 for d in ['benchmark','control','runtime','sandbox','results','agent-home','writable-heaps']:(root/d).mkdir(parents=True,exist_ok=False)
 bench=prepare(S/'input/contracts.jsonl',root/'benchmark',base_import=source['base_import'],isabelle_api='legacy',owner_blind=True,provenance={**source['provenance'],'fresh':True,'original_benchmark_id':source['benchmark_id'],'inherited_proofs':0})
 assert [(x['name'],x['statement'],x['context']) for x in bench['contracts']]==[(x['name'],x['statement'],x['context']) for x in source['contracts']]
 assert all(x['module']=='Vero_Fixed_Targets' and x['source_id'].startswith('goal:') for x in bench['contracts'])
 render(bench,root/'sandbox');shutil.copyfile(root/'benchmark/benchmark.json',root/'sandbox/benchmark.json')
 shutil.copyfile(P/'baselines/shared-harness/tool.py',root/'control/tool.py')
 for f in ['AGENTS.md','PROMPT.txt']:
  text=(P/'baselines'/method/f).read_text().replace('{{budget_seconds}}','172800').replace('{{budget_hours}}','48')
  text+='\nThis fresh Refine ARM experiment has 287 fixed targets and 84,281 legal parent facts. No historical generated proof is available. The total allowance is 172800 effective seconds; the last 7200 seconds are reserved for independent final evaluation, so generation must finish by 165600 effective seconds. Read the cumulative budget via the common tool. Only one native operation may run at a time within this experiment. The other experiment has a distinct native gate. Do not launch a prover directly. If a genuine infrastructure/accounting blocker or a question requiring human input prevents work, report it and stop; do not wait indefinitely for a response.\n'
  (root/'control'/f).write_text(text)
 shutil.copyfile(root/'control/AGENTS.md',root/'sandbox/AGENTS.md')
 (root/'runtime/official').mkdir()
 (root/'runtime/isabelle').write_text('#!/bin/bash\nset -eu\nexec flock -x --no-fork /run/native-gate/slot.lock /workspace/verification/isabelle/bin/isabelle "$@"\n');(root/'runtime/isabelle').chmod(0o755)
 checker={'backend':'fresh-process-v1','session':'Semantic_Base','session_dirs':['/sel4-project/verification/l4v','/semantic-base'],'isabelle':'/runtime/isabelle','threads':4,'timeout_seconds':1800,'total_timeout_seconds':1800,'env':{'HOME':'/home/agent','USER_HOME':'/home/agent','L4V_ARCH':'ARM'}}
 (root/'benchmark/checker.json').write_text(json.dumps(checker,indent=2)+'\n')
 cfg={'run_id':name,'method':method,'observation_profile':'official-otel-metadata-v1','model':'gpt-6-astra','reasoning_effort':'low','budget_seconds':172800,'final_evaluation_reserve_seconds':7200,'isabelle_api':'legacy','base_import':source['base_import'],'resources':{'worker':1,'native_slots':1,'resident_limit':1,'cpus':4,'memory_bytes':17179869184,'memory_swap_bytes':17179869184,'native_threads':4,'ML_maxheap_mib':8192,'java_maxheap_gib':2},'checker':checker,'final_checker':{**checker,'command_prefix':['runuser','-u','checker','--'],'env':{'HOME':'/home/checker','USER_HOME':'/home/checker','L4V_ARCH':'ARM'}},'input_binding':{'dataset_binding':source['provenance']['dataflow']['dataflow_id'],'benchmark_source_id':source['benchmark_id']},'comparison_groups':{'counts':{'contracts':287,'owners':21}},'command':['runuser','-u','agent','--','/runtime/official/codex','exec','--json','--skip-git-repo-check','--cd','/sandbox','--dangerously-bypass-approvals-and-sandbox','--model','gpt-6-astra','-c','model_reasoning_effort="low"','-c','features.multi_agent=false','-c','features.multi_agent_v2=false','-c','web_search="disabled"','-']}
 (root/'control/config.json').write_text(json.dumps(cfg,indent=2)+'\n')
manifest={}
for p in S.rglob('*'):
 if p.is_file():manifest[str(p.relative_to(S))]=hashlib.sha256(p.read_bytes()).hexdigest()
(S/'source-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
print(S,'files',len(manifest),'bytes',sum(p.stat().st_size for p in S.rglob('*') if p.is_file()))
