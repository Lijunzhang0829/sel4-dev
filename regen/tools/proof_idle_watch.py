"""Pause a bound experiment after a configured interval without accepted proofs.

Reads only the proof projection of immutable Merkle commits, not embedded
historical evidence. No model requests or proof acceptance are performed here.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import fcntl
import hashlib
import json
from pathlib import Path
import time

from regen.execution.proof_commits import durable
from regen.infrastructure.incremental_log import ObjectStore, FORMAT, find_root, read_artifact
from regen.tools import parallel_workspace_service as service
from regen.tools import theory_effective_clock as effective_clock


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def projection(path):
    """Decode event/identity/proofs only; verify each visited object hash."""
    value = read(path)
    if '$log_format' not in value:
        return {k:value[k] for k in ('event','commit_id','sequence')}, value['state'].get('proofs', {})
    if value['$log_format'] != FORMAT:
        raise ValueError('unknown commit log format')
    root = find_root(path)
    if root is None:
        raise ValueError('commit has no object store')
    store = ObjectStore(root)
    def resolve(node):
        seen = set()
        while node[0] == 'ref':
            if node[1] in seen:
                raise ValueError('cyclic projection')
            seen.add(node[1]); node = store.node(node[1])[0]
        return node
    def field(node, key):
        node = resolve(node)
        if node[0] != 'dict': raise ValueError('expected commit object')
        return node[1][key]
    node = ['ref',value['root']]
    meta = {k:resolve(field(node,k))[1] for k in ('event','commit_id','sequence')}
    proofs = resolve(field(field(node,'state'),'proofs'))
    if proofs[0] != 'dict':raise ValueError('invalid proof map')
    result = {}
    for name, encoded in proofs[1].items():
        decoded = resolve(encoded)
        if decoded[0] != 'value' or not isinstance(decoded[1],str):
            raise ValueError('accepted proof is not text')
        result[name] = decoded[1]
    return meta, result


def scan(run_dirs, state):
    """Advance persistent cursors; inherited/repeated proofs earn no new time."""
    for root in map(Path,run_dirs):
        for commits in sorted(root.glob('theories/*/acceptance/commits')):
            theory = commits.parent.parent.name
            cursor = state['cursors'].setdefault(str(commits), {'sequence':0})
            if cursor['sequence']:
                last = commits/f"{cursor['sequence']:06d}.json"
                if sha(last) != cursor['sha256']:
                    raise ValueError('observed commit changed or disappeared')
            for path in sorted(commits.glob('*.json')):
                if int(path.stem) <= cursor['sequence']:continue
                meta, proofs = projection(path)
                if meta['sequence'] != cursor['sequence']+1:
                    raise ValueError('gap in proof commit sequence')
                changed = []
                for name, body in proofs.items():
                    if state.get('progress_scope') in {'contract', 'new_contract'} and not name.startswith('contract:'):
                        continue
                    if not isinstance(body,str) or not body.strip():
                        raise ValueError('invalid accepted proof body')
                    fingerprint = hashlib.sha256(body.encode()).hexdigest()
                    # Contract UIDs are experiment-global. Moving an inherited
                    # UID to another theory is not a newly proved contract.
                    key = name if state.get('progress_scope') == 'new_contract' else theory+'/'+name
                    seen = state['seen'].setdefault(key,[])
                    if fingerprint not in seen:
                        first = not seen
                        seen.append(fingerprint)
                        if state.get('progress_scope') != 'new_contract' or first:
                            changed.append(name)
                # Initial continuation/sealed reuse copies do not prove anything.
                if changed and meta['event'] in {'green','batch','document'}:
                    timestamp = path.stat().st_mtime
                    if state.get('clock_mode') == effective_clock.MODE:
                        effective_clock.clock_state(state)['progress'].append({
                            'at': timestamp, 'theory': theory, 'run_dir': str(root),
                            'commit_id': meta['commit_id'], 'proof_ids': changed})
                    if timestamp > state['last_progress_at']:
                        state.update(last_progress_at=timestamp,
                            last_progress={'run_dir':str(root),'theory':theory,
                                'commit_id':meta['commit_id'],'commit_path':str(path),
                                'proof_ids':changed,'time_source':'immutable commit publication mtime'})
                cursor.update(sequence=meta['sequence'],commit_id=meta['commit_id'],sha256=sha(path))
    return state


def expired(state, now, timeout):
    if state.get('clock_mode') == effective_clock.MODE:
        return bool(effective_clock.clock_state(state).get('snapshot', {}).get('expired_theories'))
    return now-max(state['last_progress_at'], state.get('continuous_started_at', 0)) >= timeout


def refresh_effective(config, state, observations):
    # Sample the comparison time after disk reads, not before a potentially
    # long first tail. Re-scan commits published while reading phase evidence.
    effective_clock.ingest(config['run_dirs'], state)
    scan(config['run_dirs'], state)
    return effective_clock.evaluate(state, observations, time.time(), config['timeout_seconds'])


def services(config):
    rows = {}
    for name in config['run_dirs']:
        root = Path(name)
        if not (root/'service-launch.json').exists():continue
        receipt = read(root/'service-launch.json')
        if name == config['run_dirs'][0]:
            if sha(root/'service-launch.json') != config['source_launch_sha256']:
                raise ValueError('source service receipt changed')
        else:
            origin = read(root/'workspace-continuation.json')
            if origin['source_run'] != config['run_dirs'][0]:
                raise ValueError('destination belongs to another experiment')
        if receipt['run_dir'] != name:raise ValueError('service target mismatch')
        row = service.status(root)
        if not row['identity_verified'] and row['status'] not in {'not_loaded', 'previous_boot'}:
            raise RuntimeError('service identity cannot be verified: '+name)
        rows[name] = row
    return rows


def report(config, state, phase, **extra):
    now = time.time()
    value = {'phase':phase,'observed_at':datetime.now(timezone.utc).isoformat(),
             'timeout_seconds':config['timeout_seconds'],
             'last_progress_at':datetime.fromtimestamp(state['last_progress_at'],timezone.utc).isoformat(),
             'idle_seconds':round(now-max(state['last_progress_at'],state.get('continuous_started_at',0)),3),
             'progress_scope':state.get('progress_scope','all'),
             'continuous_started_at':state.get('continuous_started_at'),
             'last_progress':state.get('last_progress'),
             'unique_proof_nodes_seen':len(state['seen']), **extra}
    if state.get('clock_mode') == effective_clock.MODE:
        snapshot = effective_clock.clock_state(state).get('snapshot', {})
        value.update(clock_mode=effective_clock.MODE, effective_clock=snapshot,
                     idle_seconds=max((row['idle_effective_seconds'] for row in snapshot.get('theories', {}).values()), default=0),
                     idle_seconds_scope='maximum theory-local effective idle; not global wall')
    durable(Path(config['status_path']),value)
    durable(Path(config['state_path']),state)
    print(json.dumps(value,ensure_ascii=False),flush=True)
    return value


def pause(config, state, reason='no_accepted_proof_progress'):
    marker = Path(config['pause_marker'])
    with marker.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock.fileno(),fcntl.LOCK_EX)
        if not marker.exists():
            durable(marker,{'reason':reason,'requested_at':time.time(),
                'timeout_seconds':config['timeout_seconds'],
                'last_progress_at':state['last_progress_at'],
                'automatic_resume_allowed':False,
                **({'effective_clock': effective_clock.clock_state(state).get('snapshot')}
                   if state.get('clock_mode') == effective_clock.MODE else {})},immutable=True)
    # Do not kill a continuation fork mid-copy. The updated handoff observes
    # the marker at wait/fork/launch boundaries; the shared launch lock closes
    # the last check/start race. Stop proof services and recheck until it exits.
    while True:
        observations = services(config)
        errors = []
        for name,row in observations.items():
            if row['running']:
                try: service.stop(Path(name))
                except Exception as error:errors.append(str(error))
        unit = service._show(config['handoff_unit'])
        remaining = services(config)
        if not int(unit.get('MainPID','0')) and not any(r['running'] for r in remaining.values()) and not errors:
            break
        report(config,state,'pausing',errors=errors,handoff_running=bool(int(unit.get('MainPID','0'))))
        time.sleep(config['poll_seconds'])
    scan(config['run_dirs'],state)
    if state.get('clock_mode') == effective_clock.MODE:
        try:
            refresh_effective(config, state, remaining)
        except effective_clock.ClockEvidenceError as error:
            # A hard-killed worker may have lost its final lifecycle event.
            # Preserve the last measured state and record the ambiguity; never
            # invent downtime or silently reset a consumed budget at resume.
            effective_clock.clock_state(state)['reconciliation_required'] = str(error)
    snapshots = {}
    from regen.execution.proof_session_runner import acquire_run_mutation_lock, release_run_mutation_lock
    for name in config['run_dirs']:
        root=Path(name)
        if not (root/'run.json').exists():continue
        token=acquire_run_mutation_lock(root,operation='proof-idle-pause-snapshot')
        try:
            tasks=Counter()
            for p in (root/'check-tasks').glob('*/task.json'):
                tasks[read_artifact(p).get('status','unknown')]+=1
            snapshots[name]={'run_manifest_sha256':sha(root/'run.json'),
                'journal_tips':{k:v for k,v in state['cursors'].items() if Path(k).is_relative_to(root)},
                'progress':read(root/'session-progress-ledger.json') if (root/'session-progress-ledger.json').exists() else None,
                'check_task_states':dict(tasks)}
        finally:release_run_mutation_lock(token)
    durable(Path(config['checkpoint_path']),{'status':'paused','reason':reason,
        'pause_marker':read(marker),'snapshots':snapshots,
        **({'effective_clock': effective_clock.clock_state(state)}
           if state.get('clock_mode') == effective_clock.MODE else {}),
        'policy':'all original journals, requests, costs and proof bodies retained in place; no rollback or deletion'},immutable=True)
    report(config,state,'paused',checkpoint=config['checkpoint_path'],automatic_resume_allowed=False)


def emergency_stop_bound_services(config):
    """Stop verified experiment processes even when diagnostic storage fails.

    This deliberately performs no broad kill, service restart or fake checkpoint.
    Handoff safety still requires its marker; marker failure is a surfaced error.
    """
    errors = []
    try:
        observed = services(config)
    except Exception as error:
        return ['cannot verify bound services: '+str(error)]
    for name, row in observed.items():
        if row['running']:
            try:
                service.stop(Path(name))
            except Exception as error:
                errors.append(name+': '+str(error))
    return errors


def run(config):
    validate_policy(config)
    with Path(config['state_path']).with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock.fileno(),fcntl.LOCK_EX | fcntl.LOCK_NB)
        state_path=Path(config['state_path'])
        if state_path.exists():state=read(state_path)
        else:
            launch=read(Path(config['run_dirs'][0])/'service-launch.json')
            state={'cursors':{},'seen':{},'last_progress_at':datetime.fromisoformat(launch['created_at']).timestamp()}
            state['progress_scope']=config.get('progress_scope','all')
            state['continuous_started_at']=config.get('continuous_started_at',0)
            state['clock_mode']=config.get('clock_mode','wall')
        if state.get('progress_scope','all') != config.get('progress_scope','all'):
            raise ValueError('progress scope changed; use a fresh audited observer state')
        if state.get('clock_mode','wall') != config.get('clock_mode','wall'):
            raise ValueError('clock mode changed; use a fresh audited observer state')
        try:
            while True:
                scan(config['run_dirs'],state)
                if Path(config['pause_marker']).exists():
                    return pause(config,state)
                observed=services(config)
                active=any(r['running'] for r in observed.values())
                if state.get('clock_mode') == effective_clock.MODE:
                    refresh_effective(config, state, observed)
                if active and not state.get('continuous_started_at') and config.get('start_clock_on_launch'):
                    state['continuous_started_at']=min(
                        datetime.fromisoformat(read(Path(k)/'service-launch.json')['created_at']).timestamp()
                        for k,v in observed.items() if v['running'])
                if active and expired(state,time.time(),config['timeout_seconds']):
                    # Re-read the append-only journals once at the decision edge.
                    scan(config['run_dirs'],state)
                    if state.get('clock_mode') == effective_clock.MODE:
                        observed=services(config)
                        refresh_effective(config, state, observed)
                    if expired(state,time.time(),config['timeout_seconds']):
                        return pause(config,state, reason=('theory_no_new_contract_effective_timeout'
                            if state.get('clock_mode') == effective_clock.MODE else 'no_accepted_proof_progress'))
                report(config,state,'watching' if active else 'waiting_for_run',services={k:v['status'] for k,v in observed.items()})
                time.sleep(config['poll_seconds'])
        except Exception as error:
            if state.get('clock_mode') == effective_clock.MODE:
                # Evidence errors must not leave an unmonitored experiment
                # running. The normal pause/checkpoint protocol is fail-closed.
                try:
                    report(config,state,'monitor_error',error_type=type(error).__name__,reason=str(error))
                except Exception as diagnostic_error:
                    error.add_note('diagnostic persistence failed: '+str(diagnostic_error))
                try:
                    return pause(config,state,reason='effective_clock_evidence_error')
                except Exception as pause_error:
                    failures = emergency_stop_bound_services(config)
                    pause_error.add_note('pause/checkpoint failed; emergency bound-service stop attempted; '
                                         'no saved-checkpoint guarantee; errors='+repr(failures))
                    raise
            report(config,state,'monitor_error',error_type=type(error).__name__,reason=str(error))
            raise


def validate_policy(config):
    if config['timeout_seconds'] not in {1800,3600,7200} or not 1 <= config['poll_seconds'] <= 30:
        raise ValueError('invalid proof inactivity policy')
    if config.get('clock_mode', 'wall') not in {'wall', effective_clock.MODE}:
        raise ValueError('unknown proof inactivity clock')
    if config.get('clock_mode') == effective_clock.MODE and (
            config.get('progress_scope') != 'new_contract' or config['timeout_seconds'] != 3600):
        raise ValueError('theory effective policy requires new_contract and 3600 seconds')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--config-sha256',required=True)
    args=parser.parse_args()
    if sha(args.config)!=args.config_sha256:raise ValueError('watchdog configuration changed')
    config=read(args.config)
    run(config)


if __name__=='__main__':main()
