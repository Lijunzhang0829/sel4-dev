"""Artifact-driven planner in a disposable filesystem-isolated Docker worker."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import socketserver
import stat
import subprocess
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone

from regen.planning.workspace_harness import WorkspaceHarness, save
from regen.planning.workspace_preflight import native_checker
from regen.planning.import_contract import prompt_v5
from regen.planning.planning_freeze import code_files
from regen.infrastructure.audited_llm import model_settings
from regen.preparation.refine_campaign import write_immutable_json


ALLOWED_EVENTS={'reasoning','agent_message','error','command_execution','file_change','todo_list'}


def instruction(payload):
    # Reuse the proposal schema/goals, but put data in read-only files instead of
    # injecting the entire contract and shared-symbol catalog on every model call.
    minimal={**payload,'contract_cards':[],'contract_relation_candidates':[]}
    template=prompt_v5(minimal,[]).split('\nPREVIOUS VALIDATION ERRORS:')[0]
    template=template.replace('Use only the supplied legal input and any explicit harness operations offered by the enclosing interaction. Do not read files, history, removed proofs, or external sources.',
        'Use only /input, your own /workspace files, and the trusted /tool.py service. Never seek removed proofs, source owners, host files, credentials, or external sources.')
    template=template.replace('Return JSON only matching this schema (all fields required):',
        'Write plan.json matching this schema (all fields required):')
    return '''You are an autonomous repository architecture planner in an isolated worker.
Read /input/catalog.json and /input/contracts.json (all exact frozen declarations and owned contexts).
Original theory attribution is deliberately absent. Do not try to reconstruct it.
Keep a parseable plan.json in /workspace; write a scaffold early and edit incrementally.
Use Python or shell scripts to assemble IDs, maintain coverage, and edit JSON; do not recopy whole plans in chat.
Keep your architectural observations and evidence references in NOTES.md. There is no proposal-correction or tool-turn cap.
Use /tool.py to query the trusted harness. For example:
  python /tool.py contracts
  python /tool.py contracts C0001 C0002
  python /tool.py definitions FUNCTION_NAME
  python /tool.py definitions FUNCTION_NAME --offset 20
  python /tool.py search 'PUBLIC_FACT_NAME or concept'
  python /tool.py inspect PREMISE_ID
  python /tool.py related C0001
  python /tool.py validate plan.json
  python /tool.py preflight plan.json
  python /tool.py submit plan.json
The service is outside your editable filesystem and records every request/result. Query results have full propositions; inspect search hits before reusing them.
Definitions returns complete verified candidate equations and next_offset for pagination. mentioned_identifiers is a lexical navigation aid, possibly including variables, not a certified call graph. Query promising identifiers yourself.
Read actual definitions when boundaries depend on operation composition. Examine potential cycles, primitive versus protocol layers, and which established results a consumer could reuse. Reference inspected definitions/facts and specific contract names in your rationales when they support a decision. Do not invent a theorem-dependency graph from lexical similarity or a function call graph.
Distinguish a restricted-case contract from a more general contract for the same operation: the restricted result may be provable first and remove an apparent circular waiting dependency. Decide this from the actual assumptions and definitions; do not assume every implementation call requires the general correspondence theorem. Do not hard-code any existing repository decomposition.
Query order and theory count are yours to choose. Do not manufacture helper intents or provider edges to fill quotas.
Theories have unique names and helper IDs matching [A-Za-z][A-Za-z0-9_]* (no hyphens).
IMPORTANT: required_imports are the planned PROVIDERS used by downstream execution to freeze imports after those theories seal. Once accepted, these are mandatory imports, not optional scheduling suggestions. All providers must seal before this theory starts, then imports are frozen. Individual fact citations are not mandatory. Do not add edges merely to prioritize work. The theories array separately breaks ties among ready theories.
Review whether every claimed cross-theory reuse is supported by a provider path and whether priority agrees with your rationale. Keep uncertain relations explicit as unresolved questions. The fields name, purpose, grouping_reason, helper_decisions and required_imports describe a handoff to File Planner, not a local proof graph. The native-checker entry name Planner_Preflight_All is reserved.
Exact new helpers and local proof DAGs belong to File Planner. This stage generates no proofs and uses no proof-state retrieval.
Validate and repair incrementally; validation returns errors without changing your file. Native preflight renders exact declarations with oops and real planned imports; it proves nothing. A successful final response alone does not submit anything: finish by calling submit on the complete plan, which also requires native preflight to pass. Only then give a brief final status.
Do not use the network, install packages, access /codex-home, change /input, or modify /tool.py. They are not task inputs.
''' + template


class RPCServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads=True


def start_service(harness, api):
    api.mkdir(parents=True,exist_ok=True); endpoint=api/'planner.sock'
    if endpoint.exists(): endpoint.unlink()
    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            line=self.rfile.readline(8_000_001)
            try:
                if len(line)>8_000_000: raise ValueError('request too large')
                result=harness.dispatch(json.loads(line))
            except Exception as e:
                result={'valid':False,'error':'Harness request failed: '+type(e).__name__}
            self.wfile.write(json.dumps(result,ensure_ascii=False).encode()+b'\n')
    server=RPCServer(str(endpoint),Handler); endpoint.chmod(0o666)
    thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
    return server,thread


def snapshot_file(path):
    """Never follow an agent-controlled symlink into the supervisor filesystem."""
    try:
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        with os.fdopen(fd,'rb') as stream:
            st=os.fstat(stream.fileno())
            if not stat.S_ISREG(st.st_mode) or st.st_size>2_000_000: return None
            return stream.read(2_000_001).decode()
    except (OSError,UnicodeError): return None


def container_command(settings, policy, public, workspace, api, runtime_home, name):
    binary=Path(settings['codex_bin']).resolve()
    return ['docker','run','--rm','-i','--name',name,'--read-only','--cap-drop=ALL',
        '--security-opt=no-new-privileges','--user',f'{os.getuid()}:{os.getgid()}',
        '--tmpfs','/tmp:rw,exec,mode=1777','--network','host',
        '--mount',f'type=bind,src={public},dst=/input,readonly',
        '--mount',f'type=bind,src={workspace},dst=/workspace',
        '--mount',f'type=bind,src={api},dst=/api,readonly',
        '--mount',f'type=bind,src={Path(__file__).with_name("workspace_tools.py").resolve()},dst=/tool.py,readonly',
        '--mount',f'type=bind,src={binary.parent},dst=/runtime,readonly',
        '--mount',f'type=bind,src={runtime_home},dst=/codex-home',
        '-e','CODEX_HOME=/codex-home','-e','HOME=/tmp','-w','/workspace',
        *[v for k in ('http_proxy','https_proxy','HTTP_PROXY','HTTPS_PROXY','NO_PROXY') if os.environ.get(k) for v in ('-e',k+'='+os.environ[k])],
        policy.get('worker_image','python:3.11-slim'),'/runtime/'+binary.name,
        '-m',policy['model']['model'],'-C','/workspace','-s','danger-full-access','-a','never',
        'exec','--skip-git-repo-check','--ignore-user-config','--ignore-rules','--ephemeral',
        '-c','web_search="disabled"',
        '-c','model_reasoning_effort="'+policy['model'].get('reasoning_effort','medium')+'"',
        '--disable','apps','--disable','plugins','--disable','remote_plugin','--disable','hooks',
        '--json','-o','/workspace/final.txt','-']


def call_worker(settings, policy, output, public, workspace, api, text, number):
    prefix=output/f'transcript/workspace-agent-{number:06d}'
    prefix.parent.mkdir(parents=True,exist_ok=True)
    def artifact(suffix): return Path(str(prefix)+suffix)
    if artifact('.meta.json').exists(): raise ValueError('Never overwrite a model call.')
    artifact('.prompt.txt').write_text(text)
    auth=Path(os.environ.get('CODEX_HOME',str(Path.home()/'.codex')))/'auth.json'
    if not auth.is_file(): raise ValueError('Codex authentication unavailable.')
    name='regen-plan-worker-'+uuid.uuid4().hex
    started=datetime.now(timezone.utc).isoformat(); t0=time.monotonic()
    meta={'state':'running','provider':'codex-workspace-audited','scope':'repository-planning-only',
          'model':policy['model']['model'],'reasoning_effort':policy['model'].get('reasoning_effort','medium'),
          'started_at':started,'prompt_sha256':hashlib.sha256(text.encode()).hexdigest(),
          'container':name,'external_isolation':'docker-nonroot-readonly-root','timed_out':False}
    save(artifact('.meta.json'),meta)
    with tempfile.TemporaryDirectory(prefix='regen-planner-auth-') as home:
        shutil.copyfile(auth,Path(home)/'auth.json'); (Path(home)/'auth.json').chmod(0o600)
        command=container_command(settings,policy,public,workspace,api,Path(home),name)
        try:
            with artifact('.ndjson').open('w') as raw, artifact('.stderr.txt').open('w') as err:
                process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=raw,stderr=err,text=True)
                try:
                    process.communicate(text,timeout=policy['model'].get('timeout_seconds',1800))
                    code=process.returncode
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait(); code=124; meta['timed_out']=True
        finally:
            subprocess.run(['docker','rm','-f',name],capture_output=True,timeout=30)
    final=snapshot_file(workspace/'final.txt') or ''
    artifact('.final.txt').write_text(final)
    events=[]; invalid_lines=0
    for line in artifact('.ndjson').read_text().splitlines():
        try: events.append(json.loads(line))
        except ValueError: invalid_lines+=1
    types=sorted({e['item']['type'] for e in events if isinstance(e.get('item'),dict) and 'type' in e['item']})
    audit={'allowed_item_types':sorted(ALLOWED_EVENTS),'observed_item_types':types,
           'forbidden_item_types':sorted(set(types)-ALLOWED_EVENTS),'invalid_json_lines':invalid_lines,
           'isolation':'No repository, original owners, proof sources, Docker socket, or checker assets mounted into worker.'}
    save(artifact('.audit.json'),audit)
    meta.update(state='complete' if code==0 else 'failed',returncode=code,wall_ms=int((time.monotonic()-t0)*1000),
                finished_at=datetime.now(timezone.utc).isoformat(),codex_sha256=settings['codex_sha256'])
    for suffix,key in [('.ndjson','raw'),('.stderr.txt','stderr'),('.final.txt','final'),('.audit.json','audit')]:
        meta[key+'_sha256']=hashlib.sha256(artifact(suffix).read_bytes()).hexdigest()
    save(artifact('.meta.json'),meta)
    if audit['forbidden_item_types'] or invalid_lines: raise ValueError('Workspace call failed tool audit.')
    return meta,prefix


def verify_worker_call(prefix, policy, codex_sha256):
    prefix=Path(prefix)
    def path(suffix): return Path(str(prefix)+suffix)
    meta=json.loads(path('.meta.json').read_text())
    expected={'state':'complete','returncode':0,'timed_out':False,
              'provider':'codex-workspace-audited','scope':'repository-planning-only',
              'model':policy['model']['model'],
              'reasoning_effort':policy['model'].get('reasoning_effort','medium'),
              'codex_sha256':codex_sha256}
    if any(meta.get(k)!=v for k,v in expected.items()):
        raise ValueError('Submission lacks a completed matching workspace model call.')
    for key,suffix in [('prompt','.prompt.txt'),('raw','.ndjson'),('final','.final.txt'),
                       ('stderr','.stderr.txt'),('audit','.audit.json')]:
        if hashlib.sha256(path(suffix).read_bytes()).hexdigest()!=meta.get(key+'_sha256'):
            raise ValueError('Workspace transcript integrity mismatch: '+key)
    audit=json.loads(path('.audit.json').read_text())
    observed=set()
    for line in path('.ndjson').read_text().splitlines():
        event=json.loads(line)
        item=event.get('item')
        if isinstance(item,dict) and 'type' in item: observed.add(item['type'])
    if (observed-ALLOWED_EVENTS or audit.get('forbidden_item_types')
            or audit.get('invalid_json_lines')!=0
            or sorted(observed)!=audit.get('observed_item_types')):
        raise ValueError('Workspace submission failed tool audit.')
    return meta


def completed_submission(harness, output, policy, settings):
    if harness.submitted is None:
        return None
    number=harness.submitted.get('call_number')
    if isinstance(number,bool) or not isinstance(number,int) or number<1:
        raise ValueError('Submission is not bound to its model call.')
    prefix=output/f'transcript/workspace-agent-{number:06d}'
    verify_worker_call(prefix,policy,settings['codex_sha256'])
    proposal,artifacts=harness.artifacts()
    return {'status':'submitted','proposal':proposal,'artifacts':artifacts,'plan_prefix':prefix}


def run_agent(payload, planning_input_path, policy, output):
    output=Path(output).resolve(); settings=model_settings()
    if hashlib.sha256(Path(settings['codex_bin']).read_bytes()).hexdigest()!=settings['codex_sha256']:
        raise ValueError('Pinned Codex executable changed.')
    sources=code_files()
    repo=Path(__file__).resolve().parents[2]
    sources.update({str(p.relative_to(repo)):p for p in (repo/'regen/execution').glob('*.py')})
    write_immutable_json(output/'workspace-binding.json',{
        'model':policy['model'],'codex_sha256':settings['codex_sha256'],
        'policy':{k:v for k,v in policy.items() if not k.startswith('_')},
        'implementation':{name:hashlib.sha256(p.read_bytes()).hexdigest() for name,p in sorted(sources.items())}})
    harness=WorkspaceHarness(planning_input_path,output,native_checker(Path(planning_input_path).parent,policy.get('preflight_seconds',900)))
    completed=completed_submission(harness,output,policy,settings)
    if completed is not None:
        return completed
    if not policy.get('_allow_external_call'):
        raise ValueError('Workspace planning requires authorized external calls.')
    public=output/'public'; public.mkdir(exist_ok=True)
    workspace=output/'workspace'; workspace.mkdir(exist_ok=True)
    save(public/'contracts.json',harness.view.payload['contracts'])
    save(public/'catalog.json',{'contracts':harness.view.payload['contract_cards'],'context_profiles':harness.view.payload['context_profiles']})
    text=instruction(harness.view.payload); harness.view.assert_no_owner_disclosure(text)
    api=Path(tempfile.mkdtemp(prefix='regen-planner-api-')); server,thread=start_service(harness,api)
    stop=threading.Event()
    def watch():
        previous=None
        while not stop.wait(2):
            content=snapshot_file(workspace/'plan.json')
            if content is not None and content!=previous:
                save(output/f'snapshots/{time.time_ns()}.json',{'content':content,'sha256':hashlib.sha256(content.encode()).hexdigest()}); previous=content
    watcher=threading.Thread(target=watch,daemon=True); watcher.start()
    number=len(list((output/'transcript').glob('workspace-agent-*.meta.json')))
    timeouts=0
    try:
        while True:
            number+=1
            checkpoint=output/f'control/checkpoint-{number:06d}'
            shutil.copytree(workspace,checkpoint,symlinks=True)
            before_state=json.loads(json.dumps(harness.state))
            harness.active_call=number
            meta,prefix=call_worker(settings,policy,output,public,workspace,api,text,number)
            if meta['timed_out']:
                # Completed query logs remain charged/auditable. The retry sees
                # the exact pre-call artifact state, never a partial response.
                with harness.lock:
                    before_state['sequence']=harness.state['sequence']
                    harness.state=before_state; harness.submitted=None
                    save(harness.state_path,harness.state)
                    submitted=output/'control/submitted.json'
                    if submitted.exists(): submitted.unlink()
                    shutil.rmtree(workspace)
                    shutil.copytree(checkpoint,workspace,symlinks=True)
                timeouts+=1
                if timeouts>=2: raise RuntimeError('Workspace planning paused after two consecutive timeouts.')
                time.sleep(10); continue
            timeouts=0
            if meta['returncode']!=0: raise RuntimeError('Workspace model call failed; inspect '+str(prefix)+'.stderr.txt')
            verify_worker_call(prefix,policy,settings['codex_sha256'])
            completed=completed_submission(harness,output,policy,settings)
            if completed is not None:
                return completed
            # Agent ended without explicit submission. Keep the same artifacts
            # and ask it to continue; no proposal retry ceiling is introduced.
            text=instruction(harness.view.payload)+'\nContinue your existing plan and notes. No successful submit was recorded; inspect, validate, repair, and explicitly submit.\n'
    finally:
        stop.set(); watcher.join(timeout=3); server.shutdown(); server.server_close(); thread.join(timeout=3)
        endpoint=api/'planner.sock'
        if endpoint.exists(): endpoint.unlink()
        shutil.rmtree(api)
