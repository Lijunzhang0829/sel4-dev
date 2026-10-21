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
import socketserver
import stat
import subprocess
import sys
import tempfile
import threading
import time
from uuid import uuid4

from regen.execution.proof_commits import durable, digest
from regen.execution.check_executor import shared_host_binding
from regen.infrastructure.experiment_ledger import Ledger
from regen.infrastructure.experiment_activity import experiment_summary
from regen.infrastructure.effective_time_budget import CHECKPOINT_KIND
from regen.infrastructure.active_wall_budget import (
    ActiveWallBudget, POLICY as ACTIVE_WALL_BUDGET_POLICY,
    LIMIT_REASON as ACTIVE_WALL_LIMIT_REASON,
)
from regen.infrastructure.effective_time_budget import (
    EffectiveTimeBudget, POLICY as EFFECTIVE_BUDGET_POLICY,
    LIMIT_REASON as EFFECTIVE_LIMIT_REASON,
)
from regen.infrastructure.vero_experiment_observer import VeroObserver
from regen.infrastructure.vero_auth_state import (
    existing_vero_auth_source,prepare_vero_auth_state,rotate_vero_auth_state,
)
from regen.infrastructure.vero_cli_provider_failure import (
    RECOVERY_WINDOW_SECONDS, classify_vero_cli_failure,
    vero_cli_recovery_decision,
)
from regen.tools.generic_serial_application import (
    DEFAULT_AUTH_LEASE_DIR,provider_auth_lease,
)
from regen.execution.vero_checker_adapter import VeroCheckerAdapter
from regen.execution.vero_import_graph import (
    attributable_import_failure, conservative_missing_imports,
    effective_generated_imports,
)
from regen.planning.premise_retrieval import retrieve_premises
from regen.preparation.checker_context import load_context

_BINDING_LOCK=threading.Lock()
FORMAL_MODEL='gpt-5.6-sol'
FORMAL_REASONING='medium'
FORMAL_EXPECTED_CONTRACTS=287
FORMAL_ACTIVE_WALL_SECONDS=172800
FORMAL_CREFINE_CONTRACTS=309
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
CHECKER_LIFECYCLE_MIGRATION_ID='vero-checker-takeover-lifecycle-20261018-v1'
CHECKER_LIFECYCLE_MIGRATION_FROM='380ede9ef4d8e903da975af3e276e9ffb1f065061a89e9b7cc7a04033bb4d9e9'
RESOURCE_POOL_MIGRATION_ID='vero-crefine-one-of-two-native-slots-20261020-v1'
RESOURCE_POOL_MIGRATION_FROM='7b777dd629e71745fdc019b415f5250b4ab56393f1f69c0b50e1f860b61982e7'
RESOURCE_POOL_MIGRATION_APPLICATION='74ad48757933f27fb94575087d9a78d97a266980376f513b2d583926d68cc910'
PROVIDER_AUTH_MIGRATION_ID='vero-crefine-independent-auth-chain-20261021-v1'
PROVIDER_AUTH_MIGRATION_SEED='/data/zljj/vero-private/vero-crefine-login-20261017-001/auth.json'
PROVIDER_AUTH_MIGRATION_BASE=('/data/zljj/vero-private/vero-crefine-login-20261017-001/'
    '.regen-vero-auth-state-pool2-20261021')
PROVIDER_AUTH_MIGRATION_APPLICATION='a74f8534b90d0ebe426380bd01ed61469f366ab9e15b42132674179b93eaa9c3'
RUNNER_REOPEN_MIGRATION_ID='vero-crefine-paused-agent-error-reopen-20261021-v1'


class TerminalEffectiveTimeBudget(EffectiveTimeBudget):
    """Treat the frozen CRefine 48-hour effective allowance as a final stop."""

    def _cancel_locked(self, reason):
        if reason != self.limit_reason:
            return super()._cancel_locked(reason)
        if self.event.is_set():return
        self.reason=reason;self.event.set()
        self._append('termination',reason=reason,disposition='stop',
            activity_policy=self.policy,effective_seconds=self.effective_seconds,
            worker_seconds=self.worker_seconds,theory_seconds=dict(self._theory_seconds),
            seconds=self.seconds,token_limit=None)
        self._append('time_budget_finished',reason=reason,
            effective_seconds=self.effective_seconds)
        self.finished=True;self.finish_reason=reason
        self._write_state('finished')


class RecoverableAgentFailure(RuntimeError):
    """A provider process failed after the runner durably submitted its call."""

    def __init__(self, message, *, classification=None, checkpoint_verified=False):
        super().__init__(message)
        self.classification=classification or {'category':'unknown','failure':None,
            'evidence':'none'}
        self.checkpoint_verified=checkpoint_verified


class ProviderRecoveryPause(RuntimeError):
    """A classified failure cannot safely authorize a fresh model invocation."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason=reason


def classify_agent_invocation(log_dir):
    """Read only terminal CLI envelopes and bounded stderr, never model prose."""
    log_dir=Path(log_dir)
    events=[]
    events_file=log_dir/'agent_events.jsonl'
    try:
        fd=os.open(events_file,os.O_RDONLY|os.O_NOFOLLOW)
        with os.fdopen(fd,'rb') as stream:
            info=os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):raise ValueError('unsafe agent event log')
            offset=max(0,info.st_size-1024*1024)
            stream.seek(offset)
            raw=stream.read(1024*1024)
        lines=raw.splitlines()[1:] if offset else raw.splitlines()
        for line in lines:
            try:event=json.loads(line)
            except (ValueError,UnicodeError):continue
            if isinstance(event,dict) and event.get('type') in {'error','turn.failed'}:
                events.append(event)
    except FileNotFoundError:pass
    stderr=''
    try:
        fd=os.open(log_dir/'agent.stderr',os.O_RDONLY|os.O_NOFOLLOW)
        with os.fdopen(fd,'rb') as stream:
            info=os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):raise ValueError('unsafe agent stderr log')
            stream.seek(max(0,info.st_size-4096))
            stderr=stream.read(4096).decode('utf-8',errors='replace')
    except FileNotFoundError:pass
    return classify_vero_cli_failure(events,stderr)


def _agent_checkpoint_verified(log_dir):
    """Only the original runner's submitted, terminated call can be recovered."""
    log_dir=Path(log_dir)
    try:
        submitted=_read_json(log_dir.parent/'submitted.json',limit=65536)
        state=_read_json(log_dir/'state.json',limit=65536)
    except (FileNotFoundError,ValueError,OSError):
        return False
    return (isinstance(submitted,dict) and
        state.get('status') in {'interrupted','finished'})


def _planning_checkpoint_verified(log_dir):
    """A closed CLI process has no outstanding call to replay during planning."""
    try:state=_read_json(Path(log_dir)/'state.json',limit=65536)
    except (FileNotFoundError,ValueError,OSError):return False
    return isinstance(state,dict) and state.get('status') in {'interrupted','finished'}


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
    host_keys=('native_slots','shared_host_lock_dir','shared_host_slots',
        'shared_host_cleanup_timeout')
    effective=base_binding.get('budget_mode')=='effective-time'
    return {'sandbox':str((Path(root)/'workspace').resolve()),
        'total_agent_seconds':None,'max_iterations':None,
        'external_activity_supervisor':True,'completion_metric':'positive',
        'feedback':True,'resume_artifact':True,
        'checker':{key:base_binding[key] for key in host_keys if key in base_binding},
        'activity_poll_seconds':FORMAL_POLL_SECONDS,
        'activity_checkpoint_seconds':FORMAL_CHECKPOINT_SECONDS,
        'exploration_policy':EFFECTIVE_BUDGET_POLICY if effective else ACTIVE_WALL_BUDGET_POLICY,
        'exploration_limits':{'token_limit':None,
            **({'effective_seconds_limit':base_binding['effective_seconds_limit'],
                'combination':'effective-time-only'} if effective else {
                'active_wall_seconds_limit':base_binding.get(
                    'active_wall_seconds_limit',FORMAL_ACTIVE_WALL_SECONDS),
                'combination':'active-wall-only'})},
        'recovery_mode':'workspace-level-new-agent-thread-v1',
        'provider':{'recovery_policy':'provider-recovery-v2',
            'recovery_window_seconds':RECOVERY_WINDOW_SECONDS,
            'auth_policy':'durable-private-home-v1',
            'auth_state_base':base_binding.get('auth_state_base'),
            'auth_lease_dir':base_binding.get('auth_lease_dir')},
        'agent':{'codex':base_binding['client_path'],'home':RUNTIME_HOME_SENTINEL,
            'model':FORMAL_MODEL,'reasoning_effort':FORMAL_REASONING,
            'timeout_seconds':None,'sandbox_mode':'workspace-write',
            'env':{'PYTHONPATH':str(Path(__file__).resolve().parents[2])}},
        'frozen_plan':frozen_plan}


def validate_resume_artifacts(root, base_binding, *, seal_migration=False,
                              checker_lifecycle_migration=False,
                              resource_pool_migration=False,
                              provider_auth_migration=False):
    """Authenticate the complete frozen hand-off before mutating its ledger."""
    root=Path(root)
    required=('binding.json','accepted-plan.json','planned-benchmark.json','config.json')
    if root.is_symlink() or not root.is_dir():
        raise ValueError('resume requires an existing regular run directory')
    artifacts={name:_read_json(root/name) for name in required}
    binding=artifacts['binding.json'];plan=artifacts['accepted-plan.json']
    planned=artifacts['planned-benchmark.json'];config=artifacts['config.json']
    effective=base_binding.get('budget_mode')=='effective-time'
    expected_policy=EFFECTIVE_BUDGET_POLICY if effective else ACTIVE_WALL_BUDGET_POLICY
    expected_combination='effective-time-only' if effective else 'active-wall-only'
    if (binding.get('activity_policy') != expected_policy or
            config.get('exploration_limits',{}).get('combination') != expected_combination):
        raise ValueError('VERO continuation budget policy changed; no implicit migration or clock reset')
    frozen=frozen_plan_binding(planned,plan)
    expected={**base_binding,'frozen_plan':frozen}
    if resource_pool_migration:
        if (binding.get('application_sha256')!=RESOURCE_POOL_MIGRATION_FROM
                or binding.get('native_slots')!=2
                or binding.get('shared_host_slots')!=2
                or base_binding.get('native_slots',1)!=1
                or base_binding.get('shared_host_slots')!=2
                or base_binding.get('shared_host_lock_dir')!=binding.get('shared_host_lock_dir')):
            raise ValueError('resource pool migration requires the audited VERO two-to-one slot binding')
        expected['application_sha256']=RESOURCE_POOL_MIGRATION_FROM
        expected['native_slots']=2
    if provider_auth_migration:
        seed_hash=hashlib.sha256(str(Path(PROVIDER_AUTH_MIGRATION_SEED).resolve()).encode()).hexdigest()
        if (not resource_pool_migration or base_binding.get('auth_seed_path_sha256')!=seed_hash
                or base_binding.get('auth_state_base')!=PROVIDER_AUTH_MIGRATION_BASE
                or base_binding.get('auth_lease_dir')!=binding.get('auth_lease_dir')):
            raise ValueError('provider auth migration requires the audited independent VERO login')
        expected['auth_seed_path_sha256']=binding['auth_seed_path_sha256']
        expected['auth_state_base']=binding['auth_state_base']
    if checker_lifecycle_migration:
        if binding.get('application_sha256')!=CHECKER_LIFECYCLE_MIGRATION_FROM:
            raise ValueError('checker lifecycle migration is limited to the audited original application')
        expected['application_sha256']=CHECKER_LIFECYCLE_MIGRATION_FROM
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


def record_resource_pool_migration(ledger, old_binding, new_binding):
    """Append a one-time admission-only change; never rewrite frozen inputs."""
    old={key:old_binding.get(key,1 if key=='native_slots' else None)
        for key in ('native_slots','shared_host_lock_dir','shared_host_slots')}
    new={key:new_binding.get(key,1 if key=='native_slots' else None)
        for key in ('native_slots','shared_host_lock_dir','shared_host_slots')}
    if (old['native_slots']!=2 or new['native_slots']!=1
            or old['shared_host_slots']!=2 or new['shared_host_slots']!=2
            or old['shared_host_lock_dir']!=new['shared_host_lock_dir']):
        raise ValueError('resource pool migration must only reduce VERO local capacity')
    event=dict(migration_id=RESOURCE_POOL_MIGRATION_ID,
        frozen_binding_sha256=digest(old_binding),old_resources=old,new_resources=new,
        original_application_sha256=RESOURCE_POOL_MIGRATION_FROM,
        new_application_sha256=RESOURCE_POOL_MIGRATION_APPLICATION,
        frozen_binding_unchanged=True,clock_and_usage_preserved=True)
    previous=[row for row in ledger.rows if row['kind']=='resource_policy_migration']
    if previous:
        if len(previous)!=1 or previous[0]['data']!=event:
            raise ValueError('resource pool migration differs from audited event')
        return previous[0]
    return ledger.append('resource_policy_migration',**event)


def record_provider_auth_migration(ledger, old_binding, new_binding,
                                   resource_event):
    """Bind a separate login chain without changing proof, clock or old auth state."""
    if (resource_event['data'].get('migration_id')!=RESOURCE_POOL_MIGRATION_ID
            or old_binding.get('auth_seed_path_sha256')==new_binding.get('auth_seed_path_sha256')
            or new_binding.get('auth_state_base')!=PROVIDER_AUTH_MIGRATION_BASE):
        raise ValueError('provider auth migration requires the prior resource migration')
    event=dict(migration_id=PROVIDER_AUTH_MIGRATION_ID,
        frozen_binding_sha256=digest(old_binding),
        resource_migration_event_id=resource_event['event_id'],
        old_auth_seed_path_sha256=old_binding['auth_seed_path_sha256'],
        new_auth_seed_path_sha256=new_binding['auth_seed_path_sha256'],
        old_auth_state_base=old_binding['auth_state_base'],
        new_auth_state_base=new_binding['auth_state_base'],
        prior_application_sha256=RESOURCE_POOL_MIGRATION_APPLICATION,
        new_application_sha256=PROVIDER_AUTH_MIGRATION_APPLICATION,
        frozen_binding_unchanged=True,old_auth_state_preserved=True,
        clock_and_usage_preserved=True)
    previous=[row for row in ledger.rows if row['kind']=='provider_auth_migration'
        and row['data'].get('migration_id')==PROVIDER_AUTH_MIGRATION_ID]
    if previous:
        if len(previous)!=1 or previous[0]['data']!=event:
            raise ValueError('provider auth migration differs from audited event')
        return previous[0]
    return ledger.append('provider_auth_migration',**event)


def record_runner_reopen_migration(ledger, old_binding, new_binding, auth_event):
    """Audit the code change that permits a new turn after a closed agent error."""
    if auth_event['data'].get('migration_id')!=PROVIDER_AUTH_MIGRATION_ID:
        raise ValueError('runner reopen requires the audited independent auth migration')
    event=dict(migration_id=RUNNER_REOPEN_MIGRATION_ID,
        frozen_binding_sha256=digest(old_binding),
        provider_auth_migration_event_id=auth_event['event_id'],
        prior_application_sha256=PROVIDER_AUTH_MIGRATION_APPLICATION,
        new_application_sha256=new_binding['application_sha256'],
        purpose='explicitly reopen a closed agent_error checkpoint without replaying its call',
        frozen_binding_unchanged=True,clock_and_usage_preserved=True)
    previous=[row for row in ledger.rows if row['kind']=='implementation_migration'
        and row['data'].get('migration_id')==RUNNER_REOPEN_MIGRATION_ID]
    if previous:
        if len(previous)!=1 or previous[0]['data']!=event:
            raise ValueError('runner reopen implementation differs from audited event')
        return previous[0]
    return ledger.append('implementation_migration',**event)


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


def record_checker_lifecycle_migration(ledger):
    """Authorize a borrowed runner checker without changing frozen task inputs."""
    application_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    prior=[row for row in ledger.rows if row['kind']=='implementation_migration'
        and row['data'].get('migration_id')==CHECKER_LIFECYCLE_MIGRATION_ID]
    if prior:
        if len(prior)!=1 or prior[0]['data'].get('new_application_sha256')!=application_sha256:
            raise ValueError('checker lifecycle migration implementation changed')
        return prior[0]
    return ledger.append('implementation_migration',
        migration_id=CHECKER_LIFECYCLE_MIGRATION_ID,
        original_application_sha256=CHECKER_LIFECYCLE_MIGRATION_FROM,
        new_application_sha256=application_sha256,
        purpose='keep host-owned checker open across runner provider takeovers',
        frozen_binding_unchanged=True,clock_and_usage_preserved=True)


def validate_resume_ledger(ledger, *, effective=False):
    if any(r['kind']=='global_exploration_state' for r in ledger.rows):
        raise ValueError('legacy VERO run cannot resume under the active-wall '
            'policy; no implicit migration or clock reset')
    states=[r for r in ledger.rows if r['kind']==CHECKPOINT_KIND]
    if not states:raise ValueError('resume requires persisted active-wall budget state')
    state=states[-1]['data']['state']
    if state.get('policy')!=(EFFECTIVE_BUDGET_POLICY if effective else ACTIVE_WALL_BUDGET_POLICY):
        raise ValueError('continuation active-wall budget policy changed')
    if state.get('finished'):
        raise ValueError('completed VERO experiment cannot be resumed')
    if state.get('reason')==(EFFECTIVE_LIMIT_REASON if effective else ACTIVE_WALL_LIMIT_REASON):
        raise ValueError('48-hour time limit is terminal and cannot be resumed')
    return state


class ProviderRecoveryGate:
    """Exclude known runner recovery, not unobservable provider-internal time."""
    def __init__(self, stop, ledger):
        self.stop,self.ledger=stop,ledger
        self.handles={}

    def begin(self, reason):
        if reason not in self.handles:
            if reason == 'provider_recovery' and hasattr(self.stop,'note_provider_failure'):
                self.stop.note_provider_failure()
            self.handles[reason]=self.stop.begin_exclusion(reason)

    def generation_started(self):
        if hasattr(self.stop,'note_provider_started'):
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
                            resume=False, recovery_gate=None,
                            reopen_agent_error=False,
                            clock=time.monotonic, sleep=time.sleep):
    """Continue a trusted checkpoint only for a bounded transient CLI failure."""
    recovering=bool(resume);takeovers=0
    first_failure_at=None;prior_failures=0

    if reopen_agent_error:
        if not resume:
            raise ValueError('agent error reopen requires an explicit resume')
        journal=load(journal_path)
        if journal.get('status')!='agent_error' or not journal.get('iterations'):
            raise ValueError('agent error reopen requires a closed failed runner checkpoint')
        previous=journal['iterations'][-1]
        if (previous.get('agent',{}).get('ok') is not False
                or not _agent_checkpoint_verified(Path(journal_path).parent/
                    f"iter-{previous['index']:03d}"/'agent')):
            raise ValueError('agent error reopen lacks a verified terminated call')
        stop.check()
        event=ledger.append('operator_agent_error_reopened',
            previous_iteration=previous['index'],
            previous_agent_error=previous['agent'].get('provider_failure'),
            prior_journal_sha256=digest(journal),
            recovery='new model thread; retain prior iteration and cost; no pending call replay')
        journal['status']='running'
        journal['formal_takeover_count']=int(journal.get('formal_takeover_count',0))+1
        journal['operator_reopen_event_id']=event['event_id']
        save(journal_path,journal)

    def schedule(classification, *, checkpoint_verified):
        nonlocal first_failure_at,prior_failures
        now=clock()
        if first_failure_at is None:first_failure_at=now
        decision=vero_cli_recovery_decision(classification,prior_failures,
            now-first_failure_at,checkpoint_verified=checkpoint_verified)
        if not decision['retry']:
            ledger.append('provider_recovery_paused',category=classification['category'],
                evidence=classification['evidence'],reason=decision['reason'],
                recovery_window_seconds=RECOVERY_WINDOW_SECONDS,
                consecutive_failures=prior_failures)
            raise ProviderRecoveryPause(decision['reason'])
        prior_failures=decision['consecutive_failures']
        ledger.append('provider_recovery_scheduled',category=classification['category'],
            evidence=classification['evidence'],
            http_status=classification['failure']['http_status'],
            delay_seconds=decision['delay_seconds'],
            consecutive_failures=prior_failures,
            recovery_window_seconds=RECOVERY_WINDOW_SECONDS,
            incident_elapsed_seconds=now-first_failure_at,
            retry_policy=decision['retry_policy'],
            recovery='new CLI invocation from runner checkpoint; no pending tool replay')
        if recovery_gate:recovery_gate.begin('provider_recovery')
        try:
            deadline=clock()+decision['delay_seconds']
            while (remaining:=deadline-clock())>0:
                stop.check()
                sleep(min(1,remaining))
        except BaseException:
            if recovery_gate:recovery_gate.close()
            raise

    while True:
        stop.check()
        try:
            result=run_once(recovering)
        except RecoverableAgentFailure as error:
            schedule(error.classification,
                checkpoint_verified=error.checkpoint_verified)
            takeovers+=1
            ledger.append('agent_provider_takeover',takeover=takeovers,
                error_type=type(error.__cause__ or error).__name__,
                recovery='runner recover_iteration; no unknown tool call replay',
                conversation_restored=False)
            recovering=True
            continue
        if result.get('status')!='agent_error':
            if recovery_gate:recovery_gate.close()
            return result
        journal=load(journal_path)
        iterations=journal.get('iterations',[])
        if (not iterations or iterations[-1].get('agent',{}).get('ok') is not False
                or journal.get('status')!='agent_error'):
            raise RuntimeError('runner returned an unauthenticated agent_error checkpoint')
        # A watchdog pause may have caused the agent_error. Never reopen the
        # runner journal after the authoritative global clock has stopped.
        stop.check()
        agent=iterations[-1]['agent']
        if agent.get('num_turns',0)>0:
            first_failure_at=None;prior_failures=0
        classification=agent.get('provider_failure') or {
            'category':'unknown','failure':None,'evidence':'none'}
        schedule(classification,checkpoint_verified=True)
        takeovers+=1
        journal['status']='running'
        journal['formal_takeover_count']=int(journal.get('formal_takeover_count',0))+1
        save(journal_path,journal)
        ledger.append('agent_provider_takeover',takeover=takeovers,
            runner_iteration=iterations[-1].get('index'),
            recovery='evaluated runner checkpoint; new agent thread; no tool replay',
            conversation_restored=False)
        recovering=True


def create_fresh_plan(*, root, bench, config, Agent, stop, ledger, compile_plan,
                      clock=time.monotonic, sleep=time.sleep):
    planning=root/'planning-workspace';planning.mkdir()
    durable(planning/'benchmark.json',bench)
    (planning/'INSTRUCTION.md').write_text(
        'Plan the repository before generating any proofs. Read benchmark.json for the fixed contracts. '
        'Write PLAN.json with version=1, source_benchmark_id from benchmark.json, modules and execution_order. '
        'Each module has name (Vero_ prefix, excluding Vero_All/Vero_Support), contracts (IDs), imports (other generated modules), '
        'and a nonempty rationale. Assign every contract exactly once. Imports must precede their consumers in execution_order. '
        'Choose the division and order yourself. This is the only allocation pass: after acceptance, theories, contracts and imports are immutable. Do not generate proof bodies in this planning phase. '
        'Parent search is available through python3 /backend/tool.py search QUERY. Read PLAN-FEEDBACK.json if present.')
    attempt=0;first_failure_at=None;prior_failures=0
    while True:
        stop.check();attempt+=1
        agent=Agent({**config['agent'],'application_workspace':str(planning)},
            root/f'planning-{attempt:04d}'/'agent')
        try:result=agent.run(sandbox_dir=planning,
            instruction_file=planning/'INSTRUCTION.md')
        except RecoverableAgentFailure as error:
            result={'ok':False,'provider_failure':error.classification,
                'interrupted':True}
        durable(root/f'planning-{attempt:04d}'/'result.json',result)
        stop.check()
        if not result.get('ok'):
            classification=result.get('provider_failure') or {
                'category':'unknown','failure':None,'evidence':'none'}
            if result.get('num_turns',0)>0:
                first_failure_at=None;prior_failures=0
            now=clock()
            if first_failure_at is None:first_failure_at=now
            decision=vero_cli_recovery_decision(classification,prior_failures,
                now-first_failure_at,
                checkpoint_verified=_planning_checkpoint_verified(agent.log_dir))
            if not decision['retry']:
                ledger.append('planning_provider_recovery_paused',
                    category=classification['category'],
                    evidence=classification['evidence'],reason=decision['reason'],
                    recovery_window_seconds=RECOVERY_WINDOW_SECONDS,
                    consecutive_failures=prior_failures)
                raise ProviderRecoveryPause(decision['reason'])
            prior_failures=decision['consecutive_failures']
            ledger.append('planning_provider_recovery_scheduled',
                category=classification['category'],
                evidence=classification['evidence'],
                http_status=classification['failure']['http_status'],
                delay_seconds=decision['delay_seconds'],
                consecutive_failures=prior_failures,
                recovery_window_seconds=RECOVERY_WINDOW_SECONDS,
                incident_elapsed_seconds=now-first_failure_at,
                retry_policy=decision['retry_policy'],
                recovery='new CLI invocation over planning workspace; no tool replay')
            if hasattr(stop,'note_provider_failure'):stop.note_provider_failure()
            handle=stop.begin_exclusion('provider_recovery')
            try:
                deadline=clock()+decision['delay_seconds']
                while (remaining:=deadline-clock())>0:
                    stop.check();sleep(min(1,remaining))
            finally:stop.end_exclusion(handle)
            continue
        try:
            plan=_read_json(planning/'PLAN.json',limit=4*1024*1024)
            planned=compile_plan(bench,plan)
        except (ValueError,OSError,KeyError,TypeError) as error:
            durable(planning/'PLAN-FEEDBACK.json',{'error':str(error)})
            ledger.append('plan_rejected',error=str(error));continue
        return plan,planned


def formal_configuration_binding(args):
    """Validate and persist the immutable formal-experiment controls."""
    effective=getattr(args,'budget_mode','active-wall')=='effective-time'
    native_slots=getattr(args,'native_slots',1)
    if native_slots not in (1,2):
        raise ValueError('formal VERO native_slots must be 1 or 2')
    values={'model':args.model,'reasoning_effort':args.reasoning,
        'expected_contract_count':args.expected_contracts,
        'token_limit':None,
        **({'native_slots':native_slots} if native_slots!=1 else {}),
        **({'budget_mode':'effective-time',
            'effective_seconds_limit':args.effective_seconds} if effective else {
            'active_wall_seconds_limit':args.active_wall_seconds}),
        **shared_host_binding(getattr(args,'shared_host_lock_dir',None),
            getattr(args,'shared_host_slots',1),
            getattr(args,'shared_host_cleanup_timeout',30))}
    valid_budget=((effective and args.expected_contracts==FORMAL_CREFINE_CONTRACTS
            and args.effective_seconds==FORMAL_ACTIVE_WALL_SECONDS)
        or (not effective and args.expected_contracts==FORMAL_EXPECTED_CONTRACTS
            and args.active_wall_seconds==FORMAL_ACTIVE_WALL_SECONDS
            and getattr(args,'effective_seconds',None) is None))
    if (args.model!=FORMAL_MODEL or args.reasoning!=FORMAL_REASONING or not valid_budget):
        raise ValueError('formal VERO experiment requires gpt-5.6-sol/medium and either '
            '287 Refine contracts with 172800 active-wall seconds, or '
            '309 CRefine contracts with 172800 effective-generation seconds')
    if getattr(args,'token_limit',None) is not None:
        raise ValueError('legacy token-limit is unsupported: VERO now uses '
            'only the active-wall time limit')
    return values


def provider_auth_binding(args):
    """Freeze only paths and policy identifiers, never credential contents."""
    seed=Path(args.auth).absolute()
    state_base=(Path(args.auth_state_base).absolute()
        if getattr(args,'auth_state_base',None) else seed.parent/'.regen-vero-auth-state')
    lease_dir=(Path(args.auth_lease_dir).absolute()
        if getattr(args,'auth_lease_dir',None) else DEFAULT_AUTH_LEASE_DIR)
    return {'auth_seed_path_sha256':hashlib.sha256(str(seed.resolve()).encode()).hexdigest(),
        'auth_state_base':str(state_base),'auth_lease_dir':str(lease_dir),
        'auth_policy':'durable-private-home-v1',
        'provider_recovery_policy':'provider-recovery-v2',
        'provider_recovery_window_seconds':RECOVERY_WINDOW_SECONDS}


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
        'theory is sealed. When every contract and helper in a theory has passed the current '
        'native theory check, request seal immediately; if imports '
        'are not yet sealed, the harness records a deferred seal and you must finish those imports first. '
        'A contract proof body accepted GREEN by a native check is frozen during ordinary '
        'generation. Editing another lemma in the same theory does not erase that contract\'s '
        'independent GREEN evidence if its checked prefix and imports are unchanged. '
        'When all contracts and helper declarations in a theory have passed the exact '
        'native theory check and its imports are sealed, use '
        '`tool.py begin-seal THEORY` before editing any already-GREEN proof body as part '
        'of seal repair; `scope --phase seal` is only cost attribution and grants no edit '
        'authority. If abandoning seal repair, restore the accepted proof bodies and '
        'checked context, then call `tool.py abort-seal THEORY TRANSACTION_ID`. '
        'A failed seal may be repaired inside the same theory; recheck and '
        'seal it, and do not change the frozen plan.\n')


def effective_budget_instruction(seconds):
    return ('\nThe experiment has one shared-workspace effective-generation budget of '
        f'{seconds:g} seconds. It does not reset for a new GREEN contract or a theory switch. '
        'Planning, initialization, trusted native seal/build, integration, recorded pauses, '
        'and confirmed provider recovery do not consume this budget; ordinary proof '
        'generation, model reasoning, edits, helper work, retrieval, and native candidate '
        'checks do. Model-driven diagnosis or repair after a failed seal is generation. '
        'There is no token, retry, or no-progress stopping threshold. The accepted '
        'contract allocation, theory imports and declarations are frozen. All theories '
        'share one workspace. Only native evidence can publish GREEN; seal every complete '
        'theory and perform final repository integration.\n')


def interrupted_result(stop, error):
    """Stop a run segment without declaring an unfinished experiment complete."""
    already_paused=stop.event.is_set()
    if not already_paused:
        stop.cancel(error.reason if isinstance(error,ProviderRecoveryPause)
            else 'infrastructure_error')
    terminal=stop.reason==getattr(stop,'limit_reason',ACTIVE_WALL_LIMIT_REASON)
    classified_pause=isinstance(error,ProviderRecoveryPause)
    return {'status':'stopped' if terminal else ('paused' if already_paused or classified_pause
        else 'infrastructure_error'),
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


def canonical_contract_receipts(bench, artifact, checker, frozen_imports, destination):
    """Fingerprint checked prefixes in the full, unmasked VERO render."""
    from vero.isabelle.benchmark import render
    if not all(callable(getattr(checker,name,None)) for name in
            ('_contract_receipts','_seal_source_identity')):
        raise ValueError('checker cannot audit seal readiness')
    render(bench,destination,{
        key:slot['body'] for key,slot in artifact['slots'].items()
        if slot['found']})
    canonical={path.name:path.read_bytes()
        for path in sorted(Path(destination).glob('*.thy'))}
    effective=effective_generated_imports(canonical,frozen_imports,
        require_rendered=True)
    receipts={theory:checker._contract_receipts(theory,canonical,effective)
        for theory in effective}
    return canonical,effective,receipts


def audit_live_workspace_after_step(checker, observer, bench, frozen_imports,
                                    raw_snapshot, source, requested_theory):
    """Close a no-native-check gap against the live model-writable workspace.

    VERO may reject a draft before calling the checker at all. A concurrent
    edit during that evaluator or a progress response must still reconcile
    any previously accepted certificate against the *new* raw source.
    """
    assert_raw=getattr(checker,'_assert_workspace_snapshot_unchanged',None)
    if not callable(assert_raw):return
    prior=getattr(observer,'audit_workspace_snapshot',None)
    observer.audit_workspace_snapshot=raw_snapshot
    try:
        try:assert_raw(observer)
        except (OSError,ValueError):
            reconcile=getattr(checker,'_reconcile_changed_raw_workspace',None)
            if callable(reconcile):
                reconcile(observer,Path(source),requested_theory,bench,
                    frozen_imports,{},reason='source_changed_during_vero_step')
            raise
    finally:
        observer.audit_workspace_snapshot=prior


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

    def canonical_receipts(self, artifact, attempt):
        """Recreate the same complete source fingerprints used by native checks."""
        return canonical_contract_receipts(self.bench,artifact,self.checker,
            self.frozen_plan['imports'],attempt/'canonical')

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
        if cmd not in {'check','progress','begin-seal','abort-seal'}:raise ValueError('unknown command')
        if cmd in {'check','begin-seal','abort-seal'} and request.get('module') not in self.modules:
            raise ValueError('unknown module')
        if cmd in {'check','begin-seal'} and observer:observer.select_theory(request['module'])
        attempt=self.output/uuid4().hex
        snapshot(self.workspace,attempt/'snapshot',self.modules)
        # Reject changes to an accepted proof before any source-invalidating
        # observer hook can erase the receipt that makes the edit detectable.
        artifact=self.extract(self.bench,attempt/'snapshot')
        if observer and hasattr(type(observer),'guard_green_proofs'):
            observer.guard_green_proofs(artifact,snapshot_dir=attempt/'snapshot')
        # The VERO renderer materializes every planned theory even when the
        # agent deleted its source file. Observe the original workspace now,
        # before extraction can turn the missing proof into an open slot.
        invalid_observed=False
        if observer and hasattr(type(observer),'source_missing'):
            workspace_sources={p.name:p.read_bytes()
                for p in sorted((attempt/'snapshot').glob('*.thy'))}
            try:
                observed_imports=effective_generated_imports(
                    workspace_sources,self.frozen_plan['imports'])
            except (ValueError,UnicodeError) as error:
                # Extraction will report the malformed slot. Missing-source
                # revocation must still cover any possible additive consumer.
                observed_imports=conservative_missing_imports(
                    self.frozen_plan['imports'])
                invalid_hook=getattr(observer,'source_invalid',None)
                if callable(invalid_hook) and hasattr(type(observer),'source_invalid'):
                    invalid_theories,trusted_imports=attributable_import_failure(
                        workspace_sources,self.frozen_plan['imports'])
                    if trusted_imports is not None:
                        observed_imports=trusted_imports
                    invalid_hook(reason=type(error).__name__,
                        requested_theory=request.get('module') if cmd=='check' else None,
                        candidate_theories=observer.candidate_theories_for_snapshot(
                            attempt/'snapshot'),invalid_theories=invalid_theories,
                        imports=trusted_imports)
                    invalid_observed=True
            observer.source_missing(workspace_sources,observed_imports,
                request.get('module') if cmd=='check' else None,
                support_required=False)
            if artifact.get('file_errors') and not invalid_observed:
                observer.source_extraction_invalid(artifact['file_errors'],
                    observed_imports,snapshot_dir=attempt/'snapshot')
        durable(attempt/'artifact.json',artifact)
        if cmd=='begin-seal':
            if observer is None or not hasattr(type(observer),'begin_seal_transaction'):
                raise ValueError('seal transaction owner unavailable')
            if invalid_observed:
                raise ValueError('cannot begin seal with invalid generated imports')
            # Re-render every raw slot exactly as VERO's checker does. An
            # unchanged proof body can still have a changed helper, earlier
            # declaration, imported theory, or support context. Such a stale
            # contract must lose current GREEN before it can authorize seal.
            canonical,effective,receipts=self.canonical_receipts(artifact,attempt)
            audit_again=attempt/'precommit'
            snapshot(self.workspace,audit_again,self.modules)
            if any(((attempt/'snapshot'/(name+'.thy')).read_bytes()
                        if (attempt/'snapshot'/(name+'.thy')).exists() else None)
                    != ((audit_again/(name+'.thy')).read_bytes()
                        if (audit_again/(name+'.thy')).exists() else None)
                    for name in sorted(self.modules)):
                raise ValueError('raw workspace changed while beginning seal')
            observer.reconcile_contracts(request['module'],receipts)
            observer.reconcile_seals(request['module'],canonical,
                lambda theory,contents:self.checker._seal_source_identity(
                    theory,contents,effective),effective)
            missing=sorted(set(effective[request['module']])-observer.seals.keys())
            if missing:
                raise ValueError('seal edit requires sealed imported theories: '+', '.join(missing))
            # A GREEN contract receipt alone does not mean the whole theory is
            # ready for seal repair: a later helper may still be unfinished.
            # Admit edits only after a native successful check of this exact
            # canonical theory source, including its auxiliary declarations.
            theory=request['module']
            ids={c['id'] for c in self.bench['contracts'] if c['module']==theory}
            if not ids or not ids<=observer.accepted_receipts.keys():
                raise ValueError('seal edit requires all theory contracts GREEN')
            identity=self.checker._seal_source_identity(theory,canonical,effective)
            latest=next((row['data'] for row in reversed(observer.ledger.rows)
                if row['kind']=='proof_commit' and row['data'].get('theory')==theory
                and isinstance(row['data'].get('evidence'),dict)),None)
            checked_rows=latest.get('nodes',()) if latest else ()
            if (latest is None or latest.get('source_identity')!=identity
                    or latest['evidence'].get('returncode')!=0
                    or not ids<={row.get('uid') for row in checked_rows
                        if row.get('kind')=='contract' and row.get('status')=='green'}
                    or any(row.get('status')!='green' for row in checked_rows
                        if row.get('kind') in ('contract','helper'))):
                raise ValueError('seal edit requires exact native GREEN theory check, including helpers')
            transaction=observer.begin_seal_transaction(request['module'],artifact,
                snapshot_dir=attempt/'snapshot')
            result={'theory':request['module'],'seal_transaction_id':transaction,
                'status':'seal_edit_active'}
            durable(attempt/'response.json',result)
            return result
        if cmd=='abort-seal':
            if observer is None or not hasattr(type(observer),'abort_seal_transaction'):
                raise ValueError('seal transaction owner unavailable')
            transaction=request.get('transaction_id')
            if not isinstance(transaction,str) or not transaction:
                raise ValueError('seal transaction ID required')
            _,_,receipts=self.canonical_receipts(artifact,attempt)
            ids={c['id'] for c in self.bench['contracts']
                if c['module']==request['module']}
            if any(not isinstance(receipts[request['module']].get(uid),dict)
                    or observer._native_receipt(receipts[request['module']][uid])
                        != observer.accepted_receipts.get(uid) for uid in ids):
                raise ValueError('restore accepted GREEN proofs and checked context before abort')
            observer.abort_seal_transaction(request['module'],transaction,artifact,
                snapshot_dir=attempt/'snapshot')
            result={'theory':request['module'],'status':'seal_edit_abandoned'}
            durable(attempt/'response.json',result)
            return result
        if cmd=='progress':
            from vero.isabelle.checker import filled
            from vero.isabelle.slots import context
            if (observer and observer.accepted_receipts and not invalid_observed
                    and not artifact.get('file_errors')):
                prior_snapshot=getattr(observer,'audit_workspace_snapshot',None)
                observer.audit_workspace_snapshot=attempt/'snapshot'
                try:
                    canonical,effective,receipts=self.canonical_receipts(artifact,attempt)
                    audit_live_workspace_after_step(self.checker,observer,self.bench,
                        self.frozen_plan['imports'],attempt/'snapshot',attempt,
                        next(iter(sorted(self.modules))))
                    observer.reconcile_contracts(None,receipts)
                    if observer.seals:
                        observer.reconcile_seals(next(iter(effective)),canonical,
                            lambda theory,contents:self.checker._seal_source_identity(
                                theory,contents,effective),effective)
                finally:
                    observer.audit_workspace_snapshot=prior_snapshot
            try:
                imports_error=None
                try:
                    context(self.bench,{k:s['body'] for k,s in artifact['slots'].items() if s['found']})
                except (ValueError,KeyError) as error:
                    imports_error=str(error)
                    invalid_hook=getattr(observer,'source_invalid',None) if observer else None
                    if (not invalid_observed and callable(invalid_hook)
                            and hasattr(type(observer),'source_invalid')):
                        invalid_hook(reason=type(error).__name__,requested_theory=None,
                            candidate_theories=observer.candidate_theories_for_snapshot(
                                attempt/'snapshot'))
                result={'filled_slots':sum(s['found'] and filled(s['body'])
                    for k,s in artifact['slots'].items()
                    if k.endswith((':prove',':disprove'))),
                    'file_errors':artifact['file_errors'],
                    'authority':'filled is not proved; use check and final evaluation'}
                if imports_error is not None:result['imports_error']=imports_error
                return result
            finally:
                if observer:
                    audit_live_workspace_after_step(self.checker,observer,self.bench,
                        self.frozen_plan['imports'],attempt/'snapshot',attempt,
                        next(iter(sorted(self.modules))))
        # Same VERO hygiene/render/audit evaluator. Scope is the requested module
        # and generated imports, not unrelated unfinished sibling proofs.
        from vero.isabelle.slots import context,schedule
        from vero.isabelle.planning import dependencies
        from vero.isabelle.checker import evaluate
        try:
            effective=context(self.bench,{k:s['body'] for k,s in artifact['slots'].items() if s['found']})
        except (ValueError,KeyError) as error:
            invalid_hook=getattr(observer,'source_invalid',None) if observer else None
            if (not invalid_observed and callable(invalid_hook)
                    and hasattr(type(observer),'source_invalid')):
                invalid_hook(reason=type(error).__name__,
                    requested_theory=request['module'],
                    candidate_theories=observer.candidate_theories_for_snapshot(
                        attempt/'snapshot'))
            raise
        closure=dependencies(effective,request['module'])|{request['module']}
        for key,(module,kind) in schedule(self.bench).items():
            if module not in closure:
                artifact['slots'][key]={'found':False,'body':''}
        artifact['file_errors']={m:e for m,e in artifact['file_errors'].items() if m in closure}
        if observer:
            prior_snapshot=getattr(observer,'audit_workspace_snapshot',None)
            observer.audit_workspace_snapshot=attempt/'snapshot'
        try:
            result=evaluate(self.bench,artifact,attempt/'evaluation',self.checker)
            if observer:observer.evaluated(result,closure)
        finally:
            if observer:
                try:
                    audit_live_workspace_after_step(self.checker,observer,self.bench,
                        self.frozen_plan['imports'],attempt/'snapshot',attempt,
                        request['module'])
                finally:
                    observer.audit_workspace_snapshot=prior_snapshot
        # Include actual checker diagnostics rather than returning status alone.
        result['diagnostics']={m:(attempt/'evaluation'/(m+'.log')).read_text()
            for m in result['modules']}
        durable(attempt/'response.json',result)
        if observer:
            audit_live_workspace_after_step(self.checker,observer,self.bench,
                self.frozen_plan['imports'],attempt/'snapshot',attempt,
                request['module'])
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
        class BorrowedChecker:
            """The runner may close its lease; the host owns the real checker."""
            def __init__(self,native):self.native=native
            def __getattr__(self,name):return getattr(self.native,name)
            def close(self):pass
        runner.Checker=lambda config:BorrowedChecker(checker)
        runner.CodexAgent=agent_class
        observer=getattr(checker,'observer',None)
        if observer and old_evaluate:
            def evaluate(*args,**kwargs):
                # The original runner performs its own turn-end and recovery
                # evaluations without visiting Service.dispatch. Observe the
                # raw workspace first: extraction can replace a deleted file
                # with an open rendered slot, and then no native check reaches
                # the adapter's source reconciliation.
                frozen=(getattr(checker,'frozen_plan',None) or {}).get('imports',{})
                if not frozen:
                    result=old_evaluate(*args,**kwargs)
                    observer.evaluated(result)
                    return result
                from vero.isabelle.benchmark import extract
                with tempfile.TemporaryDirectory(prefix='vero-runner-audit-') as tmp:
                    raw_snapshot=Path(tmp)/'snapshot'
                    snapshot(observer.workspace,raw_snapshot,frozen)
                    artifact=extract(observer.bench,raw_snapshot)
                    observer.guard_green_proofs(artifact,snapshot_dir=raw_snapshot)
                    raw={path.name:path.read_bytes()
                        for path in sorted(raw_snapshot.glob('*.thy'))}
                    invalid_reason=None
                    try:imports=effective_generated_imports(raw,frozen)
                    except (ValueError,UnicodeError) as error:
                        invalid_reason=type(error).__name__
                        imports=conservative_missing_imports(frozen)
                    if invalid_reason:
                        invalid_theories,trusted_imports=attributable_import_failure(
                            raw,frozen)
                        if trusted_imports is not None:
                            imports=trusted_imports
                        observer.source_invalid(reason=invalid_reason,requested_theory=None,
                            candidate_theories=observer.candidate_theories_for_snapshot(
                                raw_snapshot),invalid_theories=invalid_theories,
                            imports=trusted_imports)
                    observer.source_missing(raw,imports,support_required=False)
                    if artifact.get('file_errors') and not invalid_reason:
                        observer.source_extraction_invalid(artifact['file_errors'],
                            imports,snapshot_dir=raw_snapshot)
                    if (observer.accepted_receipts and not invalid_reason
                            and not artifact.get('file_errors')):
                        canonical,effective,receipts=canonical_contract_receipts(observer.bench,
                            artifact,checker,frozen,Path(tmp)/'canonical')
                        previous=getattr(observer,'audit_workspace_snapshot',None)
                        observer.audit_workspace_snapshot=raw_snapshot
                        try:
                            audit_live_workspace_after_step(checker,observer,
                                observer.bench,frozen,raw_snapshot,Path(tmp),
                                next(iter(sorted(frozen))))
                            observer.reconcile_contracts(None,receipts)
                            if observer.seals:
                                observer.reconcile_seals(next(iter(effective)),canonical,
                                    lambda theory,contents:checker._seal_source_identity(
                                        theory,contents,effective),effective)
                        finally:
                            observer.audit_workspace_snapshot=previous
                    prior_snapshot=getattr(observer,'audit_workspace_snapshot',None)
                    observer.audit_workspace_snapshot=raw_snapshot
                    try:
                        result=old_evaluate(*args,**kwargs)
                        observer.evaluated(result,preflight_source_invalidated=bool(
                            invalid_reason or artifact.get('file_errors')))
                    finally:
                        try:
                            audit_live_workspace_after_step(checker,observer,
                                observer.bench,frozen,raw_snapshot,Path(tmp),
                                next(iter(sorted(frozen))))
                        finally:
                            observer.audit_workspace_snapshot=prior_snapshot
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
    checker_context=load_context(Path(args.dataset))
    expected_session=('CRefine' if getattr(args,'budget_mode','active-wall')=='effective-time'
        else 'Refine')
    if checker_context['target_session']!=expected_session:
        raise ValueError('VERO benchmark profile and checker target session disagree')
    if bench['base_import']!=checker_context['base_import']:
        raise ValueError('VERO benchmark and checker semantic base disagree')
    checker_context_id=checker_context['checker_context_id']
    base_binding={'entry':'original-vero-runner-common-application-v2',
        'benchmark_id':bench['benchmark_id'],'dataset':str(Path(args.dataset).resolve()),
        'checker_context_id':checker_context_id,
        'input_benchmark_sha256':digest(bench),**formal_binding,
        **provider_auth_binding(args),
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
        'activity_policy':(EFFECTIVE_BUDGET_POLICY if
            getattr(args,'budget_mode','active-wall')=='effective-time'
            else ACTIVE_WALL_BUDGET_POLICY),
        **inventory_binding}
    plan=planned=config=plan_binding=None
    if resume:
        try:plan,planned,config,plan_binding=validate_resume_artifacts(root,base_binding,
            seal_migration=bool(getattr(args,'seal_dependency_migration',False)),
            checker_lifecycle_migration=bool(getattr(args,'checker_lifecycle_migration',False)),
            resource_pool_migration=bool(getattr(args,'resource_pool_migration',False)),
            provider_auth_migration=bool(getattr(args,'provider_auth_migration',False)))
        except Exception:
            formal_lock.close()
            raise
    try:
        sources=[args.auth]
        auth_migration=bool(getattr(args,'provider_auth_migration',False))
        migrated_auth=existing_vero_auth_source(root,args.auth,
            state_base=base_binding['auth_state_base']) if auth_migration else None
        migrated_auth_exists=bool(migrated_auth and migrated_auth.is_file())
        if resume and (not auth_migration or migrated_auth_exists):
            sources.append(existing_vero_auth_source(root,args.auth,
                state_base=base_binding['auth_state_base']))
        with provider_auth_lease(root,*sources,
                lease_dir=base_binding['auth_lease_dir']):
            auth_state=prepare_vero_auth_state(root,args.auth,
                resume=resume and (not auth_migration or migrated_auth_exists),
                state_base=base_binding['auth_state_base'])
            if not resume:durable(root/'binding.json',base_binding)
            return _run_segment(args,root=root,bench=bench,planned=planned,
                plan=plan,config=config,plan_binding=plan_binding,
                base_binding=base_binding,resume=resume,formal_lock=formal_lock,
                workspace=workspace,client=client,runner=runner,read=read,save=save,
                auth_state=auth_state)
    except BaseException:
        if not formal_lock.closed:formal_lock.close()
        if not resume and not (root/'binding.json').exists():
            # This output was created by this invocation and no experiment
            # record exists. Never remove a directory with unexpected files.
            if {p.name for p in root.iterdir()}=={'.formal-wrapper.lock'}:
                (root/'.formal-wrapper.lock').unlink()
                root.rmdir()
        raise
    finally:
        if not formal_lock.closed:formal_lock.close()


def _run_segment(args, *, root,bench,planned,plan,config,plan_binding,
                 base_binding,resume,formal_lock,workspace,client,runner,read,save,
                 auth_state):
    """Run only while the output and host-wide provider auth leases are held."""
    ledger=Ledger(root,root.name)
    effective=getattr(args,'budget_mode','active-wall')=='effective-time'
    if resume:
        try:validate_resume_ledger(ledger,effective=effective)
        except Exception:
            formal_lock.close()
            raise
        if getattr(args,'resource_pool_migration',False):
            try:
                old_binding=_read_json(root/'binding.json')
                resource_event=record_resource_pool_migration(ledger,old_binding,base_binding)
                if getattr(args,'provider_auth_migration',False):
                    auth_event=record_provider_auth_migration(ledger,old_binding,base_binding,
                        resource_event)
                    if getattr(args,'runner_reopen_migration',False):
                        record_runner_reopen_migration(ledger,old_binding,base_binding,
                            auth_event)
                config={**config,'checker':expected_runtime_config(
                    root,base_binding,plan_binding)['checker'],
                    'provider':expected_runtime_config(root,base_binding,
                        plan_binding)['provider']}
            except Exception:
                formal_lock.close()
                raise
        if getattr(args,'seal_dependency_migration',False):
            try:record_seal_migration(ledger)
            except Exception:
                formal_lock.close()
                raise
        if getattr(args,'checker_lifecycle_migration',False):
            try:record_checker_lifecycle_migration(ledger)
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
        budget_class=TerminalEffectiveTimeBudget if effective else ActiveWallBudget
        budget_seconds=args.effective_seconds if effective else args.active_wall_seconds
        stop=budget_class(ledger,seconds=budget_seconds,
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
        auth_home=auth_state.home
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
        with VeroCheckerAdapter(args.dataset,native_segment,timeout=args.check_timeout,
                observer=observer,
                native_slots=base_binding.get('native_slots',1),
                shared_host_lock_dir=base_binding.get('shared_host_lock_dir'),
                shared_host_slots=base_binding.get('shared_host_slots',1),
                shared_host_cleanup_timeout=base_binding.get('shared_host_cleanup_timeout',30)) as checker:
            service=Service(socket_path,bench,workspace,args.dataset,root/'requests',checker)
            service.plan_ready=False
            thread=threading.Thread(target=service.serve_forever,daemon=True);thread.start()
            stop.start(sample=observer.drafts)
            original_agent=runner.CodexAgent
            recovery_gate=ProviderRecoveryGate(stop,ledger)
            agent_invocations=0
            class Agent(original_agent):
                def __init__(self,config,log_dir):
                    nonlocal agent_invocations
                    if agent_invocations:rotate_vero_auth_state(auth_state)
                    agent_invocations+=1
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
                        stream.write((effective_budget_instruction if effective else
                            active_wall_budget_instruction)(stop.snapshot()['seconds']))
                    previous=kwargs.pop('monitor',None)
                    def monitor():
                        observer.usage_events(self.log_dir/'agent_events.jsonl',self.log_dir.parent.name)
                        stop.check()
                        return previous() if previous else None
                    try:
                        if service.frozen_plan is not None:recovery_gate.generation_started()
                        result=super().run(**kwargs,monitor=monitor)
                        if not result.get('ok'):
                            result['provider_failure']=classify_agent_invocation(self.log_dir)
                        return result
                    except (OSError,subprocess.SubprocessError) as error:
                        raise RecoverableAgentFailure('provider/agent process failed',
                            classification=classify_agent_invocation(self.log_dir),
                            checkpoint_verified=_agent_checkpoint_verified(
                                self.log_dir)) from error
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
                            recovery_gate=recovery_gate,
                            reopen_agent_error=bool(getattr(args,'runner_reopen_migration',False)))
                finally:
                    checker.check=original_check
                    recovery_gate.close()
                if result.get('status')=='complete':stop.finish()
                else:
                    stop.cancel('runner_returned_without_completion')
                    terminal=stop.reason==stop.limit_reason
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
                        'budget_clock':('effective generation excludes trusted preparation, seal and integration'
                            if effective else 'active online wall clock; all normal phases count'),
                        'excluded_time':'explicit pause, offline process time, or confirmed provider interruption/recovery only',
                        'budget_stop':('terminal stop at 172800 cumulative effective-generation seconds'
                            if effective else 'terminal stop at 172800 cumulative active-wall seconds'),
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
        choices=[FORMAL_EXPECTED_CONTRACTS,FORMAL_CREFINE_CONTRACTS])
    parser.add_argument('--budget-mode',choices=['active-wall','effective-time'],
        default='active-wall')
    parser.add_argument('--active-wall-seconds',type=int,
        default=FORMAL_ACTIVE_WALL_SECONDS,choices=[FORMAL_ACTIVE_WALL_SECONDS],
        help='immutable cumulative online wall-clock limit (fixed at 48 hours)')
    parser.add_argument('--effective-seconds',type=int,
        choices=[FORMAL_ACTIVE_WALL_SECONDS],
        help='immutable cumulative effective-generation limit (fixed at 48 hours)')
    parser.add_argument('--check-timeout',type=int,default=900)
    parser.add_argument('--native-slots',type=int,default=1,choices=[1,2],
        help='frozen per-run native capacity; the serial VERO runner still issues one check at a time')
    parser.add_argument('--auth-state-base',
        help='Private durable VERO auth directory outside output (same path on resume)')
    parser.add_argument('--auth-lease-dir',
        help='Host-shared private local lock directory for provider auth chains')
    parser.add_argument('--shared-host-lock-dir',
        help='Opt in to one shared native slot directory mounted by every experiment')
    parser.add_argument('--shared-host-slots',type=int,default=1)
    parser.add_argument('--shared-host-cleanup-timeout',type=float,default=30)
    parser.add_argument('--resume',action='store_true',
        help='explicitly continue the same frozen output with a new model thread')
    parser.add_argument('--seal-dependency-migration',action='store_true',
        help='audit and apply the bounded import-seal correction to the original paused run')
    parser.add_argument('--checker-lifecycle-migration',action='store_true',
        help='audit and repair host-owned checker lifetime across model transport takeover')
    parser.add_argument('--resource-pool-migration',action='store_true',
        help='explicit audited migration of the paused CRefine run from two local native slots to one')
    parser.add_argument('--provider-auth-migration',action='store_true',
        help='explicit audited migration to the separate VERO CRefine login chain')
    parser.add_argument('--runner-reopen-migration',action='store_true',
        help='explicitly reopen the verified paused agent_error as a new model turn')
    args=parser.parse_args()
    if args.seal_dependency_migration and not args.resume:
        parser.error('--seal-dependency-migration requires --resume')
    if args.checker_lifecycle_migration and not args.resume:
        parser.error('--checker-lifecycle-migration requires --resume')
    if args.resource_pool_migration and not args.resume:
        parser.error('--resource-pool-migration requires --resume')
    if args.provider_auth_migration and not (args.resume and args.resource_pool_migration):
        parser.error('--provider-auth-migration requires --resume and --resource-pool-migration')
    if args.runner_reopen_migration and not (args.resume and args.provider_auth_migration):
        parser.error('--runner-reopen-migration requires --resume and --provider-auth-migration')
    if min(args.expected_contracts,args.active_wall_seconds,args.check_timeout)<=0:
        parser.error('budgets and expected contract count must be positive')
    try:shared_host_binding(args.shared_host_lock_dir,args.shared_host_slots,args.shared_host_cleanup_timeout)
    except ValueError as error:parser.error(str(error))
    result=run(args)
    print(json.dumps({'status':result['status'],'iterations':len(result.get('iterations',[]))}))
    sys.exit(result['status'] not in {'complete','stopped'})


if __name__=='__main__':main()
