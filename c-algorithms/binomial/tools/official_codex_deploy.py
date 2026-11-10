"""Fresh official Codex deployment; shares immutable toolchain, never writable state."""
import hashlib,json,os,shutil,subprocess,sys,time
from pathlib import Path
PROJECT=Path(__file__).resolve().parents[3]
if str(PROJECT) not in sys.path:sys.path.insert(0,str(PROJECT))
ROOT=None
NAME=None
BUDGET=None
METHOD="official-codex"
INPUT=None
BASE=None
VALIDATION_ONLY=False
TOOLS=PROJECT/'c-algorithms/toolchains/autocorres2-isabelle2026'
AFP='/screen/afp-2026/mirror-afp-2026-fca9585c6341cbf0f91816850b375c02e2c17ba6/thys'
def call(*args,**kw):return subprocess.run(list(map(str,args)),check=True,**kw)
def save(p,x):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(x,indent=2,ensure_ascii=False)+'\n')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def task_text(name,budget,method):
    text=(PROJECT/'intervaltree/baselines'/method/name).read_text()
    text=text.replace('{{budget_seconds}}',str(budget)).replace('{{budget_hours}}',format(budget/3600,'.12g'))
    if name=='PROMPT.txt':
        text=text.replace('positive proof or strict refutation',
            'positive proof or checked refutation in its original owned context')
    return text
def resolve_sources(input_root, prepared):
    from regen.preparation.calg_shuffled_dataset import inventory
    from regen.preparation.checker_context import load_context
    pointer=None
    if input_root is None:
        pointer=json.loads((PROJECT/'c-algorithms/binomial/current-dataset.json').read_text())
        input_root=PROJECT/'c-algorithms/binomial'/pointer['input_manifest']
        input_root=input_root.parent
    input_root=Path(input_root).resolve()
    manifest,targets,background=inventory(input_root)
    if pointer is not None and pointer['dataset_binding']!=manifest['dataset_binding']:
        raise ValueError('current dataset pointer binding changed')
    if manifest.get('result_scope')!='owned-context-outcomes-v1':
        raise ValueError('fresh baseline requires explicit owned-context result scope')
    if prepared is None:raise ValueError('prepare requires --prepared-dataset for the same frozen input')
    prepared=Path(prepared).resolve();context=load_context(prepared)
    receipt=json.loads((prepared/'preparation.json').read_text())
    projection=json.loads((prepared/'repository-planning-input.json').read_text())
    from regen.preparation.calg_shuffled_dataset import contracts_for
    if (receipt.get('status')!='prepared'
            or receipt['dataset_binding']!=manifest['dataset_binding']
            or projection['contracts']!=contracts_for(targets)
            or context.get('result_scope')!=manifest['result_scope']):
        raise ValueError('prepared parent/targets do not match frozen input')
    if 'comparison_groups_sha256' not in context:
        raise ValueError('fresh baseline requires separately bound comparison groups')
    return input_root,prepared,manifest,targets,background,context

def verify_sources():
    cfg=json.loads((ROOT/'control/config.json').read_text())
    binding=cfg.get('input_binding')
    if binding is None:return  # Existing frozen deployments retain their old protocol.
    _,_,manifest,_,_,context=resolve_sources(binding['input'],binding['prepared_dataset'])
    if (manifest['dataset_binding']!=binding['dataset_binding']
            or context['checker_context_id']!=binding['checker_context_id']):
        raise ValueError('frozen baseline dataset binding changed')

def prepare():
    global INPUT,BASE
    assert not (ROOT/'deployment.json').exists()
    INPUT,BASE,manifest,targets,background,context=resolve_sources(INPUT,BASE)
    for d in ['backend','benchmark','control','runtime','public','sandbox','results','agent-home']: (ROOT/d).mkdir(exist_ok=False)
    shutil.copytree(PROJECT/'vero-isabelle/src/vero',ROOT/'backend/vero',ignore=shutil.ignore_patterns('__pycache__'))
    sys.path.insert(0,str(ROOT/'backend'))
    from vero.isabelle.benchmark import prepare as bench_prepare,render
    payload=[dict(contract_id=t['id'],name=t['name'],exact_statement=t['statement'],
        owner_theory='Binomial',contract_owned_context=[] if t['context']=='global' else ['context '+t['context']+' begin']) for t in targets]
    cp=ROOT/'control/contracts.jsonl';cp.write_text(''.join(json.dumps(x)+'\n' for x in payload))
    groups=json.loads((BASE/'comparison-groups.json').read_text())
    bench=bench_prepare(cp,ROOT/'benchmark',base_import=context['base_import'],isabelle_api='2026',
        provenance={'dataset_binding':manifest['dataset_binding'],'checker_context_id':context['checker_context_id'],
            'input_only':True,'target_count':len(targets),'background_helpers':len(background),
            'background_facts_imported':0,'comparison_groups':groups})
    render(bench,ROOT/'sandbox');shutil.copyfile(ROOT/'benchmark/benchmark.json',ROOT/'sandbox/benchmark.json')
    shutil.copyfile(PROJECT/'intervaltree/baselines/shared-harness/tool.py',ROOT/'control/tool.py')
    for f in ['AGENTS.md','PROMPT.txt']:
        text=task_text(f,BUDGET,METHOD)
        if f=='AGENTS.md':text+=f'\nUse only /input and the legal parent facts. The {len(background)} background helper declarations are not goals and are not certified facts: do not assume them or call the model to regenerate them. No helper certificate has been imported in this fresh run. Do not access historical experiments, audit data or host paths. Native operations must use /backend/tool.py and /runtime/isabelle; do not launch a prover directly or bypass the one-slot gate.\n'
        (ROOT/'control'/f).write_text(text)
    shutil.copyfile(ROOT/'control/AGENTS.md',ROOT/'sandbox/AGENTS.md')
    cli=Path(shutil.which('codex')).resolve();shutil.copytree(cli.parent,ROOT/'runtime/official',symlinks=True)
    shutil.copyfile(BASE/'checker-assets/semantic-base/native_process.py',ROOT/'runtime/native_process.py')
    # The rebuilt definition-only parent already contains every semantic import.
    # Never inject the raw contract theory or shadow compiled parent theories.
    (ROOT/'runtime/isabelle').write_text('#!/bin/bash\nset -eu\nexec flock -x --no-fork /run/native-gate/slot.lock /workspace/verification/isabelle/bin/isabelle env ISABELLE_HEAPS=/parent-heaps python3 /runtime/native_process.py "$@"\n');(ROOT/'runtime/isabelle').chmod(0o755)
    import sqlite3
    with sqlite3.connect(BASE/'parent-premises.sqlite') as db,(ROOT/'public/premises.jsonl').open('w') as f:
        for name,statement in db.execute('SELECT canonical_name,exact_statement FROM premises ORDER BY canonical_name'): f.write(json.dumps({'canonical_name':name,'exact_statement':statement})+'\n')
    checker=dict(backend='fresh-process-v1',session=context['base_session'],session_dirs=[AFP,'/semantic-base'],isabelle='/runtime/isabelle',threads=4,timeout_seconds=300,total_timeout_seconds=300,env={'HOME':'/home/agent','USER_HOME':'/home/agent'})
    save(ROOT/'benchmark/checker.json',checker)
    cfg=dict(run_id=NAME,method=METHOD,observation_profile='official-otel-metadata-v1',budget_seconds=BUDGET,
        validation_only=VALIDATION_ONLY,base_import=context['base_import'],comparison_groups=groups,
        method_implementation='codex-cli-vero-style-prompt-v1' if METHOD=='vero' else 'official-codex-cli-v1',
        input_binding=dict(input=str(INPUT),prepared_dataset=str(BASE),dataset_binding=manifest['dataset_binding'],checker_context_id=context['checker_context_id']),
        model='gpt-6-astra',reasoning_effort='low',resources=dict(worker=1,native_slots=1,resident_limit=1,cpus=4,memory_bytes=17179869184),checker=checker,
        final_checker={**checker,'command_prefix':['runuser','-u','checker','--'],'env':{'HOME':'/home/checker','USER_HOME':'/home/checker'}},
        command=['runuser','-u','agent','--','/runtime/official/codex','exec','--json','--skip-git-repo-check','--cd','/sandbox','--dangerously-bypass-approvals-and-sandbox','--model','gpt-6-astra','-c','model_reasoning_effort="low"','-c','features.multi_agent=false','-c','features.multi_agent_v2=false','-c','web_search="disabled"','-'])
    save(ROOT/'control/config.json',cfg)
    shutil.copyfile(PROJECT/'c-algorithms/binomial/tools/official_codex_runtime.py',ROOT/'control/run.py')
    shutil.copyfile(PROJECT/'c-algorithms/binomial/tools/official_codex_observer.py',ROOT/'control/official_codex_observer.py')
    image=json.loads((INPUT/'environment.json').read_text())['container_image_id']
    cmd=['docker','run','--init','-d','--name',NAME,'--network','none' if VALIDATION_ONLY else 'host','--cpus','4','--memory','16g','--memory-swap','16g','--pids-limit','1024','--security-opt','no-new-privileges','--cap-drop','ALL']
    for cap in ['SETUID','SETGID','CHOWN','FOWNER','DAC_OVERRIDE','KILL']:cmd+=['--cap-add',cap]
    for tmp in ['/tmp:exec,size=2g','/workspace:exec','/sel4-project:exec']:cmd+=['--tmpfs',tmp]
    for env in ['PYTHONPATH=/vero-src','PYTHONDONTWRITEBYTECODE=1','PATH=/runtime:/runtime/official:/runtime/official/codex-path:/opt/conda/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin']:cmd+=['-e',env]
    for key in ['http_proxy','https_proxy','HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','no_proxy','NO_PROXY']:
        if os.environ.get(key):cmd+=['-e',key]
    mounts=[(TOOLS/'Isabelle2026','/workspace/verification/isabelle',True),(TOOLS/'afp-2026/mirror-afp-2026-fca9585c6341cbf0f91816850b375c02e2c17ba6/thys',AFP,True),(BASE/'checker-assets/semantic-base','/semantic-base',True),(BASE/'checker-assets/heaps','/parent-heaps',True),(INPUT,'/input',True),(ROOT/'backend','/vero-src',True)]
    mounts += [(ROOT/d,'/'+d,True) for d in ['runtime','control','benchmark','public']]
    mounts += [(ROOT/d,'/'+d,False) for d in ['sandbox','results']]+[(ROOT/'agent-home','/home/agent',False)]
    for host,dest,ro in mounts:
        assert host.exists(),host
        cmd+=['--mount',f'type=bind,src={host},dst={dest}'+(',readonly' if ro else '')]
    cmd+=['--entrypoint','/bin/bash',image,'-c','sleep infinity']
    identity=call(*cmd,capture_output=True,text=True).stdout.strip()
    init='''set -eu
useradd -u 1001 -M -s /bin/bash agent
useradd -u 1002 -M -s /bin/bash checker
groupadd -g 1003 nativegate
usermod -aG nativegate agent
usermod -aG nativegate checker
mkdir -p /run/native-gate /backend /home/agent/.codex /home/checker
touch /run/native-gate/slot.lock
chown root:nativegate /run/native-gate/slot.lock
chmod 660 /run/native-gate/slot.lock
ln -s /control/tool.py /backend/tool.py
for owner in /root /home/agent /home/checker; do
mkdir -p "$owner/.isabelle/Isabelle2026/etc"
cat > "$owner/.isabelle/Isabelle2026/etc/settings" <<'SETTINGS'
ML_OPTIONS="-H 256 --maxheap 8192 --stackspace 64 --gcthreads 4"
ISABELLE_TOOL_JAVA_OPTIONS="-Djava.awt.headless=true -Xms128m -Xmx2g -Xss8m"
SETTINGS
done
chown -R agent:agent /home/agent /sandbox
chown -R checker:checker /home/checker
chmod 700 /home/checker
chown root:checker /results
chmod 2770 /results
'''
    call('docker','exec','-i',NAME,'bash',input=init.encode())
    save(ROOT/'deployment.json',dict(container=NAME,container_id=identity,model_started=False,cli_version=call(cli,'--version',capture_output=True,text=True).stdout.strip(),cli_sha256=sha(cli),method=METHOD,prompt_sha256=sha(ROOT/'control/PROMPT.txt'),rules_sha256=sha(ROOT/'control/AGENTS.md'),mounts=[dict(source=str(h),destination=d,readonly=r) for h,d,r in mounts],resources=cfg['resources']))
    print('Prepared',NAME)
def start():
    if json.loads((ROOT/'control/config.json').read_text()).get('validation_only'):
        raise ValueError('validation-only deployment cannot start a model or copy credentials')
    verify_sources()
    receipt=json.loads((ROOT/'control/runtime-preflight.json').read_text())
    assert receipt['status']=='pass' and receipt['schema']=='official-codex-owned-context-v3'
    frozen=json.loads((ROOT/'control/frozen-files.json').read_text())
    mounts={'vero-src':'backend','control':'control','benchmark':'benchmark',
            'runtime':'runtime','public':'public'}
    for key,value in frozen.items():
        mount,rel=key.lstrip('/').split('/',1)
        if mount not in mounts or sha(ROOT/mounts[mount]/rel)!=value:
            raise ValueError('frozen launch source changed: '+key)
    assert not (ROOT/'started.json').exists()
    auth=Path.home()/'.codex/auth.json'
    call('docker','cp',auth,NAME+':/home/agent/.codex/auth.json',capture_output=True)
    call('docker','exec',NAME,'bash','-c','chown agent:agent /home/agent/.codex/auth.json; chmod 600 /home/agent/.codex/auth.json')
    call('docker','exec','-d',NAME,'bash','-c','umask 027; python3 -u /control/run.py > /results/launch.log 2>&1; code=$?; echo "$code" > /results/exit-code')
    save(ROOT/'started.json',dict(launch_unix=time.time(),budget_seconds=json.loads((ROOT/'control/config.json').read_text())['budget_seconds'],container=NAME))
def preflight():
    assert not (ROOT/'started.json').exists(), 'do not change a started experiment'
    verify_sources()
    result=call('docker','exec',NAME,'bash','-c',
        'umask 027; python3 /control/run.py --preflight',capture_output=True,text=True)
    receipt=json.loads(result.stdout)
    assert receipt['status']=='pass'
    save(ROOT/'control/runtime-preflight.json',receipt)
    files={}
    for folder,mount in [('backend','/vero-src'),('benchmark','/benchmark'),('control','/control'),('runtime','/runtime'),('public','/public')]:
        for p in (ROOT/folder).rglob('*'):
            if p.is_file() and p.name!='frozen-files.json':
                files[mount+'/'+str(p.relative_to(ROOT/folder))]=sha(p)
    save(ROOT/'control/frozen-files.json',files)
    save(ROOT/'preflight.json',receipt)
    print(json.dumps(receipt,indent=2))

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage',choices=['prepare','preflight','start'])
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--budget-seconds',type=float)
    parser.add_argument('--method',choices=['vero','official-codex'])
    parser.add_argument('--input',type=Path)
    parser.add_argument('--prepared-dataset',type=Path)
    parser.add_argument('--validation-only',action='store_true')
    args=parser.parse_args()
    ROOT=args.output.resolve();NAME=ROOT.name
    METHOD=args.method or 'official-codex'
    if args.stage!='prepare' and args.method is not None:
        frozen_method=json.loads((ROOT/'control/config.json').read_text()).get('method','official-codex')
        if frozen_method!=args.method:parser.error('method differs from frozen configuration')
    if args.stage=='prepare':
        from math import isfinite
        INPUT=args.input;BASE=args.prepared_dataset;VALIDATION_ONLY=args.validation_only
        BUDGET=0 if VALIDATION_ONLY else args.budget_seconds
        if VALIDATION_ONLY and args.budget_seconds is not None:
            parser.error('validation-only does not allocate generation budget')
        if not VALIDATION_ONLY and (BUDGET is None or not isfinite(BUDGET) or BUDGET<=0):
            parser.error('prepare requires explicit positive --budget-seconds')
        ROOT.mkdir(parents=True,exist_ok=False)
    globals()[args.stage]()
