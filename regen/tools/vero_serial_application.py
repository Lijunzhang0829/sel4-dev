"""Run the original VERO runner/agent against the common host application.

This entry point is serial. Fresh runs and explicit workspace-level continuations
share one frozen experiment identity. VERO source is an explicit dependency;
its strategy, rendering, extraction and final evaluation are not reimplemented.
Only the runner's checker factory and agent transport are bound for this process.
"""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import socketserver
import stat
import subprocess
import sys
import tempfile
import threading
import time
from uuid import uuid4

from regen.execution.proof_commits import durable, digest
from regen.infrastructure.experiment_ledger import Ledger
from regen.infrastructure.experiment_activity import experiment_summary
from regen.infrastructure.effective_time_budget import CHECKPOINT_KIND
from regen.infrastructure.active_wall_budget import (
    ActiveWallBudget, POLICY as ACTIVE_WALL_BUDGET_POLICY,
    LIMIT_REASON as ACTIVE_WALL_LIMIT_REASON,
)
from regen.infrastructure.vero_experiment_observer import VeroObserver
from regen.execution.vero_checker_adapter import VeroCheckerAdapter
from regen.planning.premise_retrieval import retrieve_premises
from regen.preparation.checker_context import load_context

_BINDING_LOCK=threading.Lock()
FORMAL_MODEL='gpt-5.6-sol'
FORMAL_REASONING='medium'
FORMAL_EXPECTED_CONTRACTS=287
FORMAL_ACTIVE_WALL_SECONDS=172800
FORMAL_POLL_SECONDS=1
FORMAL_CHECKPOINT_SECONDS=30
RUNTIME_HOME_SENTINEL='<fresh-private-runtime-home>'
SEAL_MIGRATION_ID='vero-import-seal-gate-20261012-v1'
SEAL_MIGRATION_FROM='16710eb04255dff4fefb9101db208c13d4b7a0385a4360088d96e2776c4cf150'
SEAL_MIGRATION_FILES=(
    'regen/tools/vero_serial_application.py',
    'regen/execution/vero_checker_adapter.py',
    'regen/infrastructure/vero_experiment_observer.py',
    'regen/infrastructure/effective_time_budget.py',
)


class RecoverableAgentFailure(RuntimeError):
    """A provider process failed after the runner durably submitted its call."""


def _read_json(path, *, limit=32*1024*1024):
    """Read an operator-owned artifact without following a symlink."""
    path=Path(path)
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd) as stream:
        info=os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size>limit:
            raise ValueError('invalid or oversized continuation artifact: '+str(path))
        raw=stream.read(limit+1)
    if len(raw)>limit:raise ValueError('oversized continuation artifact: '+str(path))
    return json.loads(raw)


def _source_binding(source):
    return {p.name:hashlib.sha256(p.read_bytes()).hexdigest()
        for p in source.iterdir() if p.suffix in {'.py','.thy'}}


def expected_runtime_config(root, base_binding, frozen_plan):
    return {'sandbox':str((Path(root)/'workspace').resolve()),
        'total_agent_seconds':None,'max_iterations':None,
        'external_activity_supervisor':True,'completion_metric':'positive',
        'feedback':True,'resume_artifact':True,'checker':{},
        'activity_poll_seconds':FORMAL_POLL_SECONDS,
        'activity_checkpoint_seconds':FORMAL_CHECKPOINT_SECONDS,
        'exploration_policy':ACTIVE_WALL_BUDGET_POLICY,
        'exploration_limits':{'token_limit':None,
            'active_wall_seconds_limit':base_binding.get(
                'active_wall_seconds_limit',FORMAL_ACTIVE_WALL_SECONDS),
            'combination':'active-wall-only'},
        'recovery_mode':'workspace-level-new-agent-thread-v1',
        'agent':{'codex':base_binding['client_path'],'home':RUNTIME_HOME_SENTINEL,
            'model':FORMAL_MODEL,'reasoning_effort':FORMAL_REASONING,
            'timeout_seconds':None,'sandbox_mode':'workspace-write',
            'env':{'PYTHONPATH':str(Path(__file__).resolve().parents[2])}},
        'frozen_plan':frozen_plan}


def validate_resume_artifacts(root, base_binding, *, seal_migration=False):
    """Authenticate the complete frozen hand-off before mutating its ledger."""
    root=Path(root)
    required=('binding.json','accepted-plan.json','planned-benchmark.json','config.json')
    if root.is_symlink() or not root.is_dir():
        raise ValueError('resume requires an existing regular run directory')
    artifacts={name:_read_json(root/name) for name in required}
    binding=artifacts['binding.json'];plan=artifacts['accepted-plan.json']
    planned=artifacts['planned-benchmark.json'];config=artifacts['config.json']
    if (binding.get('activity_policy') != ACTIVE_WALL_BUDGET_POLICY or
            config.get('exploration_limits',{}).get('combination') !=
            'active-wall-only'):
        raise ValueError('non-active-wall VERO run cannot resume under the '
            '48-hour active-wall policy; no implicit migration or clock reset')
    frozen=frozen_plan_binding(planned,plan)
    expected={**base_binding,'frozen_plan':frozen}
    if seal_migration:
        if binding.get('application_sha256')!=SEAL_MIGRATION_FROM:
            raise ValueError('seal migration is limited to the audited original application')
        expected['application_sha256']=SEAL_MIGRATION_FROM
    if binding != expected:
        raise ValueError('continuation binding or frozen plan changed')
    if config != expected_runtime_config(root,binding,frozen):
        raise ValueError('continuation runtime configuration changed')
    for name in ('workspace','run'):
        path=root/name
        if path.is_symlink() or not path.is_dir():
            raise ValueError('continuation '+name+' is absent or unsafe')
    return plan,planned,config,frozen


def seal_migration_hashes():
    project=Path(__file__).resolve().parents[2]
    return {name:hashlib.sha256((project/name).read_bytes()).hexdigest()
        for name in SEAL_MIGRATION_FILES}


def record_seal_migration(ledger):
    """Retain the frozen binding and record exactly which trusted code changed."""
    hashes=seal_migration_hashes()
    prior=[row for row in ledger.rows if row['kind']=='implementation_migration']
    if prior:
        if len(prior)!=1 or prior[0]['data'].get('migration_id')!=SEAL_MIGRATION_ID or \
                prior[0]['data'].get('source_hashes')!=hashes:
            raise ValueError('continuation implementation differs from audited seal migration')
        return prior[0]
    return ledger.append('implementation_migration',migration_id=SEAL_MIGRATION_ID,
        original_application_sha256=SEAL_MIGRATION_FROM,source_hashes=hashes,
        purpose='dependency-gated seal, append-only stale-seal invalidation and replay',
        frozen_binding_unchanged=True,clock_and_usage_preserved=True)


def validate_resume_ledger(ledger):
    if any(r['kind']=='global_exploration_state' for r in ledger.rows):
        raise ValueError('legacy VERO run cannot resume under the active-wall '
            'policy; no implicit migration or clock reset')
    states=[r for r in ledger.rows if r['kind']==CHECKPOINT_KIND]
    if not states:raise ValueError('resume requires persisted active-wall budget state')
    state=states[-1]['data']['state']
    if state.get('policy')!=ACTIVE_WALL_BUDGET_POLICY:
        raise ValueError('continuation active-wall budget policy changed')
    if state.get('finished'):
        raise ValueError('completed VERO experiment cannot be resumed')
    if state.get('reason')==ACTIVE_WALL_LIMIT_REASON:
        raise ValueError('48-hour active-wall limit is terminal and cannot be resumed')
    return state


class ProviderRecoveryGate:
    """Exclude known runner recovery, not unobservable provider-internal time."""
    def __init__(self, stop, ledger):
        self.stop,self.ledger=stop,ledger
        self.handles={}

    def begin(self, reason):
        if reason not in self.handles:
            if reason == 'provider_recovery':
                self.stop.note_provider_failure()
            self.handles[reason]=self.stop.begin_exclusion(reason)

    def generation_started(self):
        self.stop.note_provider_started()
        if not self.handles:return
        self.close()
        self.ledger.append('provider_generation_started',boundary='before original Agent.run',
            precision='trusted application boundary; provider-internal startup remains charged',
            excluded='confirmed runner recovery, construction and rendering only')

    def close(self):
        handles=list(self.handles.values());self.handles.clear()
        for handle in handles:self.stop.end_exclusion(handle)


def run_with_agent_takeover(run_once, *, journal_path, ledger, stop, load, save,
                            resume=False, recovery_gate=None):
    """Continue only from runner-owned evaluated/recoverable checkpoints."""
    recovering=bool(resume);takeovers=0
    while True:
        stop.check()
        try:
            result=run_once(recovering)
        except RecoverableAgentFailure as error:
            if recovery_gate:recovery_gate.begin('provider_recovery')
            takeovers+=1
            ledger.append('agent_provider_takeover',takeover=takeovers,
                error_type=type(error.__cause__ or error).__name__,
                recovery='runner recover_iteration; no unknown tool call replay',
                conversation_restored=False)
            recovering=True
            continue
        if result.get('status')!='agent_error':return result
        if recovery_gate:recovery_gate.begin('provider_recovery')
        journal=load(journal_path)
        iterations=journal.get('iterations',[])
        if (not iterations or iterations[-1].get('agent',{}).get('ok') is not False
                or journal.get('status')!='agent_error'):
            raise RuntimeError('runner returned an unauthenticated agent_error checkpoint')
        # A watchdog pause may have caused the agent_error. Never reopen the
        # runner journal after the authoritative global clock has stopped.
        stop.check()
        takeovers+=1
        journal['status']='running'
        journal['formal_takeover_count']=int(journal.get('formal_takeover_count',0))+1
        save(journal_path,journal)
        ledger.append('agent_provider_takeover',takeover=takeovers,
            runner_iteration=iterations[-1].get('index'),
            recovery='evaluated runner checkpoint; new agent thread; no tool replay',
            conversation_restored=False)
        recovering=True


def create_fresh_plan(*, root, bench, config, Agent, stop, ledger, compile_plan):
    planning=root/'planning-workspace';planning.mkdir()
    durable(planning/'benchmark.json',bench)
    (planning/'INSTRUCTION.md').write_text(
        'Plan the repository before generating any proofs. Read benchmark.json for the fixed contracts. '
        'Write PLAN.json with version=1, source_benchmark_id from benchmark.json, modules and execution_order. '
        'Each module has name (Vero_ prefix, excluding Vero_All/Vero_Support), contracts (IDs), imports (other generated modules), '
        'and a nonempty rationale. Assign every contract exactly once. Imports must precede their consumers in execution_order. '
        'Choose the division and order yourself. This is the only allocation pass: after acceptance, theories, contracts and imports are immutable. Do not generate proof bodies in this planning phase. '
        'Parent search is available through python3 /backend/tool.py search QUERY. Read PLAN-FEEDBACK.json if present.')
    attempt=0
    handle=stop.begin_exclusion('planning')
    try:
        while True:
            stop.check();attempt+=1
            agent=Agent({**config['agent'],'application_workspace':str(planning)},
                root/f'planning-{attempt:04d}'/'agent')
            result=agent.run(sandbox_dir=planning,instruction_file=planning/'INSTRUCTION.md')
            durable(root/f'planning-{attempt:04d}'/'result.json',result)
            stop.check()
            if not result.get('ok'):
                raise RuntimeError('planning provider failed: '+str(result))
            try:
                plan=_read_json(planning/'PLAN.json',limit=4*1024*1024)
                planned=compile_plan(bench,plan)
            except (ValueError,OSError,KeyError,TypeError) as error:
                durable(planning/'PLAN-FEEDBACK.json',{'error':str(error)})
                ledger.append('plan_rejected',error=str(error));continue
            return plan,planned
    finally:stop.end_exclusion(handle)


def formal_configuration_binding(args):
    """Validate and persist the immutable formal-experiment controls."""
    values={'model':args.model,'reasoning_effort':args.reasoning,
        'expected_contract_count':args.expected_contracts,
        'token_limit':None,
        'active_wall_seconds_limit':args.active_wall_seconds}
    if (args.model!=FORMAL_MODEL or args.reasoning!=FORMAL_REASONING or
            args.expected_contracts!=FORMAL_EXPECTED_CONTRACTS or
            args.active_wall_seconds!=FORMAL_ACTIVE_WALL_SECONDS):
        raise ValueError('formal VERO experiment requires gpt-5.6-sol/medium, '
            'exactly 287 contracts and an immutable 172800-second active-wall '
            'limit; there is no token stopping limit')
    if getattr(args,'token_limit',None) is not None:
        raise ValueError('legacy token-limit is unsupported: VERO now uses '
            'only the active-wall time limit')
    return values


def active_wall_budget_instruction(seconds):
    """Describe the immutable cumulative online wall-clock limit."""
    return ('\nThe external experiment policy overrides fixed attempt and iteration budgets. '
        f'The experiment stops when cumulative active online wall time reaches {seconds:g} seconds. '
        'There is no token stopping limit: all observed usage remains recorded as experiment cost. '
        'The limit cannot be extended, reset by GREEN progress, or reset by retry. Repository integration '
        'success may finish earlier. Planning, initialization, preparation, model reasoning, retrieval, editing, '
        'helper work, failures, native checks, seal, integration and ordinary waits all count while the process '
        'is online. Only an explicitly recorded experiment pause, process downtime, or confirmed provider '
        'interruption/recovery interval is excluded. An abnormal pause preserves the workspace, proofs, GREEN '
        'state and cumulative clock and may be explicitly resumed without resetting the 48-hour total. '
        'Provider recovery has no attempt threshold and continues under the same cumulative clock unless an '
        'unrecoverable infrastructure invariant fails. '
        'The accepted theory allocation and imports are frozen: never split or merge theories, move '
        'contracts, change imports or replan. All theories remain one shared workspace, but unsealed '
        'sibling facts are not legal imported premises. A theory may be drafted in any order, but its '
        'contracts cannot be certified GREEN and its seal cannot be published until every frozen imported '
        'theory is sealed. When every contract in a theory is GREEN, request seal immediately; if imports '
        'are not yet sealed, the harness records a deferred seal and you must finish those imports first. '
        'If seal fails, use its diagnostics to repair proofs or helpers inside that same '
        'theory and retry automatically; do not change the frozen plan.\n')


def interrupted_result(stop, error):
    """Stop a run segment without declaring an unfinished experiment complete."""
    already_paused=stop.event.is_set()
    if not already_paused:stop.cancel('infrastructure_error')
    terminal=stop.reason==ACTIVE_WALL_LIMIT_REASON
    return {'status':'stopped' if terminal else ('paused' if already_paused else 'infrastructure_error'),
        'disposition':'stop' if terminal else 'pause','resumable':not terminal,'stop_reason':stop.reason,
        'error_type':type(error).__name__,'error':str(error)}


def _cleanup_entry_resources(service, thread, socket_path, formal_lock, *, primary_error=None):
    """Try every cleanup; cleanup failures must not hide a persistence failure."""
    failures=[]
    actions=[('service.shutdown',lambda:service.shutdown()),
        ('service.server_close',lambda:service.server_close()),
        ('thread.join',lambda:thread.join(timeout=10)),
        ('socket.unlink',lambda:socket_path.unlink(missing_ok=True)),
        ('formal_lock.close',lambda:formal_lock.close())]
    if (isinstance(socket_path,Path) and
            socket_path.parent.name.startswith('regen-vero-endpoint-')):
        actions.insert(-1,('endpoint.rmdir',lambda:socket_path.parent.rmdir()))
    for name,action in actions:
        try:action()
        except BaseException as error:failures.append((name,error))
    if failures:
        selected=primary_error if primary_error is not None else failures[0][1]
        for name,error in failures:
            selected.add_note(f'VERO cleanup failed at {name}: {type(error).__name__}: {error}')
        if primary_error is None:raise selected


def fixed_inventory_binding(bench, expected_contracts):
    count=len(bench['contracts'])
    if expected_contracts is not None and count!=expected_contracts:
        raise ValueError(f'fixed contract inventory mismatch: expected {expected_contracts}, found {count}')
    return {'fixed_contract_count':count,
        'expected_contract_count':expected_contracts,
        'fixed_contract_inventory_sha256':digest([{k:c.get(k) for k in sorted(c)}
            for c in bench['contracts']])}


def frozen_plan_binding(bench, plan):
    """Canonical identity shared by the entry, ledger and checker service."""
    allocation={m['name']:sorted(m['contracts']) for m in plan['modules']}
    imports={m['name']:sorted(m.get('imports',[])) for m in plan['modules']}
    actual={}
    for contract in bench['contracts']:
        actual.setdefault(contract['module'],[]).append(contract['id'])
    actual={k:sorted(v) for k,v in actual.items()}
    if actual != allocation:
        raise ValueError('planned benchmark differs from accepted exact-once allocation')
    return {'plan_sha256':digest(plan),'planned_benchmark_sha256':digest(bench),
        'allocation_sha256':digest(allocation),'imports_sha256':digest(imports),
        'allocation':allocation,'imports':imports}


def snapshot(workspace, destination, modules):
    """No symlink race or special-file read from a model-writable directory."""
    destination.mkdir(parents=True,exist_ok=False)
    for module in sorted(modules):
        name=module+'.thy'
        try:
            fd=os.open(Path(workspace)/name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        except FileNotFoundError:
            continue
        with os.fdopen(fd,'rb') as stream:
            info=os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size>10*1024*1024:
                raise ValueError('invalid or oversized model source')
            raw=stream.read(10*1024*1024+1)
            if len(raw)>10*1024*1024:raise ValueError('oversized model source')
        (destination/name).write_bytes(raw)


class Service(socketserver.UnixStreamServer):
    def __init__(self,path,bench,workspace,dataset,output,checker):
        from vero.isabelle.benchmark import extract
        self.bench=bench;self.workspace=workspace;self.dataset=Path(dataset)
        self.output=Path(output);self.checker=checker;self.extract=extract
        self.modules={c['module'] for c in bench['contracts']}
        self.frozen_plan=None
        super().__init__(str(path),Handler)

    def freeze_plan(self, binding):
        if self.frozen_plan is not None:
            raise ValueError('service plan is already frozen')
        if digest(self.bench) != binding['planned_benchmark_sha256']:
            raise ValueError('service planned benchmark identity mismatch')
        self.checker.set_frozen_plan(binding)
        self.frozen_plan=dict(binding)

    def verify_plan(self):
        if self.frozen_plan is None:
            raise ValueError('submit the theory plan before proof generation/checks')
        if digest(self.bench) != self.frozen_plan['planned_benchmark_sha256']:
            raise ValueError('service planned benchmark changed after freeze')
        actual={}
        for contract in self.bench['contracts']:
            actual.setdefault(contract['module'],[]).append(contract['id'])
        if digest({k:sorted(v) for k,v in actual.items()}) != self.frozen_plan['allocation_sha256']:
            raise ValueError('service allocation changed after freeze')

    def dispatch(self,request):
        if not isinstance(request,dict):raise ValueError('request must be an object')
        cmd=request.get('cmd')
        observer=getattr(self.checker,'observer',None)
        if observer:observer.stop.check()
        if cmd=='scope':
            ids=request.get('target_ids',[])
            if not isinstance(ids,list) or any(not isinstance(k,str) or not k for k in ids):raise ValueError('invalid target scope')
            if observer:
                declared=request.get('theories',request.get('theory'))
                if declared is None:theories=[]
                elif isinstance(declared,str):theories=[declared]
                elif isinstance(declared,list) and all(isinstance(t,str) and t for t in declared):
                    theories=sorted(set(declared))
                else:raise ValueError('invalid theory scope')
                if any(t not in self.modules for t in theories):
                    raise ValueError('scope identifies an unknown planned theory')
                owners={c['id']:c['module'] for c in self.bench['contracts']}
                inferred={owners[k] for k in ids if k in owners}
                if not theories:theories=sorted(inferred)
                if any(k not in owners for k in ids) and not theories:
                    raise ValueError('unowned target scope must declare its planned theory')
                if inferred and not inferred.issubset(theories):
                    raise ValueError('scope targets do not belong to declared theories')
                for theory in theories:observer.select_theory(theory)
                unique_ids=sorted(set(ids))
                allocation=('shared' if len(unique_ids)>1 or len(theories)>1 else
                    'declared-single-target' if len(unique_ids)==1 else 'unattributed')
                observer.scope={'target_ids':unique_ids,'theories':theories,
                    'theory':theories[0] if len(theories)==1 else None,
                    'phase':request.get('phase','proof'),
                    'allocation':allocation,
                    'basis':'agent-declared intended work; not causal attribution'}
                observer.ledger.append('scope',**observer.scope)
                return observer.scope
            raise ValueError('accounting observer unavailable')
        if cmd=='search':
            query=request.get('query')
            limit=request.get('limit',20)
            if not isinstance(query,str) or len(query)>4096 or type(limit)!=int or not 1<=limit<=100:
                raise ValueError('invalid search')
            return {'records':retrieve_premises(self.dataset/'parent-premises.sqlite',query,limit=limit)}
        self.verify_plan()
        if cmd not in {'check','progress'}:raise ValueError('unknown command')
        if cmd=='check' and request.get('module') not in self.modules:
            raise ValueError('unknown module')
        if cmd=='check' and observer:observer.select_theory(request['module'])
        attempt=self.output/uuid4().hex
        snapshot(self.workspace,attempt/'snapshot',self.modules)
        artifact=self.extract(self.bench,attempt/'snapshot')
        durable(attempt/'artifact.json',artifact)
        if cmd=='progress':
            from vero.isabelle.checker import filled
            return {'filled_slots':sum(s['found'] and filled(s['body']) for k,s in artifact['slots'].items()
                if k.endswith((':prove',':disprove'))),'file_errors':artifact['file_errors'],
                'authority':'filled is not proved; use check and final evaluation'}
        # Same VERO hygiene/render/audit evaluator. Scope is the requested module
        # and generated imports, not unrelated unfinished sibling proofs.
        from vero.isabelle.slots import context,schedule
        from vero.isabelle.planning import dependencies
        from vero.isabelle.checker import evaluate
        effective=context(self.bench,{k:s['body'] for k,s in artifact['slots'].items() if s['found']})
        closure=dependencies(effective,request['module'])|{request['module']}
        for key,(module,kind) in schedule(self.bench).items():
            if module not in closure:
                artifact['slots'][key]={'found':False,'body':''}
        artifact['file_errors']={m:e for m,e in artifact['file_errors'].items() if m in closure}
        result=evaluate(self.bench,artifact,attempt/'evaluation',self.checker)
        if observer:observer.evaluated(result,closure)
        # Include actual checker diagnostics rather than returning status alone.
        result['diagnostics']={m:(attempt/'evaluation'/(m+'.log')).read_text()
            for m in result['modules']}
        durable(attempt/'response.json',result)
        return result


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(30)
        try:
            raw=self.rfile.readline(65537)
            if len(raw)>65536 or not raw.endswith(b'\n'):raise ValueError('oversized request')
            result=self.server.dispatch(json.loads(raw))
        except Exception as error:
            result={'error':str(error),'type':type(error).__name__}
        try:self.wfile.write(json.dumps(result,ensure_ascii=False).encode()+b'\n')
        except (BrokenPipeError,ConnectionResetError):pass


@contextmanager
def bind_runner(runner,checker,agent_class):
    if not _BINDING_LOCK.acquire(blocking=False):raise RuntimeError('VERO entry is serial only')
    old_checker,old_agent=runner.Checker,runner.CodexAgent
    old_evaluate=getattr(runner,'evaluate',None)
    try:
        runner.Checker=lambda config:checker
        runner.CodexAgent=agent_class
        observer=getattr(checker,'observer',None)
        if observer and old_evaluate:
            def evaluate(*args,**kwargs):
                result=old_evaluate(*args,**kwargs)
                observer.evaluated(result)
                return result
            runner.evaluate=evaluate
        yield
    finally:
        runner.Checker,runner.CodexAgent=old_checker,old_agent
        if old_evaluate is not None:runner.evaluate=old_evaluate
        _BINDING_LOCK.release()


def run(args):
    formal_binding=formal_configuration_binding(args)
    resume=bool(getattr(args,'resume',False))
    sys.path.insert(0,str(Path(args.vero_source).resolve()))
    from vero.isabelle import runner
    from vero.isabelle.benchmark import read,save,validate
    bench=validate(read(args.benchmark))
    expected_contracts=getattr(args,'expected_contracts',None)
    inventory_binding=fixed_inventory_binding(bench,expected_contracts)
    if 'external_activity_supervisor' not in Path(runner.__file__).read_text():
        raise ValueError('prepare a VERO source copy with regen.tools.prepare_vero_activity first')
    root=Path(args.output).resolve()
    if resume:
        if root.is_symlink() or not root.is_dir():
            raise ValueError('--resume requires the existing output directory')
    else:root.mkdir(parents=True,exist_ok=False)
    formal_lock=(root/'.formal-wrapper.lock').open('a')
    fcntl.flock(formal_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    workspace=root/'workspace'
    client=str(Path(args.client).resolve())
    source=Path(runner.__file__).parent
    checker_context_id=load_context(Path(args.dataset))['checker_context_id']
    base_binding={'entry':'original-vero-runner-common-application-v2',
        'benchmark_id':bench['benchmark_id'],'dataset':str(Path(args.dataset).resolve()),
        'checker_context_id':checker_context_id,
        'input_benchmark_sha256':digest(bench),**formal_binding,
        'client_path':client,'client_sha256':hashlib.sha256(Path(client).read_bytes()).hexdigest(),
        'vero_sources':_source_binding(source),
        'application':str(Path(__file__).resolve()),
        'application_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'image':args.image,'proxy':args.proxy,'check_timeout':args.check_timeout,
        'continuation_mode':'workspace-level-explicit-new-agent-thread-v1',
        'conversation_resume':False,
        'activity_poll_seconds':FORMAL_POLL_SECONDS,
        'activity_checkpoint_seconds':FORMAL_CHECKPOINT_SECONDS,
        'shutdown_tail_policy':'unknown crash tail; checkpoint cadence is not a guaranteed bound',
        'activity_policy':ACTIVE_WALL_BUDGET_POLICY,
        **inventory_binding}
    if resume:
        try:plan,planned,config,plan_binding=validate_resume_artifacts(root,base_binding,
            seal_migration=bool(getattr(args,'seal_dependency_migration',False)))
        except Exception:
            formal_lock.close()
            raise
    else:durable(root/'binding.json',base_binding)
    ledger=Ledger(root,root.name)
    if resume:
        try:validate_resume_ledger(ledger)
        except Exception:
            formal_lock.close()
            raise
        if getattr(args,'seal_dependency_migration',False):
            try:record_seal_migration(ledger)
            except Exception:
                formal_lock.close()
                raise
        authorization=ledger.append('operator_resume_authorized',authorization='operator --resume',
            recovery='workspace-level; new private HOME and new model thread',
            conversation_restored=False)
    else:
        authorization=None
        ledger.append('experiment_started',continuation_mode=base_binding['continuation_mode'])
    try:
        stop=ActiveWallBudget(ledger,seconds=args.active_wall_seconds,
            poll_seconds=FORMAL_POLL_SECONDS,checkpoint_seconds=FORMAL_CHECKPOINT_SECONDS,
            resume_authorization=authorization['event_id'] if authorization else None)
    except Exception as error:
        if resume:ledger.append('resume_rejected',authorization_event_id=authorization['event_id'],
            error_type=type(error).__name__)
        formal_lock.close()
        raise
    if resume:
        ledger.append('experiment_resumed',authorization_event_id=authorization['event_id'],
            recovery='workspace-level; new private HOME and new model thread',
            conversation_restored=False)
        bench=planned
    observer=VeroObserver(ledger,stop,workspace,bench)
    with tempfile.TemporaryDirectory(prefix='regen-vero-home-') as home:
        auth_home=Path(home)/'.codex';auth_home.mkdir(mode=0o700)
        shutil.copyfile(args.auth,auth_home/'auth.json');(auth_home/'auth.json').chmod(0o600)
        (Path(home)/'.isabelle').mkdir()
        observer.home=auth_home
        # Linux sockaddr_un.sun_path is normally limited to 108 bytes. Bind an
        # authenticated per-output endpoint under /tmp instead of making the
        # frozen output name part of the socket path.
        endpoint_id=hashlib.sha256(str(root).encode()).hexdigest()[:20]
        socket_dir=Path('/tmp')/('regen-vero-endpoint-'+endpoint_id)
        socket_dir.mkdir(mode=0o700,exist_ok=resume)
        endpoint_stat=socket_dir.lstat()
        if (socket_dir.is_symlink() or not stat.S_ISDIR(endpoint_stat.st_mode)
                or endpoint_stat.st_uid!=os.getuid()):
            raise ValueError('unsafe VERO endpoint directory')
        socket_dir.chmod(0o700)
        socket_path=socket_dir/'checker.sock'
        if resume:socket_path.unlink(missing_ok=True)
        native_segment=root/'native-segments'/('segment-'+uuid4().hex)
        with VeroCheckerAdapter(args.dataset,native_segment,timeout=args.check_timeout,observer=observer) as checker:
            service=Service(socket_path,bench,workspace,args.dataset,root/'requests',checker)
            service.plan_ready=False
            thread=threading.Thread(target=service.serve_forever,daemon=True);thread.start()
            stop.start(sample=observer.drafts)
            original_agent=runner.CodexAgent
            recovery_gate=ProviderRecoveryGate(stop,ledger)
            class Agent(original_agent):
                def __init__(self,config,log_dir):
                    identity=uuid4().hex[:8]
                    transport=Path(home)/('transport-'+identity);transport.mkdir()
                    transport_config=root/('transport-'+identity+'.json')
                    durable(transport_config,{'transport_base':str(transport),'workspace':str(config.get('application_workspace',workspace)),
                        'auth_home':str(auth_home),'client':client,'image':args.image,'proxy':args.proxy,
                        'socket_dir':str(socket_dir)})
                    config={**config,'home':home,'persistent_session':True,'resume_session':False,'command_prefix':[sys.executable,'-m','regen.tools.vero_isolated_exec',
                        '--config',str(transport_config),'--']}
                    ledger.append('agent_invocation_bound',invocation=str(log_dir),
                        conversation_restored=False,
                        recovery='fresh thread over durable shared workspace')
                    super().__init__(config,log_dir)
                def run(self,**kwargs):
                    if service.frozen_plan is not None and recovery_gate.handles:
                        # The runner has rendered the pristine planned workspace,
                        # but the model has not started. Establish the trusted
                        # fingerprint synchronously so its first edit cannot be
                        # mistaken for initialization by the async watchdog.
                        observer.drafts()
                    instruction=Path(kwargs['instruction_file'])
                    with instruction.open('a') as stream:
                        stream.write('\n# Common application transport\nThe check/search/progress commands above use the host-owned common Isabelle backend. Legal parent facts are available via tool.py search; host source directories and heaps are intentionally not mounted. Checks return diagnostics. Before editing or reasoning, call tool.py scope TARGET_ID ... --theory THEORY [--theory THEORY ...] --phase proof (or helper/retrieval/seal) for cost attribution. Include every intended target and theory; use an empty scope when work is genuinely unattributed. Multi-target or multi-theory cost remains shared and is never duplicated as precise single-target cost. Scope labels do not control the shared global exploration limits and do not grant proof authority. Do not attempt to install Isabelle or replace the checker.\n')
                    with instruction.open('a') as stream:
                        stream.write(active_wall_budget_instruction(stop.snapshot()['seconds']))
                    previous=kwargs.pop('monitor',None)
                    def monitor():
                        observer.usage_events(self.log_dir/'agent_events.jsonl',self.log_dir.parent.name)
                        stop.check()
                        return previous() if previous else None
                    try:
                        if service.frozen_plan is not None:recovery_gate.generation_started()
                        result=super().run(**kwargs,monitor=monitor)
                        if service.frozen_plan is not None and not result.get('ok'):
                            recovery_gate.begin('provider_recovery')
                        return result
                    except (OSError,subprocess.SubprocessError) as error:
                        if service.frozen_plan is not None:recovery_gate.begin('provider_recovery')
                        raise RecoverableAgentFailure('provider/agent process failed') from error
                    finally:
                        observer.usage_events(self.log_dir/'agent_events.jsonl',self.log_dir.parent.name,final=True)
                        stop.check()
            if not resume:config=expected_runtime_config(root,base_binding,{})
            try:
                if not resume:
                    from vero.isabelle.planning import compile_plan
                    plan,planned=create_fresh_plan(root=root,bench=bench,config=config,
                        Agent=Agent,stop=stop,ledger=ledger,compile_plan=compile_plan)
                    plan_binding=frozen_plan_binding(planned,plan)
                    config=expected_runtime_config(root,base_binding,plan_binding)
                    durable(root/'accepted-plan.json',plan)
                    durable(root/'planned-benchmark.json',planned)
                    durable(root/'binding.json',{**base_binding,'frozen_plan':plan_binding})
                    durable(root/'config.json',config)
                    ledger.append('plan_accepted',benchmark_id=planned['benchmark_id'],**plan_binding)
                bench=planned;observer.bench=bench;service.bench=bench
                service.modules={c['module'] for c in bench['contracts']};service.plan_ready=True
                service.freeze_plan(plan_binding)
                stop.register(plan_binding['allocation'])
                first=next((t for t in plan['execution_order'] if plan_binding['allocation'].get(t)),None)
                if first is None:raise ValueError('accepted plan has no generating theory')
                stop.select(first)
                recovery_gate.begin('runtime_setup')
                original_check=checker.check
                def governed_check(directory,theory,timeout=None):
                    if theory=='Vero_All':
                        handle=stop.begin_exclusion('integration')
                        try:return original_check(directory,theory,timeout)
                        finally:stop.end_exclusion(handle)
                    observer.select_theory(theory)
                    return original_check(directory,theory,timeout)
                checker.check=governed_check
                try:
                    with bind_runner(runner,checker,Agent):
                        result=run_with_agent_takeover(
                            lambda recovering:runner.run(root/'planned-benchmark.json',
                                root/'config.json',root/'run',resume=recovering),
                            journal_path=root/'run'/'iterations.json',ledger=ledger,
                            stop=stop,load=read,save=save,resume=resume,
                            recovery_gate=recovery_gate)
                finally:
                    checker.check=original_check
                    recovery_gate.close()
                if result.get('status')=='complete':stop.finish()
                else:
                    stop.cancel('runner_returned_without_completion')
                    terminal=stop.reason==ACTIVE_WALL_LIMIT_REASON
                    result={**result,'runner_status':result.get('status'),
                        'status':'stopped' if terminal else 'paused',
                        'disposition':'stop' if terminal else 'pause',
                        'resumable':not terminal,
                        'stop_reason':stop.reason}
                durable(root/'result.json',result)
                return result
            except Exception as error:
                result=interrupted_result(stop,error)
                durable(root/'result.json',result)
                return result
            finally:
                try:
                    stop.close()
                    clock=stop.snapshot()
                    ledger.append('experiment_finished' if clock['finished'] else 'experiment_paused',
                        reason=stop.reason)
                    summary=experiment_summary(ledger)
                    summary['generation_clock']=clock
                    summary['time_dimensions']={
                        'operation_time':'elapsed_seconds excludes recorded experiment pause/downtime only',
                        'active_wall':'one shared-workspace cumulative online wall clock; all normal phases count',
                        'excluded_time':'explicit pause, offline process time, or confirmed provider interruption/recovery only',
                        'budget_stop':'terminal stop at 172800 cumulative active-wall seconds; no token, retry, iteration, or no-progress limit',
                        'poll_seconds':FORMAL_POLL_SECONDS,
                        'checkpoint_seconds':FORMAL_CHECKPOINT_SECONDS,
                        'shutdown_tail_policy':'unknown crash tail; checkpoint cadence is not a guaranteed bound',
                        'continuation':'workspace and experiment state resume; model conversation does not'}
                    durable(root/'experiment-summary.json',summary)
                finally:
                    _cleanup_entry_resources(service,thread,socket_path,formal_lock,
                        primary_error=sys.exc_info()[1])


def main():
    parser=argparse.ArgumentParser()
    for name in ('vero-source','benchmark','dataset','output','client','auth','image'):
        parser.add_argument('--'+name,required=True)
    parser.add_argument('--proxy')
    parser.add_argument('--model',default=FORMAL_MODEL,choices=[FORMAL_MODEL])
    parser.add_argument('--reasoning',default=FORMAL_REASONING,choices=[FORMAL_REASONING])
    parser.add_argument('--expected-contracts',type=int,required=True,
        choices=[FORMAL_EXPECTED_CONTRACTS])
    parser.add_argument('--active-wall-seconds',type=int,
        default=FORMAL_ACTIVE_WALL_SECONDS,choices=[FORMAL_ACTIVE_WALL_SECONDS],
        help='immutable cumulative online wall-clock limit (fixed at 48 hours)')
    parser.add_argument('--check-timeout',type=int,default=900)
    parser.add_argument('--resume',action='store_true',
        help='explicitly continue the same frozen output with a new model thread')
    parser.add_argument('--seal-dependency-migration',action='store_true',
        help='audit and apply the bounded import-seal correction to the original paused run')
    args=parser.parse_args()
    if args.seal_dependency_migration and not args.resume:
        parser.error('--seal-dependency-migration requires --resume')
    if min(args.expected_contracts,args.active_wall_seconds,args.check_timeout)<=0:
        parser.error('budgets and expected contract count must be positive')
    result=run(args)
    print(json.dumps({'status':result['status'],'iterations':len(result.get('iterations',[]))}))
    sys.exit(result['status'] not in {'complete','stopped'})


if __name__=='__main__':main()
